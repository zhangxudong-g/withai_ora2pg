"""P4-3 批次 2：BULK COLLECT / FORALL 改写

策略（手册 §12.1、§12.3）：
- FORALL：PG 等价是 FOR i IN 1..array_length(arr,1) LOOP <stmt>; END LOOP;
- BULK COLLECT 多变量：PG 不支持，留 RAISE EXCEPTION 桩（避免装上即坏）
- PIPE ROW：留给 P4-4（依赖 TYPE/STUDENT_RECORD 重设计）

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

from rules.bulk_operations import (  # noqa: E402
    rewrite_forall,
    rewrite_bulk_collect_multivar,
    apply_bulk_operations,
)


# ===========================================================================
# FORALL → FOR + array_length
# ===========================================================================
class TestForallRewrite:
    def test_simple_forall_update(self):
        """FORALL i IN 1..arr.COUNT UPDATE ... → FOR + array_length + UPDATE"""
        src = """FORALL i IN 1..v_list.COUNT
    UPDATE STUDENT_MST SET NAME = 'x' WHERE ID = v_list[i];
END;"""
        out = rewrite_forall(src)
        assert "FORALL" not in out, f"FORALL still present: {out!r}"
        assert re.search(r"FOR\s+i\s+IN\s+1\.\.array_length", out, re.IGNORECASE)
        assert "END LOOP" in out.upper()

    def test_forall_merge(self):
        """FORALL + MERGE INTO（复杂 DML）"""
        src = """FORALL i IN 1..v_student_list.COUNT
    MERGE INTO SCORE_REC SC
    USING (SELECT 1 AS DUMMY) D
    ON (SC.ID = v_student_list[i])
    WHEN MATCHED THEN UPDATE SET SCORE = v_score_list[i];
END;"""
        out = rewrite_forall(src)
        assert "FORALL" not in out
        assert "array_length" in out
        assert "END LOOP" in out.upper()

    def test_forall_delete(self):
        src = """FORALL i IN 1..arr.COUNT
    DELETE FROM LOG WHERE ID = arr[i];
END;"""
        out = rewrite_forall(src)
        assert "FORALL" not in out
        assert "array_length" in out

    def test_no_forall_unchanged(self):
        src = "FOR i IN 1..10 LOOP v_n := v_n + 1; END LOOP;"
        out = rewrite_forall(src)
        assert "FORALL" not in out  # 本来就没有
        # 也不应该被改（已存在的 FOR 不应被包装）
        assert out.count("LOOP") == src.count("LOOP")

    def test_forall_index_var_preserved(self):
        """数组索引 var[i] 应保留"""
        src = """FORALL i IN 1..arr.COUNT
    UPDATE T SET val = arr[i] WHERE idx = arr[i];"""
        out = rewrite_forall(src)
        assert "arr[i]" in out

    def test_forall_with_schema_qualified_array(self):
        """schema.array.COUNT 也应处理"""
        src = """FORALL i IN 1..pkg.arr.COUNT
    INSERT INTO T VALUES (pkg.arr[i]);"""
        out = rewrite_forall(src)
        assert "FORALL" not in out
        assert "array_length" in out


# ===========================================================================
# BULK COLLECT 多变量 → RAISE EXCEPTION 桩
# ===========================================================================
class TestBulkCollectMultivar:
    def test_three_var_bulk_collect_raises(self):
        """BULK COLLECT INTO v1, v2, v3 → RAISE EXCEPTION 桩

        断言要点：
        - 原 Oracle "BULK COLLECT INTO v1, v2, v3" 语法被替换
        - 出现 RAISE EXCEPTION 'not implemented: BULK COLLECT ...'
        """
        src = """SELECT a, b, c
        BULK COLLECT INTO v_a, v_b, v_c
        FROM T;"""
        out = rewrite_bulk_collect_multivar(src)
        # 原 Oracle "BULK COLLECT INTO v1, v2, v3" 多变量语法应被替换
        assert not re.search(
            r"\bBULK\s+COLLECT\s+INTO\s+\w+\s*,\s*\w+\s*,",
            out, re.IGNORECASE,
        ), f"原始多变量 BULK COLLECT 语法未替换: {out!r}"
        assert "RAISE" in out.upper()
        assert "not implemented" in out.lower()

    def test_single_var_bulk_collect_left_unchanged(self):
        """单变量 BULK COLLECT 暂不处理（需要数组类型上下文）"""
        src = "SELECT a BULK COLLECT INTO v_arr FROM T;"
        out = rewrite_bulk_collect_multivar(src)
        # 单变量留给批次 3 或 P4-4
        assert "BULK COLLECT" in out

    def test_no_bulk_collect_unchanged(self):
        src = "SELECT a, b INTO v_a, v_b FROM T;"
        out = rewrite_bulk_collect_multivar(src)
        assert out == src


# ===========================================================================
# 组合
# ===========================================================================
class TestCombined:
    def test_combined(self):
        src = """
        BEGIN
            SELECT a, b, c
            BULK COLLECT INTO v_a, v_b, v_c
            FROM T;
            FORALL i IN 1..v_a.COUNT
                UPDATE R SET x = v_a[i] WHERE y = v_b[i];
        END;
        """
        out = apply_bulk_operations(src)
        # 多变量 BULK COLLECT 应被替换
        assert not re.search(
            r"\bBULK\s+COLLECT\s+INTO\s+\w+\s*,\s*\w+", out, re.IGNORECASE)
        assert "FORALL" not in out
        assert "array_length" in out
        assert "RAISE" in out.upper()


# ===========================================================================
# 端到端：oracle_poc 产物
# ===========================================================================
@pytest.mark.skipif(not ORACLE_OUTPUT.exists(), reason="output not present")
class TestOraclePocOutput:
    _PLSQL = {"FUNCTION", "PROCEDURE", "PACKAGE", "PACKAGE_BODY", "TRIGGER"}

    def _files(self) -> List[Path]:
        return [f for f in ORACLE_OUTPUT.glob("*/*.sql")
                if f.parent.name in self._PLSQL]

    def test_no_forall_in_plsql(self):
        """PL/SQL 产物中无原始 FORALL 语法（排除注释行）"""
        bad: List[Tuple[Path, int, str]] = []
        for f in self._files():
            for n, line in enumerate(
                    f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                # 跳过行注释
                if re.match(r"\s*--", line):
                    continue
                if re.search(r"\bFORALL\b", line, re.IGNORECASE):
                    bad.append((f, n, line.rstrip()))
        assert not bad, f"FORALL 残留 {len(bad)}:\n" + "\n".join(
            f"  {p}:{n}: {t}" for p, n, t in bad[:5])

    def test_no_bulk_collect_in_plsql(self):
        """PL/SQL 产物无原始 BULK COLLECT INTO 多变量语法（单变量保留——待 P4-4）"""
        bad: List[Tuple[Path, int, str]] = []
        for f in self._files():
            for n, line in enumerate(
                    f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                # 只检测多变量 BULK COLLECT（INTO 后有逗号）
                if re.search(r"\bBULK\s+COLLECT\s+INTO\s+\w+\s*,\s*\w+", line, re.IGNORECASE):
                    bad.append((f, n, line.rstrip()))
        assert not bad, f"多变量 BULK COLLECT 残留 {len(bad)}:\n" + "\n".join(
            f"  {p}:{n}: {t}" for p, n, t in bad[:5])
