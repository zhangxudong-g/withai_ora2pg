"""P1-3: oracle_poc 产物残留扫描 CLI（DoD-2 硬门禁）。

用法:
    python scripts/scan_oracle_residuals.py                  # 扫 oracle_poc/output
    python scripts/scan_oracle_residuals.py <目录>          # 扫任意目录
    python scripts/scan_oracle_residuals.py --json out.json  # 输出 JSON 报告

退出码:
    0  critical=0 且 major=0（DoD-2 通过）
    1  critical>0 或 major>0（硬门禁失败）
    2  参数错误 / 目录不存在
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = REPO_ROOT / "workspace"
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

from verification.residuals import scan_directory  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="oracle_poc 产物残留扫描（DoD-2 硬门禁）")
    ap.add_argument("target", nargs="?", default=str(REPO_ROOT / "oracle_poc" / "output"),
                    help="待扫描目录（默认 oracle_poc/output）")
    ap.add_argument("--json", dest="json_out", default=None, help="输出 JSON 报告到指定路径")
    args = ap.parse_args()

    target = Path(args.target).resolve()
    if not target.is_dir():
        print(f"错误: 目录不存在: {target}", file=sys.stderr)
        return 2

    report = scan_directory(target)

    print("=" * 70)
    print(f"残留扫描: {target}")
    print("=" * 70)
    print(f"扫描文件数 : {report.files_scanned}")
    print(f"critical   : {report.critical_count}")
    print(f"major      : {report.major_count}")
    print(f"minor      : {report.minor_count}")
    print(f"total      : {report.total_hits}")
    print()

    if report.hits:
        # 按文件分组打印
        from collections import defaultdict
        by_file: dict[str, list] = defaultdict(list)
        for h in report.hits:
            by_file[h.filename].append(h)
        for fname in sorted(by_file):
            hits = by_file[fname]
            print(f"  {fname}  ({len(hits)} hits)")
            for h in hits:
                sev = h.severity[0].upper()
                print(f"    [{sev}] L{h.line_no:<4} {h.rule_name}: {h.snippet}")

    print()
    if report.ok:
        print("[PASS] 门禁通过 (critical=0, major=0)")
    else:
        print("[FAIL] 门禁失败 (DoD-2 要求 major=0)")

    if args.json_out:
        out = Path(args.json_out).resolve()
        out.write_text(json.dumps({
            "files_scanned": report.files_scanned,
            "critical": report.critical_count,
            "major": report.major_count,
            "minor": report.minor_count,
            "total": report.total_hits,
            "ok": report.ok,
            "hits": [h.__dict__ for h in report.hits],
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"JSON 报告: {out}")

    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
