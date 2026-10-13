"""
workspace 主入口

使用 perlstyle 引擎转换 Oracle -> PG，并使用独立 verification 框架验证。

用法:
    cd D:/JIP/poc/workspace
    python pipeline.py                       # 完整流程
    python pipeline.py convert               # 仅转换
    python pipeline.py verify                # 仅验证
    python pipeline.py report                # 仅生成报告
    python pipeline.py clean                 # 清理输出

依赖:
    - perlstyle 转换: ora2pg_py_perlstyle/
    - 验证框架: workspace/verification/ (独立)
    - 无 AI
    - 不修改 test_reports / pipeline 目录
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

# 添加 workspace 根到 sys.path
_WORKSPACE = Path(__file__).resolve().parent
if str(_WORKSPACE) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE))

from config import (
    INPUT_DIR,
    OUTPUT_DIR,
    RESULTS_DIR,
    LATEST_DIR,
    PG_TEST_SCHEMA,
    SQL_PATTERNS,
    ensure_dirs,
)
from converter import convert_file, convert_files, detect_type, summarize as convert_summarize


def clean_outputs(verbose: bool = True) -> dict:
    """清理输出目录"""
    stats = {"output_files": 0, "result_files": 0, "bytes_freed": 0}
    for d in (OUTPUT_DIR, RESULTS_DIR):
        if d.exists():
            for f in d.rglob("*"):
                if f.is_file():
                    try:
                        stats["bytes_freed"] += f.stat().st_size
                        stats["output_files" if d == OUTPUT_DIR else "result_files"] += 1
                    except OSError:
                        pass
            shutil.rmtree(d)
            if verbose:
                print(f"  清理: {d}")
    ensure_dirs()
    return stats


def run_convert(args) -> int:
    """转换步骤"""
    print("\n[1/3] 转换 (perlstyle 引擎)")
    print("-" * 60)

    # 收集源文件
    sources = list(INPUT_DIR.rglob("*.sql")) + list(INPUT_DIR.rglob("*.SQL"))
    if args.recursive:
        for pat in SQL_PATTERNS[2:]:  # pks, pkb, fnc, prc, trg, vw
            sources.extend(INPUT_DIR.rglob(pat))
    sources = sorted(set(sources))

    if not sources:
        print(f"未找到源文件: {INPUT_DIR}")
        return 1

    print(f"  输入目录: {INPUT_DIR}")
    print(f"  输出目录: {OUTPUT_DIR}")
    print(f"  待处理: {len(sources)} 个文件")

    results = convert_files(sources, OUTPUT_DIR)

    # 保存原始结果
    raw_path = LATEST_DIR / "conversion_results.json"
    raw_path.write_text(
        json.dumps([r.to_dict() for r in results], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    summary = convert_summarize(results)
    print(f"\n  转换完成: {summary['success']}/{summary['total']}")
    print(f"  按类型:")
    for t, d in sorted(summary["by_type"].items()):
        print(f"    {t}: {d['success']}/{d['total']}")
    print(f"  耗时: {summary['total_duration_ms']} ms")
    print(f"  结果: {raw_path}")
    return 0


def run_verify(args) -> int:
    """验证步骤"""
    print("\n[2/3] 验证 (4-Level 验证框架)")
    print("-" * 60)

    from verification import (
        VerificationConfig,
        Verifier,
        Artifact,
        load_cases,
        render_suite,
        write_json,
        write_text,
    )

    # 读取转换结果
    results_path = LATEST_DIR / "conversion_results.json"
    if not results_path.exists():
        print("错误: 请先运行转换步骤")
        return 1

    data = json.loads(results_path.read_text(encoding="utf-8"))
    samples = [r for r in data if r.get("success")][:args.max_verify]
    if not samples:
        print("没有可验证的样本")
        return 1

    # 加载用例
    cases = []
    if args.cases:
        from pathlib import Path as _P
        cp = _P(args.cases)
        if cp.exists():
            cases = load_cases(cp)
            print(f"  金标准用例: {cp} ({len(cases)} 条)")
        else:
            print(f"  用例库不存在: {cp} (L4 将跳过)")

    # 构建制品
    artifacts = []
    for r in samples:
        p = Path(r["output"])
        code = p.read_text(encoding="utf-8", errors="replace") if p and p.exists() else ""
        artifacts.append(Artifact(Path(r["source"]).name, code))

    print(f"  验证 {len(artifacts)} 个样本 (PG={'on' if not args.no_pg else 'off'})")

    # 跑验证
    cfg = VerificationConfig(
        use_pg=not args.no_pg,
        schema=PG_TEST_SCHEMA,
        drop_schema_on_exit=not args.keep_schema,
    )
    with Verifier(cfg) as v:
        if cfg.use_pg and v._ensure_sandbox() is None:
            print(f"  PG 不可用: {v._pg_error}")
            print(f"  降级为仅 L1/L2")
        suite = v.verify_suite("workspace", artifacts, cases=cases)

    # 落盘
    write_json(suite, LATEST_DIR / "verification_results.json")
    write_text(render_suite(suite), LATEST_DIR / "verification_report.md")
    write_json([r.to_dict() for r in suite.reports],
               LATEST_DIR / "verification_reports_flat.json")

    s = suite.summary
    print(f"\n  总分: {s.get('overall_score')} - {s.get('grade')}")
    print(f"  Gate: {'通过' if s.get('gate_passed') else '未通过'}")
    for f in (s.get("gate_failures") or [])[:10]:
        print(f"    - {f}")
    return 0 if s.get("gate_passed") or not args.fail_on_gate else 1


def run_report(args) -> int:
    """生成汇总报告"""
    print("\n[3/3] 生成汇总报告")
    print("-" * 60)

    from verification import write_text

    conv_path = LATEST_DIR / "conversion_results.json"
    ver_path = LATEST_DIR / "verification_results.json"
    ver_md = LATEST_DIR / "verification_report.md"

    lines = []
    lines.append("# Workspace Pipeline 报告")
    lines.append("")
    lines.append(f"- 生成时间: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- 工作目录: {_WORKSPACE}")
    lines.append("")

    if conv_path.exists():
        data = json.loads(conv_path.read_text(encoding="utf-8"))
        total = len(data)
        ok = sum(1 for r in data if r.get("success"))
        lines.append("## 转换结果")
        lines.append("")
        lines.append(f"- 总文件: {total}")
        lines.append(f"- 成功: {ok}")
        lines.append(f"- 失败: {total - ok}")
        lines.append("")

    if ver_md.exists():
        lines.append("## 验证报告")
        lines.append("")
        lines.append(ver_md.read_text(encoding="utf-8"))

    out = LATEST_DIR / "summary.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"  报告: {out}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Workspace Pipeline (perlstyle + verification)")
    parser.add_argument("step", nargs="?", default="all",
                        choices=["all", "convert", "verify", "report", "clean"],
                        help="执行的步骤")
    parser.add_argument("--max-verify", type=int, default=20)
    parser.add_argument("--cases", default="", help="金标准用例库 JSON")
    parser.add_argument("--no-pg", action="store_true", help="不连 PG (仅 L1/L2)")
    parser.add_argument("--keep-schema", action="store_true")
    parser.add_argument("--no-clean", action="store_true", help="跳过清理")
    parser.add_argument("--fail-on-gate", action="store_true")
    parser.add_argument("--recursive", action="store_true", default=True)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print("Workspace Pipeline (perlstyle + 4-Level verification)")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    ensure_dirs()

    if args.step == "clean":
        clean_outputs(verbose=not args.quiet)
        return 0

    # 清理
    if not args.no_clean and args.step in ("all", "convert"):
        clean_outputs(verbose=not args.quiet)

    steps = ["convert", "verify", "report"] if args.step == "all" else [args.step]
    rc = 0
    for s in steps:
        if s == "convert":
            rc = run_convert(args) or rc
        elif s == "verify":
            rc = run_verify(args) or rc
        elif s == "report":
            rc = run_report(args) or rc

    print("\n" + "=" * 60)
    print(f"完成 (rc={rc})")
    print("=" * 60)
    return rc


if __name__ == "__main__":
    sys.exit(main())
