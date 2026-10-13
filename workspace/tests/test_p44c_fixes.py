"""P4-4c: L3 装入失败的小修复

策略：
- BEGIN set_config(...) → BEGIN\nPERFORM set_config(...)  (PL/pgSQL 语法)
- FOR rec IN (SELECT ...) LOOP → DECLARE rec record; ...  (PG 15 显式 record)
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

from rules.p44c_fixes import (  # noqa: E402
    fix_begin_set_config,
    fix_for_rec_select,
    apply_p44c_fixes,
)


# ===========================================================================
# BEGIN set_config → BEGIN + PERFORM
# ===========================================================================
class TestBeginSetConfig:
    def test_basic(self):
        src = """BEGIN set_config('x', 'y', false);"""
        out = fix_begin_set_config(src)
        assert "BEGIN\n" in out or "BEGIN ;" in out
        assert "PERFORM set_config" in out
        # 不应再以 BEGIN set_config 开头
        assert not re.search(r"^BEGIN set_config", out, re.MULTILINE)

    def test_no_change_when_already_perform(self):
        src = "PERFORM set_config('x', 'y', false);"
        out = fix_begin_set_config(src)
        # 已经 PERFORM 的不动
        assert out.count("PERFORM") == 1

    def test_no_change_for_unrelated(self):
        src = "BEGIN\n  v_n := 1;\nEND;"
        out = fix_begin_set_config(src)
        assert out == src

    def test_multiple_lines(self):
        src = """DECLARE
BEGIN set_config('a', '1', false);
BEGIN set_config('b', '2', false);
END;"""
        out = fix_begin_set_config(src)
        assert "PERFORM set_config" in out
        # 应有 2 个 PERFORM
        assert out.count("PERFORM set_config") == 2


# ===========================================================================
# FOR rec IN (SELECT ...) → 加 DECLARE rec record
# ===========================================================================
class TestForRecSelect:
    def test_add_declare_rec(self):
        """DECLARE 段加 rec record 声明"""
        src = """CREATE FUNCTION f_test() RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    v_sum NUMERIC := 0;
BEGIN
    FOR rec IN (SELECT 1::int as x) LOOP
        v_sum := v_sum + rec.x;
    END LOOP;
    RETURN v_sum;
END$$;"""
        out = fix_for_rec_select(src)
        # 应该在 DECLARE 段加 rec record（出现在 BEGIN 之前）
        for_block = re.search(
            r"DECLARE\s*\n(.*?)\nBEGIN",
            out, re.IGNORECASE | re.DOTALL,
        )
        assert for_block is not None
        assert "rec record" in for_block.group(1)

    def test_no_change_when_rec_already_declared(self):
        src = """CREATE FUNCTION f_test() RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    rec record;
BEGIN
    FOR rec IN (SELECT 1::int as x) LOOP
        RETURN rec.x;
    END LOOP;
    RETURN 0;
END$$;"""
        out = fix_for_rec_select(src)
        # 已声明 rec record，不重复加
        assert out.count("rec record") == 1

    def test_no_select_for_loop_unchanged(self):
        """非 SELECT 形式的 FOR 循环不处理"""
        src = """CREATE FUNCTION f_test() RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    i int;
BEGIN
    FOR i IN 1..10 LOOP
        PERFORM 1;
    END LOOP;
    RETURN 0;
END$$;"""
        out = fix_for_rec_select(src)
        assert out == src


# ===========================================================================
# 端到端
# ===========================================================================
@pytest.mark.skipif(
    not (REPO_ROOT / "oracle_poc/output/FUNCTION/00_FUNCTION.sql").exists(),
    reason="output not present",
)
class TestOraclePocOutput:
    _PLSQL = ("FUNCTION", "PROCEDURE", "PACKAGE", "PACKAGE_BODY", "TRIGGER")

    def test_no_begin_set_config_in_plsql(self):
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
                    if re.search(r"\bBEGIN\s+set_config\s*\(", line, re.IGNORECASE):
                        bad.append((f, n, line.rstrip()))
        assert not bad, f"BEGIN set_config 残留 {len(bad)}:\n" + "\n".join(
            f"  {p}:{n}: {t}" for p, n, t in bad[:5])

    def test_for_rec_select_has_record_decl(self):
        """FOR rec IN (SELECT ...) 之前 rec record 已被声明"""
        import subprocess
        for sub in self._PLSQL:
            d = REPO_ROOT / "oracle_poc/output" / sub
            if not d.exists():
                continue
            for f in d.glob("*.sql"):
                text = f.read_text(encoding="utf-8", errors="ignore")
                new = fix_for_rec_select(text)
                # 找所有 FOR rec IN (SELECT ...) 块，检查前面 rec record 声明
                for m in re.finditer(
                        r"FOR\s+rec\s+IN\s*\(\s*SELECT", new, re.IGNORECASE):
                    pos = m.start()
                    # 找前一个 BEGIN ... DECLARE
                    preceding = new[max(0, pos - 2000):pos]
                    if "rec record" not in preceding:
                        pytest.fail(
                            f"{f.name}: 'FOR rec IN (SELECT)' 前无 'rec record' 声明")
