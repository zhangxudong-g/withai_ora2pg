"""P4-4c 阶段 2：RETURNS TABLE(IN/OUT xxx) 错误修复

Oracle 源 'RETURN BOOLEAN' / 'RETURN NUMBER' 被引擎错误地转成
'RETURNS TABLE(IN NUMERIC)' / 'RETURNS TABLE(o_xxx NUMERIC)'。

PG 把 IN/o_xxx 当列名/字段名解析失败。

策略：
- RETURNS TABLE(IN <type>) → RETURNS <type>（第一个 OUT 字段）
- RETURNS TABLE(o_<name> <type>...) → RETURNS RECORD（第一个 o_ 字段）
- 函数参数中对应的 IN 参数移除
- 函数体中 RETURN NEXT (col, ...) 改 RETURN QUERY SELECT ...
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

from rules.p44c_returns import (  # noqa: E402
    fix_returns_table_out,
    apply_p44c_returns,
)


# ===========================================================================
# RETURNS TABLE 错误修复
# ===========================================================================
class TestReturnsTableOut:
    def test_simple_table_in_to_type(self):
        src = """CREATE OR REPLACE FUNCTION F(i_nVALUE INOUT NUMERIC)
RETURNS TABLE(IN NUMERIC)
LANGUAGE plpgsql AS $$ BEGIN RETURN TRUE; END $$;"""
        out = fix_returns_table_out(src)
        assert "RETURNS TABLE" not in out, f"RETURNS TABLE 残留: {out!r}"
        assert re.search(r"RETURNS\s+BOOLEAN", out, re.IGNORECASE)

    def test_table_out_to_record(self):
        src = """CREATE OR REPLACE FUNCTION F(i_nYMD NUMERIC)
RETURNS TABLE(o_nY NUMERIC, o_nM NUMERIC, o_nD NUMERIC)
LANGUAGE plpgsql AS $$ BEGIN RETURN; END $$;"""
        out = fix_returns_table_out(src)
        assert "RETURNS TABLE" not in out
        # 改成 RETURNS BOOLEAN（Oracle 源是 RETURN BOOLEAN）
        assert "RETURNS BOOLEAN" in out
        # 补回 o_xxx INOUT 参数
        assert "INOUT o_nY NUMERIC" in out

    def test_no_change_for_normal_returns(self):
        src = """CREATE FUNCTION F() RETURNS NUMERIC LANGUAGE plpgsql AS $$ BEGIN RETURN 1; END $$;"""
        out = fix_returns_table_out(src)
        assert out == src

    def test_no_change_for_real_table_function(self):
        """真正的 RETURNS TABLE（带真实列名）不处理"""
        src = """CREATE FUNCTION F() RETURNS TABLE(student_no NUMERIC) LANGUAGE plpgsql AS $$ BEGIN RETURN; END $$;"""
        out = fix_returns_table_out(src)
        # 真实列名 student_no 不是 IN/o_ 前缀，保留
        assert "RETURNS TABLE(student_no NUMERIC)" in out


@pytest.mark.skipif(
    not (REPO_ROOT / "oracle_poc/output/PACKAGE_BODY/00_PACKAGE_BODY.sql").exists(),
    reason="output not present",
)
class TestOraclePocOutput:
    def test_no_returns_table_in_or_o_prefix(self):
        """PL/pgSQL 产物无 RETURNS TABLE(IN/o_xxx) 错误"""
        bad: List[Tuple[Path, int, str]] = []
        for sub in ("FUNCTION", "PROCEDURE", "PACKAGE", "PACKAGE_BODY", "TRIGGER"):
            d = REPO_ROOT / "oracle_poc/output" / sub
            if not d.exists():
                continue
            for f in d.glob("*.sql"):
                for n, line in enumerate(
                        f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if re.search(
                            r"RETURNS\s+TABLE\s*\(\s*(?:IN|o_)\w+",
                            line, re.IGNORECASE):
                        bad.append((f, n, line.rstrip()))
        assert not bad, f"RETURNS TABLE(IN/o_) 残留 {len(bad)}:\n" + "\n".join(
            f"  {p}:{n}: {t}" for p, n, t in bad[:5])
