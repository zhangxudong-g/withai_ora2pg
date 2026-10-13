"""P4-3 批次 1：动态 SQL + DUAL 机械替换

范围（实施路径 v3.2 P4-3 扩范围）：
- FROM DUAL：PG 无 DUAL 表；SELECT 不带 FROM 即可
- NAME.NEXTVAL / NAME.CURRVAL → nextval('NAME') / currval('NAME')
- EXECUTE IMMEDIATE → EXECUTE（PL/pgSQL 动态 SQL）
  * 简单形态 EXECUTE IMMEDIATE sql INTO var  → EXECUTE sql INTO var
  * USING 子句语法 PG 兼容
  * BULK COLLECT INTO 数组形态本批次不处理（批次 2 集合化）

批次 2（后续）：BULK COLLECT / FORALL / PIPE ROW
批次 3（业务决策）：UTL_FILE / INDEX BY

单测按内容断言，禁止写死行号（AGENTS.md §4）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = REPO_ROOT / "workspace"
ORACLE_OUTPUT = REPO_ROOT / "oracle_poc" / "output"

if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

from rules.dynamic_sql import (  # noqa: E402
    rewrite_from_dual,
    rewrite_execute_immediate,
    rewrite_dynamic_sql_all,
)


# ===========================================================================
# FROM DUAL + NEXTVAL/CURRVAL
# ===========================================================================
class TestFromDual:
    def test_plain_from_dual_removed(self):
        """SELECT expr FROM DUAL → SELECT expr（删 FROM DUAL）"""
        src = "SELECT 1 FROM DUAL;"
        out = rewrite_from_dual(src)
        assert "FROM DUAL" not in out.upper().replace("FROM  DUAL", "")
        assert not re.search(r"\bFROM\s+DUAL\b", out, re.IGNORECASE)

    def test_nextval_into_from_dual(self):
        """SELECT seq.NEXTVAL INTO v FROM DUAL → SELECT nextval('seq') INTO v"""
        src = "SELECT SEQ_JOBNUM.NEXTVAL INTO v_JOBNUM FROM DUAL;"
        out = rewrite_from_dual(src)
        assert "FROM DUAL" not in out.upper() or not re.search(
            r"\bFROM\s+DUAL\b", out, re.IGNORECASE)
        assert not re.search(r"\bFROM\s+DUAL\b", out, re.IGNORECASE)
        # NEXTVAL 改写
        assert "nextval" in out.lower()
        assert "SEQ_JOBNUM" in out
        assert ".NEXTVAL" not in out.upper()

    def test_currval(self):
        src = "SELECT SEQ_X.CURRVAL INTO v FROM DUAL;"
        out = rewrite_from_dual(src)
        assert "currval" in out.lower()
        assert ".CURRVAL" not in out.upper()

    def test_from_dual_in_subquery(self):
        """子查询里的 FROM DUAL 也要删"""
        src = "USING(SELECT 1 AS DUMMY FROM DUAL) D"
        out = rewrite_from_dual(src)
        assert not re.search(r"\bFROM\s+DUAL\b", out, re.IGNORECASE)
        # 子查询结构保留
        assert "SELECT 1 AS DUMMY" in out.upper()

    def test_dual_in_string_not_touched(self):
        """字符串字面量里的 FROM DUAL 不替换"""
        src = "v := 'SELECT * FROM DUAL';"
        out = rewrite_from_dual(src)
        assert "FROM DUAL" in out  # 在字符串里保留

    def test_no_dual_unchanged(self):
        src = "SELECT * FROM STUDENT_MST WHERE ID = 1;"
        out = rewrite_from_dual(src)
        assert out == src


# ===========================================================================
# EXECUTE IMMEDIATE
# ===========================================================================
class TestExecuteImmediate:
    def test_simple_into(self):
        """EXECUTE IMMEDIATE sql INTO v → EXECUTE sql INTO v"""
        src = "EXECUTE IMMEDIATE i_sSQL INTO v_VALUE;"
        out = rewrite_execute_immediate(src)
        assert not re.search(r"\bEXECUTE\s+IMMEDIATE\b", out, re.IGNORECASE)
        assert re.search(r"\bEXECUTE\b", out, re.IGNORECASE)
        assert "INTO v_VALUE" in out

    def test_with_using(self):
        """USING 子句保留（PG 兼容）"""
        src = "EXECUTE IMMEDIATE v_sql USING v_a, v_b;"
        out = rewrite_execute_immediate(src)
        assert not re.search(r"\bIMMEDIATE\b", out, re.IGNORECASE)
        assert "USING v_a, v_b" in out

    def test_no_into(self):
        src = "EXECUTE IMMEDIATE v_ddl;"
        out = rewrite_execute_immediate(src)
        assert not re.search(r"\bIMMEDIATE\b", out, re.IGNORECASE)
        assert re.search(r"\bEXECUTE\s+v_ddl\b", out, re.IGNORECASE)

    def test_immediate_in_string_not_touched(self):
        src = "v := 'EXECUTE IMMEDIATE x';"
        out = rewrite_execute_immediate(src)
        assert "EXECUTE IMMEDIATE" in out  # 字符串里保留

    def test_bulk_collect_left_for_batch2(self):
        """BULK COLLECT INTO 数组形态：批次 1 只把 IMMEDIATE 去掉，
        BULK COLLECT 关键字留给批次 2（不在本测试断言消除）"""
        src = "EXECUTE IMMEDIATE v_sql BULK COLLECT INTO v_ranks;"
        out = rewrite_execute_immediate(src)
        # IMMEDIATE 应去掉
        assert not re.search(r"\bIMMEDIATE\b", out, re.IGNORECASE)


# ===========================================================================
# 组合
# ===========================================================================
class TestCombined:
    def test_combined(self):
        src = """
        BEGIN
            EXECUTE IMMEDIATE g_vSQL INTO g_nRETURN_CODE;
            SELECT SEQ_LOG_ID.NEXTVAL INTO v_LOG_ID FROM DUAL;
        END;
        """
        out = rewrite_dynamic_sql_all(src)
        assert not re.search(r"\bEXECUTE\s+IMMEDIATE\b", out, re.IGNORECASE)
        assert not re.search(r"\bFROM\s+DUAL\b", out, re.IGNORECASE)
        assert not re.search(r"\.NEXTVAL\b", out, re.IGNORECASE)


# ===========================================================================
# 端到端：oracle_poc 产物
# ===========================================================================
@pytest.mark.skipif(not ORACLE_OUTPUT.exists(), reason="output not present")
class TestOraclePocOutput:
    _PLSQL = {"FUNCTION", "PROCEDURE", "PACKAGE", "PACKAGE_BODY", "TRIGGER"}

    def _files(self) -> List[Path]:
        return [f for f in ORACLE_OUTPUT.glob("*/*.sql")
                if f.parent.name in self._PLSQL]

    def test_no_from_dual_in_plsql(self):
        bad: List[Tuple[Path, int, str]] = []
        for f in self._files():
            for n, line in enumerate(
                    f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if re.search(r"\bFROM\s+DUAL\b", line, re.IGNORECASE):
                    bad.append((f, n, line.rstrip()))
        assert not bad, f"FROM DUAL 残留 {len(bad)}:\n" + "\n".join(
            f"  {p}:{n}: {t}" for p, n, t in bad[:5])

    def test_no_execute_immediate_in_plsql(self):
        bad: List[Tuple[Path, int, str]] = []
        for f in self._files():
            for n, line in enumerate(
                    f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if re.search(r"\bEXECUTE\s+IMMEDIATE\b", line, re.IGNORECASE):
                    bad.append((f, n, line.rstrip()))
        assert not bad, f"EXECUTE IMMEDIATE 残留 {len(bad)}:\n" + "\n".join(
            f"  {p}:{n}: {t}" for p, n, t in bad[:5])

    def test_no_dot_nextval_in_plsql(self):
        bad: List[Tuple[Path, int, str]] = []
        for f in self._files():
            for n, line in enumerate(
                    f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if re.search(r"\.\s*NEXTVAL\b", line, re.IGNORECASE):
                    bad.append((f, n, line.rstrip()))
        assert not bad, f".NEXTVAL 残留 {len(bad)}"
