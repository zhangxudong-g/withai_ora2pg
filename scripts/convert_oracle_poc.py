"""
oracle_poc Oracle -> PostgreSQL 自动转换入口

用途:
    把 oracle_poc/01.SQL/ 下的 Oracle DDL/PLSQL 全量转换为 PostgreSQL,
    输出到 oracle_poc/output/, 并保留原有子目录结构。

复用:
    转换引擎 = ora2pg_py_perlstyle/perlstyle_engine.py  (Ora2Pg Perl 原版 Python 重写)
    规则与 workspace/converter/perlstyle_converter.py 一致 (纯规则, 无 AI)。

用法:
    python scripts/convert_oracle_poc.py
    python scripts/convert_oracle_poc.py --clean

输出:
    oracle_poc/output/<相对路径>.sql      转换结果
    oracle_poc/output/00_CREATE_ALL_PG.sql  PG 一键安装脚本 (psql)
    oracle_poc/output/_conversion_report.json  转换元数据
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PERLSTYLE_DIR = REPO_ROOT / "ora2pg_py_perlstyle"
WORKSPACE_DIR = REPO_ROOT / "workspace"
for _p in (PERLSTYLE_DIR, WORKSPACE_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from perlstyle_engine import detect_and_convert  # noqa: E402
from converter.integrity import diff_objects  # noqa: E402  P1-2
from rules.percent_type import (  # noqa: E402  P2-2
    build_type_map_from_table_sql,
    resolve_percent_types,
)
from rules.outer_join_ast import rewrite_outer_joins  # noqa: E402  P2-3
from rules.sequence_synonym import (  # noqa: E402  P2-4
    identify_sequences,
    rewrite_sequence_synonym_views,
)
from rules.exception_map import apply_all_exception_maps  # noqa: E402  P2-5
from rules.dynamic_sql import rewrite_dynamic_sql_all  # noqa: E402  P4-3 批次 1
from rules.bulk_operations import apply_bulk_operations  # noqa: E402  P4-3 批次 2
from rules.type_self import rewrite_type_self  # noqa: E402  P4-4a TYPE SELF
from rules.p44c_fixes import apply_p44c_fixes  # noqa: E402  P4-4c L3 修复
from rules.p44c_returns import apply_p44c_returns  # noqa: E402  P4-4c 阶段2 RETURNS
from rules.p44c_pipe import apply_p44c_pipe  # noqa: E402  P4-4c 阶段3 PIPE
from rules.p44c_create import apply_p44c_create  # noqa: E402  P4-4c 阶段4 CREATE OR REPLACE
from rules.p44c_inout import (  # noqa: E402  P4-4c 阶段5 INOUT+RETURNS
    apply_p44c_inout,
    scan_inout_fixes,
)
from rules.p44c_cursor import (  # noqa: E402  P4-4c 阶段6 动态游标 OPEN FOR
    apply_p44c_cursor,
    scan_open_for_execute,
)
from rules.p44c_cursor_decl import (  # noqa: E402  P4-4c 阶段7 游标声明
    apply_p44c_cursor_decl,
    scan_cursor_decls,
)
from rules.nested_inline import apply_nested_inline  # noqa: E402  P4-4c 嵌套函数内联

POC_DIR = REPO_ROOT / "oracle_poc"
SRC_DIR = POC_DIR / "01.SQL"
DST_DIR = POC_DIR / "output"

# 依赖顺序: 与 oracle_poc/01.SQL/README.md 的创建顺序一致
# TABLE -> SEQUENCE -> TYPE -> VIEW -> TRIGGER -> FUNCTION -> PROCEDURE
#       -> PACKAGE -> PACKAGE_BODY -> SYNONYM -> DATA
INSTALL_ORDER = [
    "TABLE",
    "SEQUENCE",
    "TYPE",
    "VIEW",
    "TRIGGER",
    "FUNCTION",
    "PROCEDURE",
    "PACKAGE",
    "PACKAGE_BODY",
    "SYNONYM",
    "DATA",
]

# 驱动脚本: Oracle SQL*Plus 的 @@include / PROMPT 在 PG(psql) 下不存在,
# 直通无意义, 改由生成的 00_CREATE_ALL_PG.sql 承担。
DRIVER_FILES = {"CREATE_ALL.sql"}

_COMMENT_LINE = re.compile(r"^\s*--[^\n]*", re.MULTILINE)
_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)


def detect_type(sql: str) -> str:
    """对象类型检测 (与 workspace/converter 的规则保持一致)"""
    upper = _COMMENT_BLOCK.sub("", sql)
    upper = _COMMENT_LINE.sub("", upper).strip().upper()

    if re.search(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?PACKAGE\s+BODY\b", upper):
        return "PACKAGE_BODY"
    if re.search(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?PACKAGE\b", upper):
        return "PACKAGE"
    if re.search(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?TRIGGER\b", upper):
        return "TRIGGER"
    if re.search(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?TYPE\b", upper):
        return "TYPE"
    if re.search(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?VIEW\b", upper):
        return "VIEW"
    if re.search(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?PROCEDURE\b", upper):
        return "PROCEDURE"
    if re.search(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\b", upper):
        return "FUNCTION"
    if re.search(r"\bCREATE\s+(?:GLOBAL\s+)?(?:TEMPORARY\s+)?TABLE\b", upper):
        return "TABLE"
    if re.search(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?SEQUENCE\b", upper):
        return "SEQUENCE"
    if re.search(r"\bCREATE\s+(?:PUBLIC\s+)?SYNONYM\b", upper):
        return "SYNONYM"
    if re.search(r"\bINSERT\b", upper) or re.search(r"\bMERGE\b", upper):
        return "DATA"
    return "UNKNOWN"


def is_passthrough(text: str) -> bool:
    """
    是否直通 (不转换)。

    DATA 文件直通: 纯 INSERT/MERGE, 由 perlstyle 的类型映射单独处理容易
    破坏字面量; 这里保持与 workspace/converter 一致的直通行为。
    """
    return detect_type(text) == "DATA"


def render_with_warnings(converted: str, warnings) -> str:
    if not warnings:
        return converted
    head = "-- Perl-style Ora2Py Warnings:\n"
    head += "".join(f"-- {w}\n" for w in warnings)
    return head + "\n" + converted


# ---------------------------------------------------------------------------
# P2-2: %TYPE 查表展开（后处理）
#   转换器本身在 DECLARE 块内只做了"删 %TYPE"（perlstyle_engine.py:4166-4169），
#   留下了表名列名作为"伪类型"——这在 PG 中语法错误。
#   本后处理从已转换的 TABLE 文件建 表.列 → PG类型 字典，代入 FUNCTION/PROCEDURE/
#   PACKAGE/PACKAGE_BODY/TRIGGER 的 DECLARE 块。查不到保留原文 + warning（不抛错）。
# ---------------------------------------------------------------------------
_POST_PASS_DIRS = ("FUNCTION", "PROCEDURE", "PACKAGE", "PACKAGE_BODY", "TRIGGER")


def post_pass_percent_type(dst_dir: Path) -> dict:
    """对已生成的产物文件运行 %TYPE 后处理。

    Returns:
        {"substituted": int, "warnings": List[str], "files_touched": List[str]}
    """
    # 1. 从 TABLE 文件建 type_map
    type_map = {}
    table_dir = dst_dir / "TABLE"
    if table_dir.exists():
        for tbl in sorted(table_dir.glob("*.sql")):
            try:
                text = tbl.read_text(encoding="utf-8", errors="replace")
                type_map.update(build_type_map_from_table_sql(text))
            except OSError:
                pass

    if not type_map:
        return {"substituted": 0, "warnings": [], "files_touched": []}

    # 2. 对每个函数/过程类文件跑 resolve
    all_warnings: List[str] = []
    files_touched: List[str] = []
    total_substituted = 0
    for sub in _POST_PASS_DIRS:
        sub_dir = dst_dir / sub
        if not sub_dir.exists():
            continue
        for f in sorted(sub_dir.glob("*.sql")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            new, warns = resolve_percent_types(text, type_map)
            if warns:
                all_warnings.extend([f"[{f.name}] {w}" for w in warns])
            if new != text:
                f.write_text(new, encoding="utf-8")
                files_touched.append(f.name)
                total_substituted += text.count("%TYPE") - new.count("%TYPE")

    return {
        "substituted": total_substituted,
        "warnings": all_warnings,
        "files_touched": files_touched,
    }


# ---------------------------------------------------------------------------
# P2-3: (+) 外连接 → ANSI JOIN（后处理）
#   引擎内建逻辑已处理主查询的 (+)，但子查询内残留 2 处（oracle_poc PROCEDURE）。
#   本后处理用 rules.outer_join_ast 模块做正则/图论改写。
# ---------------------------------------------------------------------------


def post_pass_outer_join(dst_dir: Path) -> dict:
    """对所有产物文件运行 (+) → ANSI JOIN 改写。

    Returns: {"substituted": int, "files_touched": List[str]}
    """
    files_touched = []
    total_substituted = 0
    for sub in ("FUNCTION", "PROCEDURE", "PACKAGE", "PACKAGE_BODY", "TRIGGER", "VIEW", "TYPE"):
        sub_dir = dst_dir / sub
        if not sub_dir.exists():
            continue
        for f in sorted(sub_dir.glob("*.sql")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            new, n = rewrite_outer_joins(text)
            if n > 0:
                f.write_text(new, encoding="utf-8")
                files_touched.append(f.name)
                total_substituted += n
    return {
        "substituted": total_substituted,
        "files_touched": files_touched,
    }


# ---------------------------------------------------------------------------
# P2-4: 序列/同义话语义治理（后处理）
#   当前 convert_synonym 把所有 SYNONYM 都写成 CREATE VIEW x AS SELECT * FROM y;
#   对序列同义词（y 是序列）来说这是语法错误（PG 序列不是表）。
#   本后处理识别 target 是序列时改写为 nextval()。
# ---------------------------------------------------------------------------


def post_pass_sequence_synonym(dst_dir: Path) -> dict:
    """对 SYNONYM 产物中的序列同义词做改写。

    Returns: {"substituted": int, "files_touched": List[str]}
    """
    # 1. 从 SEQUENCE 文件建序列集合
    seq_dir = dst_dir / "SEQUENCE"
    if not seq_dir.exists():
        return {"substituted": 0, "files_touched": []}
    sequences = set()
    for f in sorted(seq_dir.glob("*.sql")):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
            sequences.update(identify_sequences(text))
        except OSError:
            pass
    if not sequences:
        return {"substituted": 0, "files_touched": []}

    # 2. 改写 SYNONYM 文件
    syn_dir = dst_dir / "SYNONYM"
    if not syn_dir.exists():
        return {"substituted": 0, "files_touched": []}
    files_touched = []
    total_substituted = 0
    for f in sorted(syn_dir.glob("*.sql")):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        new, n = rewrite_sequence_synonym_views(text, sequences)
        if n > 0:
            f.write_text(new, encoding="utf-8")
            files_touched.append(f.name)
            total_substituted += n
    return {
        "substituted": total_substituted,
        "files_touched": files_touched,
    }


# ---------------------------------------------------------------------------
# P2-5: 异常与运行时属性映射（后处理）
#   对所有 PL/SQL 文件（FUNCTION/PROCEDURE/PACKAGE/PACKAGE_BODY/TRIGGER）
#   应用异常体系映射：SQLCODE/SQLERRM/SQL%ROWCOUNT/%NOTFOUND/异常名/WHEN OTHERS。
# ---------------------------------------------------------------------------

# P2-5 后处理覆盖的子目录（VIEW/TYPE/SEQUENCE/SYNONYM/TABLE/DATA 通常不含 PL/SQL）
_P25_TARGET_SUBDIRS = ("FUNCTION", "PROCEDURE", "PACKAGE", "PACKAGE_BODY", "TRIGGER")


def post_pass_exception_map(dst_dir: Path) -> dict:
    """P2-5 异常体系映射后处理。

    对所有 PL/SQL 文件应用 apply_all_exception_maps。
    Returns: {"substituted": int, "files_touched": List[str], "warnings": List[str]}
    """
    files_touched: List[str] = []
    total_substituted = 0
    warnings: List[str] = []
    for sub in _P25_TARGET_SUBDIRS:
        sub_dir = dst_dir / sub
        if not sub_dir.exists():
            continue
        for f in sorted(sub_dir.glob("*.sql")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                new = apply_all_exception_maps(text)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{f.name}: P2-5 应用失败 {type(exc).__name__}: {exc}")
                continue
            # 简单差异计数：行数变化 + 关键字变化
            n = _count_p25_substitutions(text, new)
            if n > 0:
                f.write_text(new, encoding="utf-8")
                files_touched.append(f.name)
                total_substituted += n
    return {
        "substituted": total_substituted,
        "files_touched": files_touched,
        "warnings": warnings,
    }


def _count_p25_substitutions(old: str, new: str) -> int:
    """粗略统计 P2-5 替换次数（基于关键字计数差异）。"""
    keys = [
        ("SQLCODE", "_p25_sqlstate"),
        ("SQLERRM", "_p25_msg"),
        ("SQL%ROWCOUNT", "ROW_COUNT"),
        ("%NOTFOUND", "NOT FOUND"),
        ("%ISOPEN", "IS NOT FOUND"),
        ("DUP_VAL_ON_INDEX", "unique_violation"),
        ("NO_DATA_FOUND", "no_data_found"),
        ("TOO_MANY_ROWS", "too_many_rows"),
    ]
    n = 0
    for old_k, new_k in keys:
        n += old.count(old_k) - new.count(old_k)
    # GET STACKED DIAGNOSTICS 注入数
    n += new.count("GET STACKED DIAGNOSTICS") - old.count("GET STACKED DIAGNOSTICS")
    # RAISE 注入数（WHEN OTHERS 强制 RAISE）
    n += _count_raises_in_when_others(new) - _count_raises_in_when_others(old)
    return max(n, 0)


def _count_raises_in_when_others(sql: str) -> int:
    """统计 WHEN OTHERS 块内 RAISE 数量。"""
    import re as _re
    return len(_re.findall(
        r"WHEN\s+OTHERS\s+THEN[\s\S]*?\bRAISE\b",
        sql,
        _re.IGNORECASE,
    ))


# ---------------------------------------------------------------------------
# P4-3 批次 1: 动态 SQL + DUAL 机械替换（后处理）
#   FROM DUAL / *.NEXTVAL / *.CURRVAL / EXECUTE IMMEDIATE
# ---------------------------------------------------------------------------
_P43_TARGET_SUBDIRS = ("FUNCTION", "PROCEDURE", "PACKAGE", "PACKAGE_BODY", "TRIGGER", "VIEW")
_P44C_TARGET_SUBDIRS = ("FUNCTION", "PROCEDURE", "PACKAGE", "PACKAGE_BODY", "TRIGGER")


def post_pass_dynamic_sql(dst_dir: Path) -> dict:
    """P4-3 批次 1 机械替换。

    Returns: {"substituted": int, "files_touched": List[str], "warnings": List[str]}
    """
    files_touched: List[str] = []
    total = 0
    warnings: List[str] = []
    for sub in _P43_TARGET_SUBDIRS:
        sub_dir = dst_dir / sub
        if not sub_dir.exists():
            continue
        for f in sorted(sub_dir.glob("*.sql")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                new = rewrite_dynamic_sql_all(text)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{f.name}: P4-3 应用失败 {type(exc).__name__}: {exc}")
                continue
            n = _count_dynamic_substitutions(text, new)
            if n > 0:
                f.write_text(new, encoding="utf-8")
                files_touched.append(f.name)
                total += n
    return {"substituted": total, "files_touched": files_touched, "warnings": warnings}


def _count_dynamic_substitutions(old: str, new: str) -> int:
    """粗略统计 P4-3 批次 1 替换次数。"""
    n = 0
    n += len(re.findall(r"\bFROM\s+DUAL\b", old, re.I)) - len(
        re.findall(r"\bFROM\s+DUAL\b", new, re.I))
    n += len(re.findall(r"\.\s*NEXTVAL\b", old, re.I)) - len(
        re.findall(r"\.\s*NEXTVAL\b", new, re.I))
    n += len(re.findall(r"\.\s*CURRVAL\b", old, re.I)) - len(
        re.findall(r"\.\s*CURRVAL\b", new, re.I))
    n += len(re.findall(r"\bEXECUTE\s+IMMEDIATE\b", old, re.I)) - len(
        re.findall(r"\bEXECUTE\s+IMMEDIATE\b", new, re.I))
    return max(n, 0)


# ---------------------------------------------------------------------------
# P4-3 批次 2: BULK COLLECT / FORALL 改写（后处理）
# ---------------------------------------------------------------------------
def post_pass_bulk_operations(dst_dir: Path) -> dict:
    """P4-3 批次 2：BULK COLLECT 多变量桩 + FORALL → FOR 循环。

    Returns: {"substituted": int, "files_touched": List[str], "warnings": List[str]}
    """
    files_touched: List[str] = []
    total = 0
    warnings: List[str] = []
    for sub in _P43_TARGET_SUBDIRS:
        sub_dir = dst_dir / sub
        if not sub_dir.exists():
            continue
        for f in sorted(sub_dir.glob("*.sql")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                new = apply_bulk_operations(text)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{f.name}: P4-3 批2 失败 {type(exc).__name__}: {exc}")
                continue
            n = _count_bulk_substitutions(text, new)
            if n > 0:
                f.write_text(new, encoding="utf-8")
                files_touched.append(f.name)
                total += n
    return {"substituted": total, "files_touched": files_touched, "warnings": warnings}


def _count_bulk_substitutions(old: str, new: str) -> int:
    """粗略统计 P4-3 批次 2 替换次数。"""
    n = 0
    # FORALL 出现次数差
    n += len(re.findall(r"\bFORALL\b", old, re.I)) - len(
        re.findall(r"\bFORALL\b", new, re.I))
    # 多变量 BULK COLLECT INTO 出现次数差
    multi_re = re.compile(
        r"\bBULK\s+COLLECT\s+INTO\s+(?:\s*\w+\s*,){1,}", re.I
    )
    n += len(multi_re.findall(old)) - len(multi_re.findall(new))
    return max(n, 0)


# ---------------------------------------------------------------------------
# P4-4a: TYPE 文件 SELF 引用改写（后处理）
# ---------------------------------------------------------------------------
def post_pass_type_self(dst_dir: Path) -> dict:
    """P4-4a：TYPE 文件中 SELF_<TYPE>_<FIELD> → i_self.<FIELD>，加 i_self 参数。

    Returns: {"substituted": int, "files_touched": List[str], "warnings": List[str]}
    """
    files_touched: List[str] = []
    total = 0
    warnings: List[str] = []
    type_dir = dst_dir / "TYPE"
    if not type_dir.exists():
        return {"substituted": 0, "files_touched": [], "warnings": warnings}
    for f in sorted(type_dir.glob("*.sql")):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            new = rewrite_type_self(text)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{f.name}: P4-4a 失败 {type(exc).__name__}: {exc}")
            continue
        n = _count_type_self_substitutions(text, new)
        if n > 0:
            f.write_text(new, encoding="utf-8")
            files_touched.append(f.name)
            total += n
    return {"substituted": total, "files_touched": files_touched, "warnings": warnings}


def _count_type_self_substitutions(old: str, new: str) -> int:
    """粗略统计 P4-4a 替换次数（SELF 引用被替换数 + 函数签名加参数数）。"""
    # 统计 SELF_xxx 引用出现次数差
    n = 0
    n += len(re.findall(r"\bSELF_(?:SCORE_RECORD|STUDENT_RECORD|REF_CURSOR_T|CONFIG_RECORD)_", old, re.I)) - len(
        re.findall(r"\bSELF_(?:SCORE_RECORD|STUDENT_RECORD|REF_CURSOR_T|CONFIG_RECORD)_", new, re.I))
    # 统计 i_self XXX 类型注解出现次数
    n += len(re.findall(r"i_self\s+(?:SCORE_RECORD|STUDENT_RECORD|REF_CURSOR_T|CONFIG_RECORD)\b", new, re.I)) - len(
        re.findall(r"i_self\s+(?:SCORE_RECORD|STUDENT_RECORD|REF_CURSOR_T|CONFIG_RECORD)\b", old, re.I))
    return max(n, 0)


# ---------------------------------------------------------------------------
# P4-4c: L3 装入失败小修复（后处理）
# ---------------------------------------------------------------------------
def post_pass_p44c_fixes(dst_dir: Path) -> dict:
    """P4-4c 第一阶段：BEGIN set_config + FOR rec record 声明补充。

    Returns: {"substituted": int, "files_touched": List[str], "warnings": List[str]}
    """
    files_touched: List[str] = []
    total = 0
    warnings: List[str] = []
    for sub in _P44C_TARGET_SUBDIRS:
        sub_dir = dst_dir / sub
        if not sub_dir.exists():
            continue
        for f in sorted(sub_dir.glob("*.sql")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                new = apply_p44c_fixes(text)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{f.name}: P4-4c 失败 {type(exc).__name__}: {exc}")
                continue
            n = _count_p44c_substitutions(text, new)
            if n > 0:
                f.write_text(new, encoding="utf-8")
                files_touched.append(f.name)
                total += n
    return {"substituted": total, "files_touched": files_touched, "warnings": warnings}


def _count_p44c_substitutions(old: str, new: str) -> int:
    """粗略统计 P4-4c 替换次数。"""
    n = 0
    # BEGIN set_config → BEGIN + PERFORM set_config
    n += len(re.findall(r"^BEGIN\s+set_config\s*\(", old, re.I | re.M)) - len(
        re.findall(r"^BEGIN\s+set_config\s*\(", new, re.I | re.M))
    # rec record 声明补充
    n += len(re.findall(r"\brec\s+record\s*;", new, re.I)) - len(
        re.findall(r"\brec\s+record\s*;", old, re.I))
    return max(n, 0)


# ---------------------------------------------------------------------------
# P4-4c 阶段 2: RETURNS TABLE(IN/o_xxx) 错误修复（后处理）
# ---------------------------------------------------------------------------
def post_pass_p44c_returns(dst_dir: Path) -> dict:
    files_touched: List[str] = []
    total = 0
    warnings: List[str] = []
    for sub in _P44C_TARGET_SUBDIRS:
        sub_dir = dst_dir / sub
        if not sub_dir.exists():
            continue
        for f in sorted(sub_dir.glob("*.sql")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                new = apply_p44c_returns(text)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{f.name}: P4-4c 阶段2 失败 {type(exc).__name__}: {exc}")
                continue
            n = _count_p44c_returns_substitutions(text, new)
            if n > 0:
                f.write_text(new, encoding="utf-8")
                files_touched.append(f.name)
                total += n
    return {"substituted": total, "files_touched": files_touched, "warnings": warnings}


def _count_p44c_returns_substitutions(old: str, new: str) -> int:
    bad_re = re.compile(r"RETURNS\s+TABLE\s*\(\s*(?:IN|o_)\w+", re.I)
    return len(bad_re.findall(old)) - len(bad_re.findall(new))


# ---------------------------------------------------------------------------
# P4-4c 阶段 3: PIPE ROW 改写为 RETURN NEXT（后处理）
# ---------------------------------------------------------------------------
def post_pass_p44c_pipe(dst_dir: Path) -> dict:
    files_touched: List[str] = []
    total = 0
    warnings: List[str] = []
    for sub in _P44C_TARGET_SUBDIRS:
        sub_dir = dst_dir / sub
        if not sub_dir.exists():
            continue
        for f in sorted(sub_dir.glob("*.sql")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                new = apply_p44c_pipe(text)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{f.name}: P4-4c 阶段3 失败 {type(exc).__name__}: {exc}")
                continue
            n = _count_p44c_pipe_substitutions(text, new)
            if n > 0:
                f.write_text(new, encoding="utf-8")
                files_touched.append(f.name)
                total += n
    return {"substituted": total, "files_touched": files_touched, "warnings": warnings}


def _count_p44c_pipe_substitutions(old: str, new: str) -> int:
    """统计 PIPE ROW 被改写次数。"""
    return len(re.findall(r"\bPIPE\s+ROW\b", old, re.I)) - len(
        re.findall(r"\bPIPE\s+ROW\b", new, re.I))


# ---------------------------------------------------------------------------
# P4-4c 阶段 4: CREATE OR REPLACE → DROP IF EXISTS + CREATE（后处理）
# ---------------------------------------------------------------------------
def post_pass_p44c_create(dst_dir: Path) -> dict:
    files_touched: List[str] = []
    total = 0
    warnings: List[str] = []
    for sub in _P44C_TARGET_SUBDIRS:
        sub_dir = dst_dir / sub
        if not sub_dir.exists():
            continue
        for f in sorted(sub_dir.glob("*.sql")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                new = apply_p44c_create(text)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{f.name}: P4-4c 阶段4 失败 {type(exc).__name__}: {exc}")
                continue
            n = _count_p44c_create_substitutions(text, new)
            if n > 0:
                f.write_text(new, encoding="utf-8")
                files_touched.append(f.name)
                total += n
    return {"substituted": total, "files_touched": files_touched, "warnings": warnings}


def _count_p44c_create_substitutions(old: str, new: str) -> int:
    """统计新增的 DROP FUNCTION IF EXISTS 语句。"""
    return len(re.findall(r"DROP\s+FUNCTION\s+IF\s+EXISTS", new, re.I)) - len(
        re.findall(r"DROP\s+FUNCTION\s+IF\s+EXISTS", old, re.I))


# ---------------------------------------------------------------------------
# P4-4c 阶段 5: INOUT + RETURNS 错配修复（后处理）
# ---------------------------------------------------------------------------
def post_pass_p44c_inout(dst_dir: Path) -> dict:
    files_touched: List[str] = []
    total = 0
    returns_fixed = 0
    bare_return = 0
    warnings: List[str] = []
    for sub in _P44C_TARGET_SUBDIRS:
        sub_dir = dst_dir / sub
        if not sub_dir.exists():
            continue
        for f in sorted(sub_dir.glob("*.sql")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                stats = scan_inout_fixes(text)
                new = apply_p44c_inout(text)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{f.name}: P4-4c 阶段5 失败 {type(exc).__name__}: {exc}")
                continue
            n = stats["returns_fixed"] + stats["bare_return"]
            if n > 0:
                f.write_text(new, encoding="utf-8")
                files_touched.append(f.name)
                total += n
                returns_fixed += stats["returns_fixed"]
                bare_return += stats["bare_return"]
    return {"substituted": total, "files_touched": files_touched,
            "returns_fixed": returns_fixed, "bare_return": bare_return,
            "warnings": warnings}


# ---------------------------------------------------------------------------
# P4-4c 阶段 6: 动态游标 OPEN c FOR <函数> -> OPEN c FOR EXECUTE <函数>
# ---------------------------------------------------------------------------
def post_pass_p44c_cursor(dst_dir: Path) -> dict:
    files_touched: List[str] = []
    total = 0
    warnings: List[str] = []
    for sub in _P44C_TARGET_SUBDIRS:
        sub_dir = dst_dir / sub
        if not sub_dir.exists():
            continue
        for f in sorted(sub_dir.glob("*.sql")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                n = scan_open_for_execute(text)["count"]
                new = apply_p44c_cursor(text)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{f.name}: P4-4c 阶段6 失败 {type(exc).__name__}: {exc}")
                continue
            if n > 0:
                f.write_text(new, encoding="utf-8")
                files_touched.append(f.name)
                total += n
    return {"substituted": total, "files_touched": files_touched, "warnings": warnings}


# ---------------------------------------------------------------------------
# P4-4c 阶段 7: 游标声明词序 + 游标类型记录变量
# ---------------------------------------------------------------------------
def post_pass_p44c_cursor_decl(dst_dir: Path) -> dict:
    files_touched: List[str] = []
    order_fixed = 0
    record_fixed = 0
    warnings: List[str] = []
    for sub in _P44C_TARGET_SUBDIRS:
        sub_dir = dst_dir / sub
        if not sub_dir.exists():
            continue
        for f in sorted(sub_dir.glob("*.sql")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                st = scan_cursor_decls(text)
                new = apply_p44c_cursor_decl(text)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{f.name}: P4-4c 阶段7 失败 {type(exc).__name__}: {exc}")
                continue
            if st["order_fixed"] or st["record_fixed"]:
                f.write_text(new, encoding="utf-8")
                files_touched.append(f.name)
                order_fixed += st["order_fixed"]
                record_fixed += st["record_fixed"]
    return {"order_fixed": order_fixed, "record_fixed": record_fixed,
            "substituted": order_fixed + record_fixed,
            "files_touched": files_touched, "warnings": warnings}


def _post_pass_nested_inline(dst_dir: Path) -> dict:
    """P4-4c 阶段 8：嵌套函数内联（单 RETURN）。"""
    files_touched: List[str] = []
    total = 0
    warnings: List[str] = []
    for sub in _P44C_TARGET_SUBDIRS:
        sub_dir = dst_dir / sub
        if not sub_dir.exists():
            continue
        for f in sorted(sub_dir.glob("*.sql")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                new = apply_nested_inline(text)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{f.name}: 嵌套内联失败 {type(exc).__name__}: {exc}")
                continue
            if new != text:
                f.write_text(new, encoding="utf-8")
                files_touched.append(f.name)
                total += 1
    return {"substituted": total, "files_touched": files_touched, "warnings": warnings}


def convert_one(src: Path, dst: Path) -> dict:
    started = time.perf_counter()
    record = {
        "source": str(src.relative_to(REPO_ROOT)),
        "output": str(dst.relative_to(REPO_ROOT)),
        "object_type": "UNKNOWN",
        "passthrough": False,
        "success": False,
        "error": "",
        "warnings": [],
        "input_bytes": 0,
        "output_bytes": 0,
        "duration_ms": 0.0,
        "dropped_objects": [],   # P1-2: 源有、产物没有的对象（静默丢失显式化）
    }

    try:
        text = src.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        record["error"] = f"读取失败: {exc}"
        return record

    record["input_bytes"] = len(text)
    record["object_type"] = detect_type(text)

    try:
        if is_passthrough(text):
            body = text
            warnings: list[str] = []
            record["passthrough"] = True
        else:
            body, warnings = detect_and_convert(text)
            record["warnings"] = list(warnings or [])
        out_text = render_with_warnings(body, warnings)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(out_text, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - 转换失败需记录而非中断全批
        record["error"] = f"转换异常: {type(exc).__name__}: {exc}"
        record["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return record

    record["success"] = True
    record["output_bytes"] = len(out_text)
    record["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)

    # W0-4: 完整性 diff 移到所有 post-pass 之后（main() 函数），
    # 避免在产物还被 post_pass_* 改写时取中间态。
    # record["dropped_objects"] 字段不再在 convert_one 内部填——
    # 由 main() 在 post-pass_* 全部完成后调 _postpass_diff 补上。
    return record


def _postpass_diff(dst_dir: Path, results: list) -> None:
    """W0-4: 在所有 post-pass_* 完成后调，补上 dropped_objects 字段。

    与原 convert_one 内逻辑一致，但读取的是已 post-pass 改完的最终产物。
    """
    for r in results:
        otype = r.get("object_type")
        if r.get("passthrough") or otype not in {
            "TRIGGER", "FUNCTION", "PROCEDURE", "PACKAGE",
            "TYPE", "VIEW", "TABLE", "SEQUENCE", "SYNONYM",
        }:
            continue
        src = SRC_DIR / r["source"] if not Path(r["source"]).is_absolute() else Path(r["source"])
        dst = dst_dir / Path(r["output"]).relative_to(dst_dir) if False else None
        # 重新计算路径（结果里的 source/output 是相对 REPO_ROOT 的字符串）
        try:
            src_text = src.read_text(encoding="utf-8", errors="replace")
            dst_path = dst_dir / Path(r["output"]).name if False else None
        except OSError:
            continue
        # 还原 dst 路径：从 output 字符串取
        output_rel = Path(r["output"])
        # output 是 "oracle_poc/output/TRIGGER/00_TRIGGER.sql" 形式
        try:
            dst_path = REPO_ROOT / output_rel
            prod_text = dst_path.read_text(encoding="utf-8", errors="replace")
            report = diff_objects(src_text, prod_text, otype)
            r["dropped_objects"] = report.missing
        except Exception as exc:  # noqa: BLE001
            r.setdefault("warnings", []).append(
                f"[P1-2] 完整性比对失败: {type(exc).__name__}: {exc}"
            )


def collect_sources() -> list[Path]:
    return sorted(p for p in SRC_DIR.rglob("*") if p.is_file() and p.suffix.lower() == ".sql")


def build_pg_driver(dst_dir: Path, files: list[Path]) -> str:
    """生成 psql 一键安装脚本, 替换 Oracle SQL*Plus 的 @@include / PROMPT"""
    by_dir: dict[str, list[str]] = {}
    for f in files:
        if f.name in DRIVER_FILES:
            continue
        rel = f.relative_to(dst_dir).as_posix()
        by_dir.setdefault(f.parent.name, []).append(rel)

    order = [d for d in INSTALL_ORDER if d in by_dir]
    order += sorted(d for d in by_dir if d not in INSTALL_ORDER)

    lines = [
        "-- ============================================================================",
        "-- PostgreSQL 一键安装脚本 (由 scripts/convert_oracle_poc.py 自动生成)",
        f"-- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "-- 用法: psql -d <db> -f 00_CREATE_ALL_PG.sql",
        "--       可指定 schema:  psql -v schema=my_schema -f 00_CREATE_ALL_PG.sql",
        "--       默认 schema:    oracle_poc_verify",
        "-- ============================================================================",
        "",
        "\\set ON_ERROR_STOP on",
        "",
        "-- Defect-1 (D0): schema 隔离（默认 oracle_poc_verify，可用 -v schema=xxx 覆盖）",
        "\\set schema oracle_poc_verify",
        "CREATE SCHEMA IF NOT EXISTS :schema;",
        "\\set search_path :schema",
        "",
    ]

    labels = {
        "TABLE": "1. 创建表",
        "SEQUENCE": "2. 创建序列",
        "TYPE": "3. 创建类型",
        "VIEW": "4. 创建视图 (必须在触发器之前)",
        "TRIGGER": "5. 创建触发器",
        "FUNCTION": "6. 创建函数",
        "PROCEDURE": "7. 创建存储过程",
        "PACKAGE": "8. 创建包 (PG: schema + 函数集合)",
        "PACKAGE_BODY": "9. 创建包主体",
        "SYNONYM": "10. 创建同义词 (PG: 视图别名)",
        "DATA": "11. 插入测试数据",
    }

    for idx, dirname in enumerate(order, start=1):
        label = labels.get(dirname, f"{idx}. {dirname}")
        lines.append("-- " + "-" * 74)
        lines.append(f"-- {label}")
        lines.append("-- " + "-" * 74)
        for rel in sorted(by_dir[dirname]):
            # \ir = 相对于脚本自身所在目录, 避免依赖调用者 CWD
            lines.append(f"\\ir {rel}")
        lines.append("")

    lines.append("\\echo '========================================'")
    lines.append("\\echo 'PostgreSQL 版本安装完成!'")
    lines.append("\\echo '========================================'")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="oracle_poc Oracle -> PostgreSQL 转换")
    ap.add_argument("--clean", action="store_true", help="转换前清空输出目录")
    ap.add_argument("--src", default=str(SRC_DIR), help="Oracle 源目录")
    ap.add_argument("--dst", default=str(DST_DIR), help="PG 输出目录")
    args = ap.parse_args()

    src_dir = Path(args.src).resolve()
    dst_dir = Path(args.dst).resolve()

    if not src_dir.is_dir():
        print(f"错误: 源目录不存在: {src_dir}")
        return 1

    if args.clean and dst_dir.exists():
        shutil.rmtree(dst_dir)
        print(f"已清空: {dst_dir}")

    sources = collect_sources()
    if not sources:
        print(f"错误: 未找到源文件: {src_dir}")
        return 1

    print("=" * 70)
    print("oracle_poc  Oracle -> PostgreSQL  转换")
    print(f"源目录 : {src_dir}")
    print(f"输出   : {dst_dir}")
    print(f"文件数 : {len(sources)}")
    print("=" * 70)

    results = []
    for src in sources:
        rel = src.relative_to(src_dir)
        dst = dst_dir / rel
        if src.name in DRIVER_FILES:
            print(f"  [跳过驱动] {rel.as_posix()}  (由 00_CREATE_ALL_PG.sql 取代)")
            continue
        rec = convert_one(src, dst)
        results.append(rec)
        flag = "OK " if rec["success"] else "ERR"
        print(f"  [{flag}] {rel.as_posix():<28} {rec['object_type']:<14} "
              f"{rec['input_bytes']:>6}B -> {rec['output_bytes']:>6}B  "
              f"{rec['duration_ms']:>7.1f}ms"
              + (f"  <- {rec['error']}" if rec["error"] else ""))

    # 驱动脚本
    driver_path = dst_dir / "00_CREATE_ALL_PG.sql"
    driver_path.parent.mkdir(parents=True, exist_ok=True)
    driver_path.write_text(
        build_pg_driver(dst_dir, [REPO_ROOT / r["output"] for r in results]), encoding="utf-8"
    )
    print(f"  [生成驱动] {driver_path.relative_to(REPO_ROOT)}")

    # P2-2 后处理: %TYPE 查表展开（仅在表后生成完表产物后再跑）
    post = post_pass_percent_type(dst_dir)
    if post["substituted"] or post["warnings"]:
        print(f"  [P2-2 后处理] 代入 {post['substituted']} 处 %TYPE, 改动 {len(post['files_touched'])} 文件")
        for w in post["warnings"]:
            print(f"    [warn] {w}")

    # P2-3 后处理: (+) 外连接 → ANSI JOIN 改写
    post_oj = post_pass_outer_join(dst_dir)
    if post_oj["substituted"]:
        print(f"  [P2-3 后处理] 改写 {post_oj['substituted']} 处 (+) → ANSI JOIN")

    # P2-4 后处理: 序列同义词改写为 nextval()
    post_syn = post_pass_sequence_synonym(dst_dir)
    if post_syn["substituted"]:
        print(f"  [P2-4 后处理] 改写 {post_syn['substituted']} 处序列同义词 → nextval()")

    # P2-5 后处理: 异常与运行时属性映射（SQLCODE/SQLERRM/SQL%ROWCOUNT/%NOTFOUND/异常名/WHEN OTHERS）
    post_p25 = post_pass_exception_map(dst_dir)
    if post_p25["substituted"] or post_p25.get("warnings"):
        print(f"  [P2-5 后处理] 异常体系 {post_p25['substituted']} 处改写, 警告 {len(post_p25.get('warnings', []))} 条")
        for w in post_p25.get("warnings", [])[:5]:
            print(f"    [warn] {w}")

    # P4-3 批次 1 后处理: 动态 SQL + DUAL（FROM DUAL / NEXTVAL / EXECUTE IMMEDIATE）
    post_p43 = post_pass_dynamic_sql(dst_dir)
    if post_p43["substituted"] or post_p43.get("warnings"):
        print(f"  [P4-3 批1 后处理] 动态SQL/DUAL {post_p43['substituted']} 处改写")
        for w in post_p43.get("warnings", [])[:5]:
            print(f"    [warn] {w}")

    # P4-3 批次 2 后处理: BULK COLLECT 多变量桩 + FORALL → FOR 循环
    post_p43b = post_pass_bulk_operations(dst_dir)
    if post_p43b["substituted"] or post_p43b.get("warnings"):
        print(f"  [P4-3 批2 后处理] BULK/FORALL {post_p43b['substituted']} 处改写")

    # P4-4a 后处理: TYPE 文件 SELF 引用改写
    post_p44a = post_pass_type_self(dst_dir)
    if post_p44a["substituted"] or post_p44a.get("warnings"):
        print(f"  [P4-4a 后处理] TYPE SELF {post_p44a['substituted']} 处改写")

    # P4-4c 后处理: L3 装入失败小修复（set_config + FOR rec record）
    post_p44c = post_pass_p44c_fixes(dst_dir)
    if post_p44c["substituted"] or post_p44c.get("warnings"):
        print(f"  [P4-4c 后处理] L3 小修复 {post_p44c['substituted']} 处改写")

    # P4-4c 阶段 2: RETURNS TABLE(IN/o_xxx) 错误修复
    post_p44c2 = post_pass_p44c_returns(dst_dir)
    if post_p44c2["substituted"] or post_p44c2.get("warnings"):
        print(f"  [P4-4c 阶段2] RETURNS TABLE 修复 {post_p44c2['substituted']} 处")

    # P4-4c 阶段 3: PIPE ROW → RETURN NEXT
    post_p44c3 = post_pass_p44c_pipe(dst_dir)
    if post_p44c3["substituted"] or post_p44c3.get("warnings"):
        print(f"  [P4-4c 阶段3] PIPE ROW 改写 {post_p44c3['substituted']} 处")

    # P4-4c 阶段 4: CREATE OR REPLACE → DROP IF EXISTS + CREATE
    post_p44c4 = post_pass_p44c_create(dst_dir)
    if post_p44c4["substituted"] or post_p44c4.get("warnings"):
        print(f"  [P4-4c 阶段4] DROP IF EXISTS {post_p44c4['substituted']} 处")

    # P4-4c 阶段 5: INOUT + RETURNS 错配修复
    post_p44c5 = post_pass_p44c_inout(dst_dir)
    if post_p44c5["substituted"] or post_p44c5.get("warnings"):
        print(f"  [P4-4c 阶段5] OUT/INOUT+RETURNS: RETURNS 改型 "
              f"{post_p44c5['returns_fixed']} 处 + 裸 RETURN {post_p44c5['bare_return']} 处")

    # P4-4c 阶段 6: 动态游标 OPEN c FOR <函数> -> OPEN c FOR EXECUTE <函数>
    post_p44c6 = post_pass_p44c_cursor(dst_dir)
    if post_p44c6["substituted"] or post_p44c6.get("warnings"):
        print(f"  [P4-4c 阶段6] OPEN FOR EXECUTE {post_p44c6['substituted']} 处")

    # P4-4c 阶段 7: 游标声明词序 + 游标类型记录变量
    post_p44c7 = post_pass_p44c_cursor_decl(dst_dir)
    if post_p44c7["substituted"] or post_p44c7.get("warnings"):
        print(f"  [P4-4c 阶段7] 游标声明: 词序 {post_p44c7['order_fixed']} 处 "
              f"+ RECORD {post_p44c7['record_fixed']} 处")

    # P4-4c 阶段 8: 嵌套函数内联（单 RETURN 的嵌套函数）
    post_nested = _post_pass_nested_inline(dst_dir)
    if post_nested["substituted"]:
        print(f"  [P4-4c 阶段8] 嵌套内联: {post_nested['substituted']} 处")

    # W0-4: 完整性 diff 在所有 post-pass 之后跑（产物稳定后）
    _postpass_diff(dst_dir, results)

    ok = sum(1 for r in results if r["success"])
    warn = sum(len(r["warnings"]) for r in results)
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "engine": "ora2pg_py_perlstyle/perlstyle_engine.py",
        "source_dir": str(src_dir.relative_to(REPO_ROOT)),
        "output_dir": str(dst_dir.relative_to(REPO_ROOT)),
        "total": len(results),
        "success": ok,
        "failed": len(results) - ok,
        "total_warnings": warn,
        "results": results,
    }
    report_path = dst_dir / "_conversion_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    # P1-2 完整性门禁：任一文件 dropped_objects 非空 → exit 1（DoD-7）
    dropped_total = sum(len(r["dropped_objects"]) for r in results)
    dropped_entries = [r for r in results if r["dropped_objects"]]

    print("-" * 70)
    print(f"转换完成: {ok}/{len(results)} 成功, 警告 {warn} 条")
    if dropped_total:
        print(f"[P1-2 门禁] 检出 {dropped_total} 个静默丢失对象:")
        for r in dropped_entries:
            print(f"  - {r['source']}: {r['dropped_objects']}")
    print(f"报告: {report_path.relative_to(REPO_ROOT)}")

    if ok != len(results):
        return 2
    if dropped_total:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
