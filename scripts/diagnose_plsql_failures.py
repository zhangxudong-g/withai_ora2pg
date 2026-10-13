#!/usr/bin/env python3
"""
diagnose_plsql_failures.py - 诊断 PL/SQL 转换产物的 PG 加载失败

对 out/plsql/*.pgsql 中的每个产物:
  - 加载到临时 PG 库 (gkb_diag)
  - 记录 PG 错误 (如未知类型 / 语法错误 / 未知变量)
  - 生成 out/plsql/REPORTS/<对象名>.md 报告 (基于 conversion_report.py)

用法:
  python scripts/diagnose_plsql_failures.py

原理: 转换失败时, 通用引擎不静默生成残废代码, 而是生成详尽报告驱动后续
引擎增强. 此脚本是 verify 阶段的辅助诊断工具, 自动生成 out/plsql/REPORTS/.

与 verify_all_equivalence.py 的区别: 后者只标记 FAIL, 此脚本生成 Markdown 报告
含错误消息、根因分析、源 SQL 摘录、相关 DDL、建议手动操作.
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path
from typing import Optional

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "convert"))
from conversion_report import record_failure  # noqa: E402

import psycopg2  # noqa: E402

PG_HOST = "localhost"
PG_PORT = 5432
PG_USER = "postgres"
PG_PASSWORD = "postgres"
DIAG_DB = "gkb_plsql_diag"
PLSQL_DIR = ROOT / "out" / "plsql"
DDL_DIR = ROOT / "out" / "ddl"


def connect(db: str):
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASSWORD,
        dbname=db, connect_timeout=10
    )


def ensure_db():
    """确保诊断库存在"""
    conn = connect("postgres")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DIAG_DB,))
    if cur.fetchone() is None:
        cur.execute(f"CREATE DATABASE {DIAG_DB} TEMPLATE template0 ENCODING 'UTF8' "
                    f"LC_COLLATE 'C' LC_CTYPE 'C'")
    conn.close()


def reset_schema(conn):
    """清空 schema 并加载所有 DDL"""
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO postgres;")
    for f in sorted(DDL_DIR.glob("*.pgsql")):
        try:
            cur.execute(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [DDL-FAIL] {f.name}: {type(e).__name__}: {str(e)[:80]}")


def categorize_error(msg: str) -> tuple[str, str]:
    """根据 PG 错误消息推断失败类别 (FailureCategory) 与 根因."""
    msg_low = msg.lower()
    # 类型不存在 (中文「类型 "x" 不存在」 / 英文 "type \"x\" does not exist")
    if ("不存在" in msg and "类型" in msg) or ("does not exist" in msg_low and "type" in msg_low):
        m = re.search(r'["\u201c\u300c]([^"\u201d\u300d]+)["\u201d\u300d]', msg)
        ty = m.group(1) if m else "未知"
        return "TYPE_UNKNOWN", f"PG 类型 '{ty}' 不存在. 通常因 Oracle 库里类型 (如 {ty}) 未在 PG_TYPE_FALLBACK 配置."
    if "语法错误" in msg or "syntax error" in msg_low:
        m = re.search(r'[\u201c"](\w+)[\u201d"]', msg) or re.search(r'near "(\w+)"', msg_low)
        near = m.group(1) if m else "?"
        return "SYNTAX", f"PG 语法错误 (近 '{near}'). 通常因 Oracle 嵌套结构 (BEGIN/END / IF 等) 未被通用引擎识别."
    if ("不是一个已知变量" in msg) or ("is not a known variable" in msg_low) or ("is not a known" in msg_low):
        m = re.search(r'["\u201c\u300c]([^"\u201d\u300d]+)["\u201d\u300d]', msg)
        var = m.group(1) if m else "?"
        return "NESTED", f"变量 '{var}' 未知. 通常因嵌套子程序内联失败 (BEGIN/END 错位 / 嵌套函数未识别)."
    if "already exists" in msg_low or "已存在" in msg:
        return "OK", "对象已存在 (重跑时正常)"
    return "SEMANTIC", f"其他 PG 错误: {msg[:120]}"


def main():
    print("=" * 70)
    print("PL/SQL 产物诊断报告生成器")
    print("=" * 70)

    if not PLSQL_DIR.is_dir():
        print(f"[NG] 找不到 {PLSQL_DIR}")
        sys.exit(1)

    # 1. 建诊断库 + 重置 schema
    try:
        ensure_db()
    except Exception as e:
        print(f"[NG] 无法创建诊断库: {e}")
        sys.exit(1)
    try:
        conn = connect(DIAG_DB)
    except Exception as e:
        print(f"[NG] 无法连接诊断库: {e}")
        sys.exit(1)
    reset_schema(conn)
    cur = conn.cursor()

    # 2. 对每个 pgsql 尝试加载
    pgsql_files = sorted(PLSQL_DIR.glob("*.pgsql"))
    print(f"\n加载 {len(pgsql_files)} 个 PL/SQL 产物到 {DIAG_DB}...")

    results = {"ok": [], "fail": []}
    for f in pgsql_files:
        text = f.read_text(encoding="utf-8")
        name = f.stem.upper()
        # PG 会因语法错误后报 "current transaction is aborted", 需每次 commit
        try:
            conn.rollback()
            cur.execute(text)
            conn.commit()
            results["ok"].append(name)
        except Exception as e:
            conn.rollback()
            err_msg = str(e).strip().split("\n")[0]  # 取首行
            results["fail"].append((name, err_msg))
    conn.close()

    # 3. 生成报告
    print(f"\n结果: {len(results['ok'])} OK / {len(results['fail'])} FAIL")
    if results["ok"]:
        print(f"  OK: {', '.join(sorted(results['ok']))}")
    if not results["fail"]:
        print("全部 PASS, 无需生成报告.")
        return 0

    print("\n生成失败报告:")
    for name, err_msg in results["fail"]:
        # 读源 SQL
        src_kind = "function" if name.startswith("GKBFK") or name.startswith("GKBEK") else "procedure"
        src_file = ROOT / "source" / src_kind / f"{name}.SQL"
        if not src_file.exists():
            # 备选: RECORD/TYPE 可能在 source/type/
            src_file = ROOT / "source" / "type" / f"{name}.SQL"
        src_text = src_file.read_text(encoding="utf-8") if src_file.exists() else ""

        cat, root_cause = categorize_error(err_msg)
        related = []
        # 同类失败关联
        for n2, e2 in results["fail"]:
            if n2 != name and categorize_error(e2)[0] == cat:
                related.append(n2)

        rp = record_failure(
            name, category=cat, phase="PG 加载",
            error_message=err_msg, source_text=src_text,
            root_cause=root_cause,
            suggestions=[
                "若为 TYPE_UNKNOWN: 在 .env 的 PG_TYPE_FALLBACK 中补充该 Oracle 类型映射",
                "若为 SYNTAX/NESTED: 增强通用引擎 convert_function (块感知解析 / 嵌套子程序内联)",
                "若为 ROWTYPE: 实现 _read_ddl_columns 风格的 %ROWTYPE → RETURNS TABLE 展开",
                "运行 `python pg.py convert plsql` 重转后再诊断",
            ],
            related_objects=related[:5],
        )
        print(f"  [REPORT] {name}: {err_msg[:60]} (报告: {rp})")

    print(f"\n报告目录: out/plsql/REPORTS/")
    return 1


if __name__ == "__main__":
    sys.exit(main())