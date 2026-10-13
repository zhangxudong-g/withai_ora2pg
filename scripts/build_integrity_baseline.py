"""P1-2: 建立源 13 文件对象数基线（供回归比对）。

产出: workspace/converter/integrity_baseline.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "workspace"))

from converter.integrity import list_object_names, _NAME_PATTERNS  # noqa: E402

SRC = REPO / "oracle_poc" / "01.SQL"

DETECT_ORDER = [
    ("PACKAGE_BODY", r"\bCREATE\s+(?:OR\s+REPLACE\s+)?PACKAGE\s+BODY\b"),
    ("PACKAGE", r"\bCREATE\s+(?:OR\s+REPLACE\s+)?PACKAGE\b"),
    ("TRIGGER", r"\bCREATE\s+(?:OR\s+REPLACE\s+)?TRIGGER\b"),
    ("TYPE", r"\bCREATE\s+(?:OR\s+REPLACE\s+)?TYPE\b"),
    ("VIEW", r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:FORCE\s+)?VIEW\b"),
    ("PROCEDURE", r"\bCREATE\s+(?:OR\s+REPLACE\s+)?PROCEDURE\b"),
    ("FUNCTION", r"\bCREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\b"),
    ("TABLE", r"\bCREATE\s+(?:GLOBAL\s+)?(?:TEMPORARY\s+)?TABLE\b"),
    ("SEQUENCE", r"\bCREATE\s+(?:OR\s+REPLACE\s+)?SEQUENCE\b"),
    ("SYNONYM", r"\bCREATE\s+(?:PUBLIC\s+)?SYNONYM\b"),
]


def detect_type(text: str) -> str:
    upper = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    upper = re.sub(r"^\s*--[^\n]*", "", upper, flags=re.MULTILINE).strip().upper()
    for t, pat in DETECT_ORDER:
        if re.search(pat, upper):
            return t
    return "UNKNOWN"


def main() -> int:
    baseline = {}
    for f in sorted(SRC.rglob("*.sql")):
        if f.name == "CREATE_ALL.sql":
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        otype = detect_type(text)
        names = list_object_names(text, otype) if otype in _NAME_PATTERNS else []
        rel = f.relative_to(SRC).as_posix()
        baseline[rel] = {
            "type": otype,
            "count": len(names),
            "names": names,
        }

    out = REPO / "workspace" / "converter" / "integrity_baseline.json"
    out.write_text(json.dumps(baseline, indent=2, ensure_ascii=False), encoding="utf-8")
    total = sum(v["count"] for v in baseline.values())
    print(f"基线已写入: {out.relative_to(REPO)}")
    print(f"文件数: {len(baseline)}, 源对象总数: {total}")
    for rel, v in baseline.items():
        print(f"  {rel:<35} {v['type']:<14} {v['count']} 个对象")
    return 0


if __name__ == "__main__":
    sys.exit(main())
