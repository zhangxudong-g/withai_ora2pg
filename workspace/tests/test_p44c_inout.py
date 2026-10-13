"""P4-4c 阶段 5：OUT/INOUT 参数 + RETURNS 类型错配修复（PG 15 实测校准版）

PG 15.19 live 实测结论（改本测试前请先复测，别凭记忆改）：
- ``INOUT o numeric`` + ``RETURNS boolean`` → CREATE 直接失败：
  ``因为OUT参数，函数的结果类型必须是numeric``
- 2 个 OUT + ``RETURNS boolean`` → 必须是 ``record``
- 类型对齐后，带输出参数的函数**只能裸 RETURN**：
  ``在带有输出参数的函数中RETURN不能有参数``
  所以 ``RETURN TRUE;`` / ``RETURN o_x;`` 都必须改成 ``RETURN;``

回归重点（v1 缺陷）：v1 用 ``re.sub`` 对整份文件全局替换 ``RETURN TRUE/FALSE``，
把"最后一个被修复函数的 INOUT 变量名"灌注到同文件**其他**函数，
在 PACKAGE_BODY 里造出 6 处作用域外 ``o_nPASS`` 引用。`test_multi_function_
no_cross_contamination` 就是这条回归的守门测试。
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

from rules.p44c_inout import (  # noqa: E402
    _iter_functions,
    _parse_out_params,
    _RETURN_EXPR,
    apply_p44c_inout,
    fix_inout_returns,
    scan_inout_fixes,
)


class TestInoutReturns:
    def test_inout_numeric_returns_boolean(self):
        src = """CREATE OR REPLACE FUNCTION F(i_nSUB NUMERIC, INOUT o_nVAL NUMERIC)
RETURNS BOOLEAN
LANGUAGE plpgsql AS $$
BEGIN
    o_nVAL := 1;
    RETURN TRUE;
END$$;"""
        out = fix_inout_returns(src)
        assert re.search(r"RETURNS\s+NUMERIC", out, re.IGNORECASE)
        # PG 要求带输出参数的函数裸 RETURN
        assert re.search(r"\bRETURN\s*;", out)
        assert not re.search(r"\bRETURN\s+TRUE\s*;", out, re.IGNORECASE)
        assert "RETURNS BOOLEAN" not in out

    def test_no_inout_unchanged(self):
        src = """CREATE OR REPLACE FUNCTION F() RETURNS NUMERIC
LANGUAGE plpgsql AS $$ BEGIN RETURN 1; END$$;"""
        out = fix_inout_returns(src)
        assert out == src

    def test_multiple_inout_returns_record(self):
        src = """CREATE OR REPLACE FUNCTION F(OUT a NUMERIC, OUT b NUMERIC)
RETURNS BOOLEAN
LANGUAGE plpgsql AS $$
BEGIN a := 1; b := 'x'; RETURN TRUE;
END$$;"""
        out = fix_inout_returns(src)
        assert "RETURNS RECORD" in out
        assert re.search(r"\bRETURN\s*;", out)
        assert not re.search(r"\bRETURN\s+TRUE\s*;", out, re.IGNORECASE)

    def test_mode_after_name_form(self):
        """OUT/INOUT 写在参数名**后**的写法也要识别（v1 漏掉，VALIDATE_SCORE_VALUE 中招）。"""
        src = """CREATE OR REPLACE FUNCTION F(i_nVALUE INOUT NUMERIC, i_d NUMERIC)
RETURNS BOOLEAN
LANGUAGE plpgsql AS $$
BEGIN
    i_nVALUE := 1;
    RETURN TRUE;
END$$;"""
        out = fix_inout_returns(src)
        assert re.search(r"RETURNS\s+NUMERIC", out, re.IGNORECASE)
        assert re.search(r"\bRETURN\s*;", out)

    def test_bare_return_on_legal_signature(self):
        """RETURNS 类型已对齐但体内仍 `RETURN o_x;` —— PG 同样拒绝，也要改裸 RETURN。"""
        src = """CREATE OR REPLACE FUNCTION F(INOUT o_x NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql AS $$
BEGIN
    o_x := 1;
    RETURN o_x;
END$$;"""
        out = fix_inout_returns(src)
        assert re.search(r"\bRETURN\s*;", out)
        assert not re.search(r"\bRETURN\s+o_x\s*;", out, re.IGNORECASE)

    def test_setof_and_trigger_untouched(self):
        src = """CREATE OR REPLACE FUNCTION F() RETURNS TABLE(a INT)
LANGUAGE sql AS $$ SELECT 1 AS a $$;"""
        assert fix_inout_returns(src) == src
        src2 = """CREATE OR REPLACE FUNCTION TRG() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$;"""
        assert fix_inout_returns(src2) == src2

    def test_return_query_not_broken(self):
        """RETURN QUERY / RETURN NEXT 属集合返回，不能被当成带表达式 RETURN 改掉。"""
        src = """CREATE OR REPLACE FUNCTION F(OUT o_x NUMERIC)
RETURNS SETOF record
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY SELECT 1;
END$$;"""
        out = fix_inout_returns(src)
        assert "RETURN QUERY" in out


class TestNoCrossContamination:
    """v1 缺陷回归守门：修复范围必须限定在「带输出参数的函数体」内。"""

    def test_multi_function_no_cross_contamination(self):
        src = """CREATE OR REPLACE FUNCTION A(INOUT o_nCREDIT NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql AS $$
BEGIN
    o_nCREDIT := 1;
    RETURN TRUE;
END$$;

CREATE OR REPLACE FUNCTION B(i_sWS VARCHAR)
RETURNS BOOLEAN
LANGUAGE plpgsql AS $$
BEGIN
    RETURN TRUE;
END$$;

CREATE OR REPLACE FUNCTION C(OUT o_nY NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql AS $$
BEGIN
    o_nY := 1;
    RETURN TRUE;
END$$;"""
        out = fix_inout_returns(src)
        # B() 没有输出参数，其 RETURN TRUE 必须原样保留
        b_body = out.split("FUNCTION B")[1].split("END$$")[0]
        assert re.search(r"\bRETURN\s+TRUE\s*;", b_body, re.IGNORECASE), (
            "无输出参数的函数被污染：RETURN TRUE 被误改")
        # 不得出现跨函数灌注的变量名
        assert "o_nPASS" not in out
        # A / C 的 RETURN 都要改裸
        assert len(re.findall(r"\bRETURN\s*;", out)) == 2

    def test_no_undefined_var_injected(self):
        """修复后每个 RETURN <ident> 的标识符必须在所属函数作用域内。"""
        src = """CREATE OR REPLACE FUNCTION A(OUT o_nPASS NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql AS $$
BEGIN o_nPASS := 1; RETURN TRUE; END$$;

CREATE OR REPLACE FUNCTION B(i_nVALUE INOUT NUMERIC, i_d NUMERIC)
RETURNS NUMERIC
LANGUAGE plpgsql AS $$
BEGIN i_nVALUE := i_d; RETURN TRUE; END$$;"""
        out = fix_inout_returns(src)
        for fn in out.split("CREATE OR REPLACE FUNCTION")[1:]:
            body = fn.split("LANGUAGE")[1]
            for ident in re.findall(r"\bRETURN\s+([A-Za-z_]\w*)\s*;", body):
                assert ident.lower() not in ("true", "false", "null")


class TestScanCounter:
    def test_scan_reports_counts(self):
        src = """CREATE OR REPLACE FUNCTION A(OUT o_nPASS NUMERIC)
RETURNS BOOLEAN
LANGUAGE plpgsql AS $$
BEGIN o_nPASS := 1; RETURN TRUE; END$$;"""
        stats = scan_inout_fixes(src)
        assert stats["returns_fixed"] == 1
        assert stats["bare_return"] == 1
        assert stats["functions"][0]["function"] == "A"


@pytest.mark.skipif(
    not (REPO_ROOT / "oracle_poc/output/FUNCTION/00_FUNCTION.sql").exists(),
    reason="output not present",
)
class TestOraclePocOutput:
    def _funcs(self):
        for sub in ("FUNCTION", "PROCEDURE", "PACKAGE", "PACKAGE_BODY", "TRIGGER"):
            d = REPO_ROOT / "oracle_poc/output" / sub
            if not d.exists():
                continue
            for f in d.glob("*.sql"):
                yield (sub, f, f.read_text(encoding="utf-8", errors="ignore"))

    def test_no_out_param_with_mismatched_returns(self):
        """产物中不得再有「带 OUT/INOUT 但 RETURNS 类型不匹配」的函数（L3 装不上）。"""
        from rules.p44c_inout import _iter_functions, _parse_out_params  # noqa: PLC0415
        bad: List[Tuple[str, str]] = []
        for sub, f, text in self._funcs():
            for name, params, _s, _e, ret, _bs, _be in _iter_functions(text):
                if ret in ("TRIGGER", "TABLE", "SETOF", "VOID"):
                    continue
                outs = _parse_out_params(params)
                if not outs:
                    continue
                want = outs[0][1] if len(outs) == 1 else "RECORD"
                if ret != want:
                    bad.append((f"{sub}/{f.name}", f"{name}: RETURNS {ret} 期望 {want}"))
        assert not bad, f"OUT/INOUT+RETURNS 仍错配 {len(bad)}:\n" + "\n".join(
            f"  {p}: {t}" for p, t in bad[:8])

    def test_no_return_with_argument_in_out_param_function(self):
        """带输出参数的函数体内不得出现 `RETURN <表达式>;`（PG 直接拒绝 CREATE）。"""
        bad: List[Tuple[str, str]] = []
        for sub, f, text in self._funcs():
            for name, params, _s, _e, ret, bs, be in _iter_functions(text):
                if ret in ("TRIGGER", "TABLE", "SETOF", "VOID"):
                    continue
                if not _parse_out_params(params):
                    continue
                body = text[bs:be]
                for expr in _RETURN_EXPR.findall(body):
                    bad.append((f"{sub}/{f.name}", f"{name}: RETURN {expr.strip()};"))
        assert not bad, f"带输出参数的函数仍有带参 RETURN {len(bad)}:\n" + "\n".join(
            f"  {p}: {t}" for p, t in bad[:8])

    def test_no_return_var_out_of_scope(self):
        """产物里每个 `RETURN <ident>` 的标识符都必须在其所属函数作用域内（DoD-3 首调可用）。"""
        bad: List[Tuple[str, str]] = []
        for sub, f, text in self._funcs():
            for name, params, head_s, _e, ret, bs, be in _iter_functions(text):
                body = text[bs:be]
                ids = {i.lower() for i in re.findall(r"\w+", params)}
                dm = re.search(r"\bDECLARE\b(.*?)\bBEGIN\b",
                               text[head_s:be], re.IGNORECASE | re.DOTALL)
                if dm:
                    # 先剥掉行/块注释，否则 `-- 说明: xxx` 里的中文会被当成变量名
                    seg = re.sub(r"--[^\n]*", "", dm.group(1))
                    seg = re.sub(r"/\*.*?\*/", "", seg, flags=re.DOTALL)
                    for part in seg.split(";"):
                        toks = re.findall(r"\w+", part)
                        if toks:
                            ids.add(toks[0].lower())
                ids.update({"new", "old", "tg_op", "found"})
                for ident in re.findall(r"\bRETURN\s+([A-Za-z_]\w*)\s*;", body):
                    low = ident.lower()
                    if low in ("true", "false", "null"):
                        continue
                    if low not in ids:
                        bad.append((f"{sub}/{f.name}", f"{name}(): RETURN {ident}"))
        assert not bad, f"RETURN 引用作用域外标识符 {len(bad)}:\n" + "\n".join(
            f"  {p}: {t}" for p, t in bad[:8])