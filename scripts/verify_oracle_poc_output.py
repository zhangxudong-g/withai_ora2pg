"""
oracle_poc/output/ 转换产物的 4-Level 验证

复用 workspace/verification/ 框架 (L1 静态 / L2 pglast 语法 / L3 PG 沙箱安装),
对 oracle_poc/output/ 下所有 PG 文件做真实 PostgreSQL 安装验证。

用法:
    python scripts/verify_oracle_poc_output.py
    python scripts/verify_oracle_poc_output.py --no-pg        # 仅 L1/L2
    python scripts/verify_oracle_poc_output.py --keep-schema  # 保留沙箱 schema

输出:
    oracle_poc/output/_verification_report.md
    oracle_poc/output/_verification_results.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = REPO_ROOT / "workspace"
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

from verification import (  # noqa: E402
    Artifact,
    VerificationConfig,
    Verifier,
    render_suite,
    write_json,
    write_text,
)

OUT_DIR = REPO_ROOT / "oracle_poc" / "output"
PG_SCHEMA = "oracle_poc_verify"

# 依赖顺序: 表 -> 序列 -> 类型 -> 视图 -> 触发器 -> 函数 -> 过程 -> 包 -> 同义词 -> 数据
FILE_ORDER = [
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

# 驱动脚本含 psql 元命令, 不参与验证
SKIP = {"00_CREATE_ALL_PG.sql"}


def collect_artifacts() -> list[Artifact]:
    files = [
        p for p in OUT_DIR.rglob("*.sql")
        if p.is_file() and p.name not in SKIP
    ]

    def rank(p: Path) -> tuple:
        top = p.relative_to(OUT_DIR).parts[0]
        try:
            return (FILE_ORDER.index(top), p.name)
        except ValueError:
            return (len(FILE_ORDER), p.name)

    return [Artifact(str(p.relative_to(OUT_DIR).as_posix()), p.read_text(encoding="utf-8"))
            for p in sorted(files, key=rank)]


def main() -> int:
    ap = argparse.ArgumentParser(description="验证 oracle_poc/output/ 转换产物")
    ap.add_argument("--no-pg", action="store_true", help="不连 PG (仅 L1/L2)")
    ap.add_argument("--keep-schema", action="store_true", help="验证后保留沙箱 schema")
    ap.add_argument("--install-passes", type=int, default=3, help="安装趟数")
    ap.add_argument("--cases-file", type=str, default=None,
                    help="L4 golden cases JSON (默认 workspace/verification/golden/oracle_poc_cases.json)")
    args = ap.parse_args()

    if not OUT_DIR.is_dir():
        print(f"错误: 输出目录不存在: {OUT_DIR}\n先运行 scripts/convert_oracle_poc.py")
        return 1

    artifacts = collect_artifacts()
    if not artifacts:
        print(f"错误: 未找到待验证文件: {OUT_DIR}")
        return 1

    print("=" * 70)
    print("oracle_poc/output 验证 (L1 静态 / L2 语法 / L3 PG 安装 / L3.5 首调冒烟)")
    print(f"制品数 : {len(artifacts)}")
    print(f"PG 验证: {'关闭' if args.no_pg else '开启'}")
    print("=" * 70)

    cfg = VerificationConfig(
        use_pg=not args.no_pg,
        schema=PG_SCHEMA,
        drop_schema_on_exit=not args.keep_schema,
        install_passes=args.install_passes,
        cases_file=args.cases_file,
    )
    if cfg.cases_file is None:
        # 默认指向 workspace 路径
        default_cases = WORKSPACE / "verification" / "golden" / "oracle_poc_cases.json"
        if default_cases.exists():
            cfg = VerificationConfig(
                use_pg=cfg.use_pg,
                schema=cfg.schema,
                drop_schema_on_exit=cfg.drop_schema_on_exit,
                install_passes=cfg.install_passes,
                cases_file=str(default_cases),
            )
            print(f"[W0-5] 自动加载默认 cases: {default_cases.relative_to(REPO_ROOT)}")

    with Verifier(cfg) as v:
        if cfg.use_pg and v._ensure_sandbox() is None:
            print(f"  PG 不可用: {v._pg_error}")
            print("  降级为仅 L1/L2")
        # W0-5: 接 cases_file。如果 cfg.cases_file 已设，verify_suite
        # 内部会调 load_cases(cases_file) 拿到用例。
        suite = v.verify_suite("oracle_poc", artifacts, cases=None)

    write_json(suite, OUT_DIR / "_verification_results.json")
    write_text(render_suite(suite), OUT_DIR / "_verification_report.md")

    s = suite.summary
    print("-" * 70)
    print(f"总分: {s.get('overall_score')} - {s.get('grade')}")
    print(f"Gate: {'通过' if s.get('gate_passed') else '未通过'}")
    for f in (s.get("gate_failures") or [])[:20]:
        print(f"  - {f}")

    # L3.5 首调冒烟明细 (DoD-3: 能安装 ≠ 可用)
    l35 = next((lv for lv in s.get("levels", []) if lv.get("level") == "L3.5"), None)
    if l35 and not l35.get("skipped"):
        det = l35["details"]
        bs = det.get("by_status", {})
        print("-" * 70)
        print(f"L3.5 首调冒烟: 总计 {det.get('total', 0)}  "
              f"通过 {bs.get('pass', 0)}  失败 {bs.get('fail', 0)}  "
              f"受阻 {bs.get('blocked', 0)}  未触发 {bs.get('not_fired', 0)}")
        for f in det.get("failures", [])[:20]:
            err = (f.get("error") or "").strip().replace("\n", " ")[:200]
            print(f"  - [{f.get('kind', '?')}] {f.get('schema', '?')}.{f.get('name', '?')}")
            print(f"      {err}")

    # L3 安装失败明细 (定位用)
    l3 = next((lv for lv in s.get("levels", []) if lv.get("level") == "L3"), None)
    if l3 and l3.get("details"):
        d = l3["details"]
        print("-" * 70)
        print(f"L3 安装: 成功 {d.get('installed', 0)} / 总计 {d.get('total', 0)}")
        for label, g in (d.get("per_artifact") or {}).items():
            bad = g.get("failures") or []
            if bad:
                print(f"\n  [{label}] 失败 {len(bad)} / {g.get('total', 0)}:")
                for item in bad[:25]:
                    if isinstance(item, dict):
                        err = (item.get("error") or "").strip().replace("\n", " ")[:300]
                        print(f"    - {item.get('object_type', '?'):<12} "
                              f"{str(item.get('name', '?'))[:70]}")
                        print(f"      {err}")
                    else:
                        print(f"    - {item}")

    print("-" * 70)
    print(f"报告: {(OUT_DIR / '_verification_report.md').relative_to(REPO_ROOT)}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
