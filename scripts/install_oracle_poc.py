#!/usr/bin/env python3
r"""
Defect-2 (D0) 修复：per-file 安装器

解决问题：
  psql 的 ON_ERROR_STOP 是会话级变量，\ir 嵌套子文件重设无效；
  产物 00_CREATE_ALL_PG.sql 任意子文件错都会令后续文件"假装装好"，
  导致 L3 报告假成功、DoD-1 不能真验证。

策略：
  - 按 00_CREATE_ALL_PG.sql 安装顺序，每个子文件单独 psql -f 跑
  - 单文件错立即停 + exit 非零（DoD-6 流程纪律可观测）
  - 默认装到 oracle_poc_verify schema（Defect-1）
  - 提供 drop 子命令卸载

用法：
  python scripts/install_oracle_poc.py install --schema my_poc
  python scripts/install_oracle_poc.py drop   --schema my_poc

依赖：
  - psql 在 PATH 或 PSQL_BIN 环境变量
  - PGPASSWORD 环境变量，或自动读取 .pgpass
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DST = REPO_ROOT / "oracle_poc" / "output"
DEFAULT_SCHEMA = "oracle_poc_verify"
DEFAULT_PSQL = os.environ.get(
    "PSQL_BIN",
    r"C:\Program Files\PostgreSQL\15\bin\psql.exe" if os.name == "nt" else "psql",
)
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


def find_psql(explicit: str) -> str:
    """Locate psql binary, in this order: explicit, PSQL_BIN, default, PATH."""
    default = DEFAULT_PSQL
    for candidate in [explicit, os.environ.get("PSQL_BIN"), default]:
        if candidate and (Path(candidate).exists() if "/" in candidate or "\\" in candidate else shutil.which(candidate)):
            return candidate
    raise FileNotFoundError(
        "psql not found. Set PSQL_BIN env or add PostgreSQL bin/ to PATH"
    )


def ordered_sql_files(dst_dir: Path) -> list[Path]:
    """按 INSTALL_ORDER 返回所有 .sql 产物文件路径。"""
    out: list[Path] = []
    for sub in INSTALL_ORDER:
        sub_dir = dst_dir / sub
        if sub_dir.is_dir():
            out.extend(sorted(sub_dir.glob("*.sql")))
    return out


def run_psql(args: argparse.Namespace, sql_file: Path) -> tuple[int, str]:
    """单文件跑 psql，返回 (exit_code, stderr)。"""
    # 子文件不含 SET search_path，需在每个调用前用 -c 设（避免装公私）
    set_search_path = f"SET search_path TO {args.schema};"
    cmd = [
        args.psql,
        "-h", args.host,
        "-p", str(args.port),
        "-U", args.user,
        "-d", args.dbname,
        "-X",  # 不要 .psqlrc
        "-q",      # quiet
        "-v", "ON_ERROR_STOP=on",
        "-c", set_search_path,
        "-f", str(sql_file),
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PGPASSWORD": args.password} if args.password else os.environ,
    )
    return proc.returncode, proc.stdout + proc.stderr


def cmd_install(args: argparse.Namespace) -> int:
    dst = Path(args.dst).resolve()
    if not dst.is_dir():
        print(f"[FAIL] 产物目录不存在: {dst}")
        return 2

    files = ordered_sql_files(dst)
    if not files:
        print(f"[FAIL] {dst} 下无 SQL 产物")
        return 2

    # 首建 schema
    schema_setup = (
        f"CREATE SCHEMA IF NOT EXISTS {args.schema};\n"
        f"SET search_path TO {args.schema};\n"
    )
    print(f"[setup] CREATE SCHEMA {args.schema}")
    cmd = [
        args.psql, "-h", args.host, "-p", str(args.port), "-U", args.user,
        "-d", args.dbname, "-X", "-c", schema_setup,
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PGPASSWORD": args.password} if args.password else os.environ,
    )
    if proc.returncode != 0:
        print(f"[FAIL] 建 schema 失败:\n{proc.stderr}")
        return proc.returncode

    # per-file install
    failed: list[tuple[Path, int, str]] = []
    for i, f in enumerate(files, 1):
        rel = f.relative_to(dst).as_posix()
        print(f"[{i:2d}/{len(files)}] {rel}", end=" ... ", flush=True)
        rc, out = run_psql(args, f)
        if rc == 0:
            print("OK")
        else:
            err_lines = [ln for ln in out.splitlines() if "错误" in ln or "ERROR" in ln][:3]
            print(f"FAIL (exit={rc})")
            for ln in err_lines:
                print(f"    {ln.strip()}")
            failed.append((f, rc, out))
            if not args.keep_going:
                print()
                print(f"[STOP] 子文件 {rel} 失败，按 Defect-2 策略立即停止（后续文件未跑）")
                break

    print()
    if failed:
        print(f"[RESULT] {len(failed)} 文件失败")
        for f, rc, out in failed:
            print(f"  - {f.relative_to(dst).as_posix()}  exit={rc}")
        return 1

    print(f"[RESULT] 全部 {len(files)} 文件安装成功，schema={args.schema}")
    return 0


def cmd_drop(args: argparse.Namespace) -> int:
    """DROP schema CASCADE."""
    print(f"[drop] DROP SCHEMA {args.schema} CASCADE")
    sql = f"DROP SCHEMA IF EXISTS {args.schema} CASCADE;\n"
    cmd = [
        args.psql, "-h", args.host, "-p", str(args.port), "-U", args.user,
        "-d", args.dbname, "-X", "-c", sql,
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PGPASSWORD": args.password} if args.password else os.environ,
    )
    if proc.returncode != 0:
        print(f"[FAIL] {proc.stderr}")
        return proc.returncode
    print("[RESULT] 已 DROP")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="oracle_poc per-file installer (Defect-1/2 修复)",
    )
    ap.add_argument("--psql", default=None, help="psql 路径 (默认 $PSQL_BIN 或 PATH)")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", default=5432)
    ap.add_argument("--user", default="postgres")
    ap.add_argument("--dbname", default="postgres")
    ap.add_argument("--password", default=None, help="PG 密码（建议 PGPASSWORD 环境变量）")
    ap.add_argument("--schema", default=DEFAULT_SCHEMA)
    ap.add_argument("--dst", default=str(DEFAULT_DST), help="产物目录")
    ap.add_argument("--keep-going", dest="keep_going", action="store_true",
                    help="错不停（默认：错立即停）")
    sub_cmd = ap.add_subparsers(dest="cmd", required=True)

    sub_cmd.add_parser("install", help="按 INSTALL_ORDER 顺序装所有产物文件")
    sub_cmd.add_parser("drop", help="DROP schema CASCADE")

    args, _unknown = ap.parse_known_args()
    try:
        args.psql = find_psql(args.psql)
    except FileNotFoundError as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        return 2

    if args.cmd == "install":
        return cmd_install(args)
    if args.cmd == "drop":
        return cmd_drop(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())