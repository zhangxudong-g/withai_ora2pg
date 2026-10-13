"""P4-4c 阶段 3: PIPE ROW 改写

Oracle PIPELINED 表函数 + TYPE(nested_table) 转换为 PG：
- RETURNS <nested_type> → RETURNS SETOF <element_type>
- PIPE ROW(TYPE(coln, ...)) → RETURN NEXT (ROW(coln, ...))::<element_type>
- RETURN; 在 set-returning 函数中删除
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = REPO_ROOT / "workspace"

if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

from rules.p44c_pipe import (  # noqa: E402
    rewrite_pipe_row,
    apply_p44c_pipe,
)


class TestPipeRow:
    def test_returns_student_list_to_setof(self):
        src = """CREATE FUNCTION F()
RETURNS STUDENT_LIST
LANGUAGE plpgsql AS $$
BEGIN
    PIPE ROW(STUDENT_RECORD(1, 'a', 0));
    RETURN;
END$$;"""
        out = rewrite_pipe_row(src)
        assert "RETURNS SETOF STUDENT_RECORD" in out, f"RETURNS 没改: {out!r}"
        assert "PIPE ROW" not in out, f"PIPE ROW 残留: {out!r}"
        # RETURN NEXT 应有
        assert "RETURN NEXT" in out

    def test_returns_score_list_to_setof(self):
        src = """CREATE FUNCTION F()
RETURNS SCORE_LIST
LANGUAGE plpgsql AS $$
BEGIN
    PIPE ROW(SCORE_RECORD(1, 2, 3));
    RETURN;
END$$;"""
        out = rewrite_pipe_row(src)
        assert "RETURNS SETOF SCORE_RECORD" in out

    def test_no_pipe_unchanged(self):
        src = """CREATE FUNCTION F() RETURNS NUMERIC LANGUAGE plpgsql AS $$
BEGIN RETURN 1; END$$;"""
        out = rewrite_pipe_row(src)
        assert out == src


@pytest.mark.skipif(
    not (REPO_ROOT / "oracle_poc/output/FUNCTION/00_FUNCTION.sql").exists(),
    reason="output not present",
)
class TestOraclePocOutput:
    _PLSQL = ("FUNCTION", "PROCEDURE", "PACKAGE", "PACKAGE_BODY", "TRIGGER")

    def test_no_pipe_row_in_plsql(self):
        bad: List[Tuple[Path, int, str]] = []
        for sub in self._PLSQL:
            d = REPO_ROOT / "oracle_poc/output" / sub
            if not d.exists():
                continue
            for f in d.glob("*.sql"):
                for n, line in enumerate(
                        f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if re.match(r"\s*--", line):
                        continue
                    if re.search(r"\bPIPE\s+ROW\b", line, re.IGNORECASE):
                        bad.append((f, n, line.rstrip()))
        assert not bad, f"PIPE ROW 残留 {len(bad)}:\n" + "\n".join(
            f"  {p}:{n}: {t}" for p, n, t in bad[:5])

    def test_setof_appears_for_pipeline_functions(self):
        """F_GET_*_LIST_PIPELINE 应改成 RETURNS SETOF"""
        text = (REPO_ROOT / "oracle_poc/output/FUNCTION/00_FUNCTION.sql").read_text(
            encoding="utf-8", errors="ignore"
        )
        out = apply_p44c_pipe(text)
        assert "RETURNS SETOF STUDENT_RECORD" in out
        assert "RETURNS SETOF SCORE_RECORD" in out
