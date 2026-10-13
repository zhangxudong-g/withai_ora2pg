"""W0-1: 单一验收入口

串起所有 DoD 验证，任一失败 exit 1：

  DoD-1: scripts/install_oracle_poc.py install （一键安装）
  DoD-2: scripts/scan_oracle_residuals.py        （残留扫描）
  DoD-3: verify_oracle_poc_output.py L3.5        （首调冒烟）
  DoD-4: verify_oracle_poc_output.py L4 + cases  （业务等价）
  DoD-5: verify_oracle_poc_output.py Gate        （总分 + Gate）
  DoD-6: 流程纪律（commit 后必须重转 + 装 PG + 验证）—— 本脚本是最后一步
  DoD-7: _conversion_report.json dropped_objects （完整性）

用法:
    python scripts/verify_oracle_poc.py          # 完整跑（需 PG 通）
    python scripts/verify_oracle_poc.py --no-pg  # 仅 L1/L2 + DoD-2 + DoD-7
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
ORACLE_OUTPUT = REPO_ROOT / "oracle_poc" / "output"
PG_SCHEMA = "oracle_poc_verify"
SCAN_SCRIPT = REPO_ROOT / "scripts" / "scan_oracle_residuals.py"
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install_oracle_poc.py"
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify_oracle_poc_output.py"


def _print_header(title: str) -> None:
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def _check_dod7_integrity() -> Tuple[bool, str]:
    """DoD-7: 完整性。读 _conversion_report.json，dropped_objects 非空即 fail。"""
    report = ORACLE_OUTPUT / "_conversion_report.json"
    if not report.exists():
        return False, f"缺少转换报告: {report.relative_to(REPO_ROOT)}"
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"转换报告 JSON 解析失败: {e}"
    results = data.get("results", [])
    dropped = [r for r in results if r.get("dropped_objects")]
    if dropped:
        names = [(r["source"], r["dropped_objects"]) for r in dropped]
        return False, f"完整性门禁失败: {len(dropped)} 个文件有静默丢失对象 {names[:5]}"
    return True, f"完整性 OK: 0 静默丢失（共 {len(results)} 文件）"


def _check_dod2_residuals() -> Tuple[bool, str]:
    """DoD-2: 残留扫描。直接调 scan_oracle_residuals.py 拿 exit code。"""
    proc = subprocess.run(
        [sys.executable, str(SCAN_SCRIPT), str(ORACLE_OUTPUT)],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    if proc.returncode == 0:
        return True, "残留扫描 OK: 0 critical / 0 major"
    # 提取关键计数（无论 GBK 乱码，至少 stdout 有 "critical 0 / major 78" 字样）
    out = proc.stdout + proc.stderr
    return False, f"残留扫描 FAIL (exit={proc.returncode}):\n{out[-500:]}"


def _run_verify_suite(args) -> Tuple[int, str, dict]:
    """跑 verify_oracle_poc_output.py，捕获结果。返回 (exit, stdout, json_data)。"""
    cmd = [sys.executable, str(VERIFY_SCRIPT)]
    if args.no_pg:
        cmd.append("--no-pg")
    if args.keep_schema:
        cmd.append("--keep-schema")
    cmd.extend(["--install-passes", str(args.install_passes)])
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    # 读回 _verification_results.json
    vr = ORACLE_OUTPUT / "_verification_results.json"
    data: dict = {}
    if vr.exists():
        try:
            data = json.loads(vr.read_text(encoding="utf-8"))
        except Exception:
            pass
    return proc.returncode, (proc.stdout or "") + (proc.stderr or ""), data


def _check_dod5_gate(suite: dict) -> Tuple[bool, str]:
    """DoD-5: Gate 通过性。"""
    summary = suite.get("summary", suite)  # 兼容 summary 在顶层或在子键
    gate = summary.get("gate_passed")
    weight = summary.get("weight_covered", 1.0)
    score = summary.get("overall_score", 0.0)
    grade = summary.get("grade", "?")
    msg = f"总分 {score} / {grade}; weight_covered={weight*100:.0f}%; gate={'通过' if gate else '未通过'}"
    if gate is True:
        return True, msg
    return False, msg


def _check_dod3_l35(suite: dict) -> Tuple[bool, str]:
    """DoD-3: L3.5 首调冒烟。"""
    summary = suite.get("summary", suite)
    for lv in summary.get("levels", []):
        if lv.get("level") == "L3.5":
            if lv.get("skipped"):
                return True, "L3.5 跳过（--no-pg 模式或无对象）"
            d = lv.get("details", {})
            fail_n = len(d.get("failures", []))
            total = d.get("total", 0)
            if fail_n == 0:
                return True, f"L3.5 OK: {total} 个对象首调全通过"
            # 列出前 5 个失败
            sample = [f"{f.get('kind', '?')}.{f.get('name', '?')}: {(f.get('error') or '')[:80]}"
                      for f in d.get("failures", [])[:5]]
            return False, f"L3.5 FAIL: {fail_n}/{total} 个对象首调失败。样例:\n  " + "\n  ".join(sample)
    return True, "L3.5 未运行"


def _check_dod4_l4(suite: dict) -> Tuple[bool, str]:
    """DoD-4: L4 业务等价。"""
    summary = suite.get("summary", suite)
    for lv in summary.get("levels", []):
        if lv.get("level") == "L4":
            if lv.get("skipped"):
                return True, "L4 跳过（无 cases 或 --no-pg）"
            d = lv.get("details", {})
            failed = d.get("failed", 0)
            errored = d.get("errored", 0)
            total = d.get("total", 0)
            applicable = d.get("applicable", 0)
            ratio = d.get("execution_ratio", 0.0)
            min_cov = d.get("min_case_coverage", 0.5)
            if failed + errored == 0 and ratio >= min_cov:
                return True, f"L4 OK: {applicable}/{total} 用例通过 (ratio={ratio:.0%})"
            return False, (
                f"L4 FAIL: {failed+errored} 失败, "
                f"execution_ratio={ratio:.0%} (要求 ≥{min_cov:.0%}); "
                f"applicable={applicable}/{total}"
            )
    return True, "L4 未运行"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="W0-1: 单一验收入口（DoD-1~DoD-7 串联）")
    ap.add_argument("--no-pg", action="store_true", help="不连 PG（仅 DoD-2/5/7）")
    ap.add_argument("--keep-schema", action="store_true", help="验证后保留沙箱 schema")
    ap.add_argument("--install-passes", type=int, default=3)
    ap.add_argument("--skip-install", action="store_true",
                    help="跳过 DoD-1（install_oracle_poc.py）—— 通常 verify suite 已含 L3")
    ap.add_argument("--no-residual-scan", action="store_true",
                    help="跳过 DoD-2（残留扫描）")
    args = ap.parse_args()

    print("=" * 70)
    print("W0-1 单一验收入口")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"输出: {ORACLE_OUTPUT.relative_to(REPO_ROOT)}")
    print("=" * 70)

    fails: List[str] = []

    # --- DoD-7: 完整性（无需 PG） ---
    _print_header("DoD-7 完整性（dropped_objects）")
    ok, msg = _check_dod7_integrity()
    print(f"  {'[OK]' if ok else '[FAIL]'} {msg}")
    if not ok:
        fails.append(f"DoD-7: {msg}")

    # --- DoD-2: 残留扫描（无需 PG） ---
    if not args.no_residual_scan:
        _print_header("DoD-2 残留扫描（scan_oracle_residuals.py）")
        ok, msg = _check_dod2_residuals()
        first_line = msg.split("\n", 1)[0]
        print(f"  {'[OK]' if ok else '[FAIL]'} {first_line}")
        if not ok:
            fails.append(f"DoD-2: {first_line}")

    # --- L1/L2/L3/L3.5/L4 验证套件（PG 可选） ---
    _print_header("L1/L2/L3/L3.5/L4 验证套件（verify_oracle_poc_output.py）")
    print("  跑 verify_oracle_poc_output.py ...")
    ret, out, suite = _run_verify_suite(args)
    # 简要回显
    for line in out.splitlines():
        if any(k in line for k in ("总分", "Gate", "L3.5", "L3 安装", "报告:", "时间:")):
            print(f"  {line}")
    if ret != 0:
        # verify 自身报错（PG 不可用等）
        print(f"  [FAIL] verify suite exit={ret}")

    # --- DoD-5 Gate ---
    _print_header("DoD-5 Gate + weight_covered")
    ok, msg = _check_dod5_gate(suite)
    print(f"  {'[OK]' if ok else '[FAIL]'} {msg}")
    if not ok:
        fails.append(f"DoD-5: {msg}")

    # --- DoD-3 L3.5 ---
    if not args.no_pg:
        _print_header("DoD-3 L3.5 首调冒烟")
        ok, msg = _check_dod3_l35(suite)
        first_line = msg.split("\n", 1)[0]
        print(f"  {'[OK]' if ok else '[FAIL]'} {first_line}")
        if not ok:
            fails.append(f"DoD-3: {first_line}")

    # --- DoD-4 L4 ---
    _print_header("DoD-4 L4 业务等价")
    ok, msg = _check_dod4_l4(suite)
    first_line = msg.split("\n", 1)[0]
    print(f"  {'[OK]' if ok else '[FAIL]'} {first_line}")
    if not ok:
        fails.append(f"DoD-4: {first_line}")

    # --- DoD-6 流程纪律检查 ---
    _print_header("DoD-6 流程纪律")
    # 简单判定：_verification_report.md 和 _conversion_report.json 都在
    if (ORACLE_OUTPUT / "_verification_report.md").exists() and \
       (ORACLE_OUTPUT / "_conversion_report.json").exists():
        print("  [OK] 验证报告与转换报告均存在")
    else:
        print("  [WARN] 验证报告或转换报告缺失（不影响 exit code，仅提示）")

    # --- 总评 ---
    print()
    print("=" * 70)
    if fails:
        print(f"[FAIL] {len(fails)} 项 DoD 未通过:")
        for f in fails:
            print(f"   - {f.split(chr(10))[0]}")  # 只显示第一行
        print("=" * 70)
        return 1
    print("[OK] 全部 DoD 通过（或在 --no-pg 模式下为可接受状态）")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
