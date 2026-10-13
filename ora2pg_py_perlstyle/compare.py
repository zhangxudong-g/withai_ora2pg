#!/usr/bin/env python3
"""对比测试：原版 ora2pg_py vs Perl 风格重写

对 GKB 全部类型样本运行两种实现并产出对比报告。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ORACLE2PG_DIR = Path(r"D:/JIP/poc/ora2pg_py")
PERL_DIR = Path(r"D:/JIP/poc/ora2pg_py_perlstyle")
TEST_ROOT = Path(r"D:/JIP/poc/test_reports/ora2pg_py_gkb_test")
UTF8_DIR = TEST_ROOT / "utf8_inputs"
OUT_ORIG = TEST_ROOT / "outputs"
OUT_PERL = TEST_ROOT / "outputs_perlstyle"
PYTHON = sys.executable

SAMPLES: Dict[str, Dict[str, str]] = {
    "TABLE": {
        "simple": "TABLE/GKBTBKLOG.SQL",
        "complex": "TABLE/GKBTGAKUREIBO.SQL",
    },
    "VIEW": {
        "simple": "VIEW/GKBVEUCSEIJIN.SQL",
        "complex": "VIEW/GKBVEUCGAKUREIBO.SQL",
    },
    "FUNCTION": {
        "simple": "FUNCTION/GKBFKIDOJYGET.SQL",
        "complex": "FUNCTION/GKBFKHMCTRL.SQL",
    },
    "PROCEDURE": {
        "simple": "PROCEDURE/GKBSKGYMSHKK.SQL",
        "complex": "PROCEDURE/GKBSKIDOTCH.SQL",
    },
    "PACKAGE": {
        "simple": "PACKAGE/GKBPB00320.SQL",
        "complex": "PACKAGE/GKBPK00010.SQL",
    },
    "PACKAGE_BODY": {
        "simple": "PACKAGE_BODY/GKBPA00081B.SQL",
        "complex": "PACKAGE_BODY/GKBPC00120B.SQL",
    },
    "TYPE": {
        "simple": "TYPE/GKBEKENSU_LIST.SQL",
        "complex": "TYPE/GKBEKENSU_RECORD.SQL",
    },
    "DATA": {
        "simple": "DATA/Del_Data.sql",
        "complex": "DATA/INS_GKBTTSUCHISHOKANRI.SQL",
    },
}


RESIDUAL_PATTERNS: List[Tuple[str, str]] = [
    (r"\bVARCHAR2\b(?!\s*\()", "VARCHAR2"),
    (r"\bNVARCHAR2\b(?!\s*\()", "NVARCHAR2"),
    (r"\bNUMBER\s*\(", "NUMBER("),
    (r"\(\+\)", "(+) outer join"),
    (r"\bSYSDATE\b", "SYSDATE"),
    (r"\bNVL\s*\(", "NVL("),
    (r"\bDECODE\s*\(", "DECODE("),
    (r"\bDBMS_OUTPUT\.", "DBMS_OUTPUT."),
    (r"\bRAWTOHEX\s*\(", "RAWTOHEX"),
    (r"\bHEXTORAW\s*\(", "HEXTORAW"),
    (r"\bTO_DATE\s*\(", "TO_DATE"),
    (r"\bTO_CHAR\s*\(", "TO_CHAR"),
    (r"\bTO_NUMBER\s*\(", "TO_NUMBER"),
    (r"\bADD_MONTHS\s*\(", "ADD_MONTHS"),
    (r"\bLAST_DAY\s*\(", "LAST_DAY"),
    (r"\bINSTR\s*\(", "INSTR"),
    (r"\bSUBSTR\s*\(", "SUBSTR"),
    (r"\bREGEXP_LIKE\s*\(", "REGEXP_LIKE"),
    (r"\bCONNECT\s+BY\b", "CONNECT BY"),
    (r"\bROWNUM\b", "ROWNUM"),
    (r"\bSQLCODE\b", "SQLCODE"),
    (r"\bSQLERRM\b", "SQLERRM"),
    (r"\bMERGE\s+INTO\b", "MERGE INTO"),
    (r"\bPARTITION\s+BY\b", "PARTITION BY"),
    (r"\bBULK\s+COLLECT\b", "BULK COLLECT"),
    (r"\bFORALL\b", "FORALL"),
    (r"\bSYS_REFCURSOR\b", "SYS_REFCURSOR"),
    (r"\bRAISE_APPLICATION_ERROR\b", "RAISE_APPLICATION_ERROR"),
]


def _strip_strings_and_comments(text: str) -> str:
    """Replace string literals and comment lines with blanks of equal length.

    This prevents Oracle functions embedded in dynamic SQL strings (e.g.
    ``VSQLSEL := '... NVL(x, 0) ...'``) and commented-out code from being
    counted as conversion residuals.
    """
    # 1. Strip line comments (--...) and block comments (/* ... */)
    text = re.sub(r'--[^\n]*', lambda m: ' ' * len(m.group(0)), text)
    text = re.sub(r'/\*.*?\*/', lambda m: ' ' * len(m.group(0)), text, flags=re.DOTALL)
    # 2. Strip single-quoted string literals (handle '' as escaped quote)
    text = re.sub(r"'(?:''|[^'])*'", lambda m: "'" + ' ' * (len(m.group(0)) - 2) + "'" if len(m.group(0)) >= 2 else "''", text)
    # 3. Strip dollar-quoted strings (PG $$ ... $$)
    text = re.sub(r'\$\$.*?\$\$', lambda m: ' ' * len(m.group(0)), text, flags=re.DOTALL)
    return text


def check_residuals(text: str) -> List[Dict[str, int]]:
    """Find Oracle patterns that should have been converted.

    Ignore occurrences inside string literals and comments since those
    represent intentional content the converter can't/shouldn't touch.
    """
    stripped = _strip_strings_and_comments(text)
    results = []
    for pat, name in RESIDUAL_PATTERNS:
        n = len(re.findall(pat, stripped, re.IGNORECASE))
        if n > 0:
            results.append({"pattern": name, "count": n})
    return results


def run_converter(input_path: Path, output_path: Path, engine: str) -> Tuple[bool, str, float]:
    """Run a converter; engine is 'original' or 'perlstyle'."""
    if engine == "original":
        cmd = [PYTHON, str(ORACLE2PG_DIR / "ora2pg_py.py"), str(input_path), "-o", str(output_path), "-v"]
    else:
        cmd = [PYTHON, str(PERL_DIR / "perlstyle_engine.py"), str(input_path), "-o", str(output_path), "-v"]
    start = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, encoding="utf-8", errors="replace")
        return proc.returncode == 0, proc.stdout + proc.stderr, time.time() - start
    except Exception as e:
        return False, f"Exception: {e}", time.time() - start


def count_lines(p: Path) -> int:
    if not p.exists():
        return 0
    return p.read_text(encoding="utf-8", errors="ignore").count("\n") + 1


def grade(score: float) -> str:
    if score >= 95:
        return "A"
    if score >= 85:
        return "B"
    if score >= 70:
        return "C"
    if score >= 50:
        return "D"
    return "F"


def calc_accuracy(residuals: List, line_retention: float, has_create: bool, otype: str, properly_split: bool = False) -> Dict:
    # Residual score
    if residuals:
        total = sum(r["count"] for r in residuals)
        residual_score = max(0, 100 - total * 0.5)
    else:
        residual_score = 100

    # Retention score with smarter logic
    retention_score = min(100, line_retention)
    if otype == "PACKAGE_BODY":
        # Properly split PACKAGE BODY has CREATE SCHEMA + multiple CREATE OR REPLACE
        # FUNCTION/PROCEDURE. If properly_split is detected, give full retention credit.
        # The retention cap of 40 was a legacy heuristic for unsplit bodies.
        if properly_split:
            retention_score = min(100, line_retention)
        elif line_retention >= 80:
            retention_score = 40
        else:
            retention_score = min(100, line_retention)
    elif otype in ("FUNCTION", "PROCEDURE"):
        # Body should be present but output may be more compact
        if line_retention < 20:
            retention_score = 40  # body lost
        elif line_retention < 40:
            retention_score = 70
        else:
            # 40% retention is OK (more compact, but body present)
            retention_score = min(100, line_retention + 10)
    elif otype == "PACKAGE":
        # PACKAGE declarations should be present
        if line_retention < 30:
            retention_score = 50
        else:
            retention_score = min(100, line_retention)

    syntax_score = 100 if has_create else 50

    overall = round(
        (residual_score * 0.40)
        + (retention_score * 0.40)
        + (syntax_score * 0.20),
        1,
    )
    return {
        "residual_score": residual_score,
        "retention_score": retention_score,
        "syntax_score": syntax_score,
        "overall": overall,
    }


def process_sample(obj_type: str, flavor: str, rel_path: str) -> Dict:
    src_path = UTF8_DIR / rel_path
    src_text = src_path.read_text(encoding="utf-8", errors="ignore")
    src_lines = src_text.count("\n") + 1

    out_orig_dir = OUT_ORIG / obj_type
    out_perl_dir = OUT_PERL / obj_type
    out_orig_dir.mkdir(parents=True, exist_ok=True)
    out_perl_dir.mkdir(parents=True, exist_ok=True)

    stem = src_path.stem
    orig_out = out_orig_dir / f"{stem}_pg.SQL"
    perl_out = out_perl_dir / f"{stem}_pgperl.SQL"

    record: Dict[str, Any] = {
        "obj_type": obj_type,
        "flavor": flavor,
        "rel_path": rel_path,
        "src_lines": src_lines,
    }

    # Original
    ok, stdout, elapsed = run_converter(src_path, orig_out, "original")
    record["orig"] = {
        "ok": ok,
        "elapsed_s": round(elapsed, 3),
        "lines": count_lines(orig_out),
    }
    if ok:
        txt = orig_out.read_text(encoding="utf-8", errors="ignore")
        if txt.startswith("-- Ora2Py Warnings:"):
            idx = txt.find("\n\n")
            body = txt[idx + 2:] if idx >= 0 else txt
        else:
            body = txt
        record["orig"]["residuals"] = check_residuals(body)
        record["orig"]["has_create"] = bool(re.search(r"\bCREATE\s+(OR\s+REPLACE\s+)?(FUNCTION|PROCEDURE|TABLE|VIEW|TYPE|PACKAGE|SCHEMA)\b", body, re.IGNORECASE))
        # Detect properly split PACKAGE_BODY: has CREATE SCHEMA + multiple CREATE OR REPLACE FUNCTION/PROCEDURE
        if obj_type == "PACKAGE_BODY":
            has_schema = bool(re.search(r"\bCREATE\s+SCHEMA\b", body, re.IGNORECASE))
            create_fn_count = len(re.findall(r"\bCREATE\s+OR\s+REPLACE\s+(FUNCTION|PROCEDURE)\b", body, re.IGNORECASE))
            still_pkg_body = bool(re.search(r"\bCREATE\s+(OR\s+REPLACE\s+)?PACKAGE\s+BODY\b", body, re.IGNORECASE))
            record["orig"]["properly_split"] = (has_schema and create_fn_count >= 2) and not still_pkg_body

    # Perl-style
    ok, stdout, elapsed = run_converter(src_path, perl_out, "perlstyle")
    record["perl"] = {
        "ok": ok,
        "elapsed_s": round(elapsed, 3),
        "lines": count_lines(perl_out),
    }
    if ok:
        txt = perl_out.read_text(encoding="utf-8", errors="ignore")
        if txt.startswith("-- Perl-style Ora2Py Warnings:"):
            idx = txt.find("\n\n")
            body = txt[idx + 2:] if idx >= 0 else txt
        else:
            body = txt
        record["perl"]["residuals"] = check_residuals(body)
        record["perl"]["has_create"] = bool(re.search(r"\bCREATE\s+(OR\s+REPLACE\s+)?(FUNCTION|PROCEDURE|TABLE|VIEW|TYPE|PACKAGE|SCHEMA)\b", body, re.IGNORECASE))
        # Detect properly split PACKAGE_BODY
        if obj_type == "PACKAGE_BODY":
            has_schema = bool(re.search(r"\bCREATE\s+SCHEMA\b", body, re.IGNORECASE))
            create_fn_count = len(re.findall(r"\bCREATE\s+OR\s+REPLACE\s+(FUNCTION|PROCEDURE)\b", body, re.IGNORECASE))
            still_pkg_body = bool(re.search(r"\bCREATE\s+(OR\s+REPLACE\s+)?PACKAGE\s+BODY\b", body, re.IGNORECASE))
            record["perl"]["properly_split"] = (has_schema and create_fn_count >= 2) and not still_pkg_body

    return record


def main():
    print("=" * 78)
    print("Perl-style vs Original: GKB Comprehensive Comparison")
    print("=" * 78)

    results = []
    for obj_type, pair in SAMPLES.items():
        for flavor, rel in pair.items():
            print(f"\n>>> {obj_type} / {flavor}: {rel}")
            rec = process_sample(obj_type, flavor, rel)
            results.append(rec)
            if rec["orig"]["ok"] and rec["perl"]["ok"]:
                ol = rec["orig"]["lines"]
                pl = rec["perl"]["lines"]
                o_res = sum(r["count"] for r in rec["orig"].get("residuals", []))
                p_res = sum(r["count"] for r in rec["perl"].get("residuals", []))
                print(f"  Original:   {ol:5d} lines, {o_res:5d} residuals")
                print(f"  Perl-style: {pl:5d} lines, {p_res:5d} residuals")

    # Compute scores
    summary = []
    for rec in results:
        for engine in ("orig", "perl"):
            if not rec[engine]["ok"]:
                continue
            src_lines = rec["src_lines"]
            out_lines = rec[engine]["lines"]
            retention = out_lines / src_lines * 100 if src_lines else 0
            residuals = rec[engine].get("residuals", [])
            has_create = rec[engine].get("has_create", False)
            properly_split = rec[engine].get("properly_split", False)
            acc = calc_accuracy(residuals, retention, has_create, rec["obj_type"], properly_split)
            rec[engine]["accuracy"] = acc
        # Compute improvement
        if "accuracy" in rec["orig"] and "accuracy" in rec["perl"]:
            rec["delta"] = round(rec["perl"]["accuracy"]["overall"] - rec["orig"]["accuracy"]["overall"], 1)
            summary.append(rec)

    # Save JSON
    out_json = TEST_ROOT / "compare_results.json"
    out_json.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    # Print summary
    print("\n" + "=" * 78)
    print("COMPARISON SUMMARY")
    print("=" * 78)
    # Note: orig = original Python ora2pg (legacy), perl = Python perlstyle (current)
    print(f"{'Type':12s} {'Sample':10s} {'Orig%':>7s} {'Perlstyle%':>11s} {'Delta':>8s} {'Grade':>10s}")
    print("-" * 78)
    orig_avg = []
    perlstyle_avg = []
    for rec in summary:
        orig_acc = rec["orig"]["accuracy"]["overall"]       # original Python ora2pg
        perlstyle_acc = rec["perl"]["accuracy"]["overall"]  # Python perlstyle (current)
        orig_avg.append(orig_acc)
        perlstyle_avg.append(perlstyle_acc)
        orig_grade = grade(orig_acc)
        perlstyle_grade = grade(perlstyle_acc)
        delta = rec["delta"]  # perlstyle - orig
        delta_str = f"+{delta}" if delta > 0 else f"{delta}"
        sample_name = Path(rec["rel_path"]).name
        print(f"{rec['obj_type']:12s} {sample_name[:30]:30s} {orig_acc:6.1f}% {perlstyle_acc:10.1f}% {delta_str:>8s} {orig_grade}->{perlstyle_grade}")
    print("-" * 78)
    if orig_avg and perlstyle_avg:
        avg_orig = round(sum(orig_avg) / len(orig_avg), 1)
        avg_perlstyle = round(sum(perlstyle_avg) / len(perlstyle_avg), 1)
        print(f"{'AVERAGE':12s} {'':30s} {avg_orig:6.1f}% {avg_perlstyle:10.1f}% {(avg_perlstyle-avg_orig):+8.1f}")
    print(f"\nJSON: {out_json}")


if __name__ == "__main__":
    main()