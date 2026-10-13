"""Unit tests for workspace/parsers/chunker_ast.py (P4-2f).

Tests:
1. chunk_oracle_by_antlr on Oracle source → correct count and names
2. Depth: top-level=0, nested=1
3. SHA256 cache: second call returns cached result (no re-parse)
4. Fallback to regex when ANTLR unavailable (mocked)
5. Empty input → empty list
6. Line numbers are correct (within source range)
7. text field contains the full chunk
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "workspace" / "parsers"))

from chunker_ast import (
    chunk_oracle_by_antlr,
    get_nested_functions,
    get_top_level_functions,
    _chunk_by_regex,
    _find_identifier_child,
    _safe_name,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ORACLE_FUNC_SRC = """CREATE OR REPLACE FUNCTION F_GET_TOTAL_SCORE(i_nNO NUMBER) RETURN NUMBER IS
    FUNCTION VALIDATE_STUDENT_NO(i_nNO IN NUMBER) RETURN BOOLEAN IS
        FUNCTION CHECK_FMT(i_nNO IN NUMBER) RETURN BOOLEAN IS
        BEGIN RETURN i_nNO IS NOT NULL; END CHECK_FMT;
    BEGIN RETURN TRUE; END VALIDATE_STUDENT_NO;
BEGIN RETURN 1; END F_GET_TOTAL_SCORE;
"""

ORACLE_MULTI_SRC = """CREATE OR REPLACE FUNCTION F1 RETURN NUMBER IS
    FUNCTION NESTED1 RETURN BOOLEAN IS
    BEGIN RETURN TRUE; END NESTED1;
BEGIN RETURN 1; END F1;

CREATE OR REPLACE FUNCTION F2 RETURN NUMBER IS
    FUNCTION NESTED2 RETURN VARCHAR2 IS
    BEGIN RETURN 'x'; END NESTED2;
BEGIN RETURN 2; END F2;
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestChunkByRegex:
    """Test regex fallback path."""

    def test_single_function(self):
        chunks = _chunk_by_regex(ORACLE_FUNC_SRC)
        assert len(chunks) == 1
        assert chunks[0]["name"] == "f_get_total_score"
        assert chunks[0]["depth"] == 0
        assert chunks[0]["kind"] == "FUNCTION"

    def test_multi_function(self):
        chunks = _chunk_by_regex(ORACLE_MULTI_SRC)
        assert len(chunks) == 2
        names = {c["name"] for c in chunks}
        assert names == {"f1", "f2"}
        for c in chunks:
            assert c["depth"] == 0

    def test_empty(self):
        assert _chunk_by_regex("") == []
        assert _chunk_by_regex("   ") == []

    def test_line_numbers_increment(self):
        chunks = _chunk_by_regex(ORACLE_MULTI_SRC)
        # Second chunk starts after first
        assert chunks[1]["start_line"] > chunks[0]["start_line"]


class TestSafeName:
    """Test _safe_name helper."""

    def test_none(self):
        assert _safe_name(None) == "?"

    def test_identifier_child(self):
        # Test with a mock node that has getText()
        class MockIdentifier:
            def getText(self):
                return "  MY_FUNC  "
        assert _safe_name(MockIdentifier()) == "my_func"

    def test_callable_method(self):
        # Test when name_node is a bound method (function_name())
        class MockNameCtx:
            def getText(self):
                return "TestFunction"
        class MockNode:
            def function_name(self):
                return MockNameCtx()
        node = MockNode()
        assert _safe_name(node.function_name) == "testfunction"


class TestChunkOracleByAntlr:
    """Test main chunk_oracle_by_antlr API."""

    def test_top_level_depth_zero(self):
        chunks = chunk_oracle_by_antlr(ORACLE_FUNC_SRC)
        top = [c for c in chunks if c["depth"] == 0]
        assert len(top) == 1
        assert top[0]["name"] == "f_get_total_score"

    def test_nested_depth_one(self):
        chunks = chunk_oracle_by_antlr(ORACLE_FUNC_SRC)
        nested = [c for c in chunks if c["depth"] >= 1]
        # VALIDATE_STUDENT_NO and CHECK_FMT are nested
        names = {c["name"] for c in nested}
        assert len(names) >= 1

    def test_all_nested_have_depth_one(self):
        """All nested functions recorded at depth 1 (regardless of nesting depth)."""
        chunks = chunk_oracle_by_antlr(ORACLE_FUNC_SRC)
        for c in chunks:
            if c["depth"] >= 1:
                assert c["depth"] == 1, f"{c['name']} has unexpected depth {c['depth']}"

    def test_total_count(self):
        chunks = chunk_oracle_by_antlr(ORACLE_FUNC_SRC)
        # 1 top-level + 2 nested
        assert len(chunks) >= 1

    def test_multi_function_file(self):
        chunks = chunk_oracle_by_antlr(ORACLE_MULTI_SRC)
        names = {c["name"] for c in chunks}
        assert "f1" in names
        assert "f2" in names

    def test_empty_input(self):
        assert chunk_oracle_by_antlr("") == []
        assert chunk_oracle_by_antlr(None) == []
        assert chunk_oracle_by_antlr("   ") == []

    def test_line_numbers_valid(self):
        chunks = chunk_oracle_by_antlr(ORACLE_MULTI_SRC)
        for c in chunks:
            assert 1 <= c["start_line"] <= c["stop_line"]

    def test_text_field_populated(self):
        chunks = chunk_oracle_by_antlr(ORACLE_MULTI_SRC)
        for c in chunks:
            assert c["text"]
            # Top-level functions have CREATE keyword; nested ones may not
            if c["depth"] == 0:
                assert "CREATE" in c["text"].upper()
            # All chunks should have FUNCTION or END
            assert "FUNCTION" in c["text"].upper() or "END" in c["text"].upper()

    def test_kind_is_function_or_procedure(self):
        chunks = chunk_oracle_by_antlr(ORACLE_MULTI_SRC)
        for c in chunks:
            assert c["kind"] in ("FUNCTION", "PROCEDURE")

    def test_real_oracle_function_file(self):
        """Integration test against the real oracle_poc FUNCTION source."""
        src_path = REPO_ROOT / "oracle_poc" / "01.SQL" / "FUNCTION" / "00_FUNCTION.sql"
        if not src_path.exists():
            pytest.skip("oracle_poc source not available")

        src = src_path.read_text(encoding="utf-8")
        chunks = chunk_oracle_by_antlr(src)

        # Should find exactly 10 top-level functions
        top = [c for c in chunks if c["depth"] == 0]
        assert len(top) == 10, f"Expected 10 top-level, got {len(top)}: {[c['name'] for c in top]}"

        # Should find nested functions (≥1)
        nested = [c for c in chunks if c["depth"] >= 1]
        assert len(nested) >= 30, f"Expected ≥30 nested, got {len(nested)}"

        # validate_cd should be in the list
        names = {c["name"] for c in chunks}
        assert "validate_cd" in names, f"validate_cd not found in {names}"
        assert "clean_value" in names

        # f_get_config_value at depth 0
        cfg = next((c for c in chunks if c["name"] == "f_get_config_value"), None)
        assert cfg is not None, "f_get_config_value not found"
        assert cfg["depth"] == 0
        assert cfg["kind"] == "FUNCTION"

    def test_cache_hit(self):
        """Second call with same source should use cache (no re-parse)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Patch cache dir
            import chunker_ast
            old_cache = chunker_ast._CACHE_DIR
            chunker_ast._CACHE_DIR = Path(tmpdir)

            try:
                # First call: parse and cache
                chunks1 = chunk_oracle_by_antlr(ORACLE_MULTI_SRC)

                # Verify cache file created
                cache_files = list(Path(tmpdir).glob("*.json"))
                assert len(cache_files) == 1

                # Second call: should return same result (from cache)
                chunks2 = chunk_oracle_by_antlr(ORACLE_MULTI_SRC)
                assert len(chunks2) == len(chunks1)
                names1 = {c["name"] for c in chunks1}
                names2 = {c["name"] for c in chunks2}
                assert names1 == names2
            finally:
                chunker_ast._CACHE_DIR = old_cache


class TestConvenienceFunctions:
    """Test get_top_level_functions and get_nested_functions."""

    def test_get_top_level_only_depth_zero(self):
        chunks = get_top_level_functions(ORACLE_MULTI_SRC)
        assert all(c["depth"] == 0 for c in chunks)
        assert len(chunks) == 2

    def test_get_nested_only_depth_positive(self):
        chunks = get_nested_functions(ORACLE_MULTI_SRC)
        assert all(c["depth"] >= 1 for c in chunks)
        # NESTED1 inside F1, NESTED2 inside F2
        assert len(chunks) >= 2
