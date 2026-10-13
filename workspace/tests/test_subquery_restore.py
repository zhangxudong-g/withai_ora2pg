"""
P2-1: %SUBQUERY%d% 占位符回填

HANDOFF §1.1 第 1-3 类缺陷（数据丢失）：
- `MERGE ... USING (%SUBQUERY0%) D` 应还原为 `MERGE ... USING (SELECT 1 AS DUMMY FROM DUAL) D`
- `FROM (%SUBQUERY0%) alias0` 应还原为 `FROM (SELECT ...) alias0`
- `FROM (%SUBQUERY1%) alias1` 应还原为 `FROM (SELECT ...) alias1`

设计：
- 抽取时缓存原文到 state.subquery_map
- 回填在所有转换步骤完成后执行（位于 perlstyle_engine.py 转换流水线末尾）
- 无法还原（即 idx 不在 map 中）→ 抛错，绝不静默
"""

import pytest

from rules.subquery_restore import restore_subqueries, SubqueryRestoreError


# ---------------------------------------------------------------------------
# 纯函数单测（不依赖 PG / 引擎）
# ---------------------------------------------------------------------------


class _FakeState:
    """最小 ConversionState 替身：只暴露 subquery_map。"""
    def __init__(self, m):
        self.subquery_map = dict(m)


def test_restore_simple_from_clause():
    """FROM (%SUBQUERY0%) alias0 → FROM (SELECT 1) alias0"""
    state = _FakeState({0: "SELECT 1 AS DUMMY FROM DUAL"})
    sql = "SELECT * FROM (%SUBQUERY0%) alias0"
    out, restored = restore_subqueries(sql, state)
    assert out == "SELECT * FROM (SELECT 1 AS DUMMY FROM DUAL) alias0"
    assert restored == [0]


def test_restore_using_clause_no_alias():
    """MERGE ... USING(%SUBQUERY0%) D → USING(SELECT 1 FROM DUAL) D"""
    state = _FakeState({0: "SELECT 1 AS DUMMY FROM DUAL"})
    sql = "MERGE INTO T USING(%SUBQUERY0%) D ON (T.ID=D.ID)"
    out, restored = restore_subqueries(sql, state)
    assert out == "MERGE INTO T USING(SELECT 1 AS DUMMY FROM DUAL) D ON (T.ID=D.ID)"
    assert restored == [0]


def test_restore_multiple_indices():
    """两个不同 idx 在同一 SQL 中都要还原"""
    state = _FakeState({
        0: "SELECT id FROM student_mst",
        1: "SELECT id FROM score_rec",
    })
    sql = (
        "SELECT a.n FROM (%SUBQUERY0%) alias0, (%SUBQUERY1%) alias1 "
        "WHERE alias0.id = alias1.id"
    )
    out, restored = restore_subqueries(sql, state)
    assert out == (
        "SELECT a.n FROM (SELECT id FROM student_mst) alias0, "
        "(SELECT id FROM score_rec) alias1 "
        "WHERE alias0.id = alias1.id"
    )
    assert sorted(restored) == [0, 1]


def test_restore_outside_parens():
    """占位符出现在非 FROM/USING 上下文也必须还原（通用兜底）"""
    state = _FakeState({2: "SELECT 42"})
    sql = "INSERT INTO T VALUES(%SUBQUERY2%)"
    out, _ = restore_subqueries(sql, state)
    assert out == "INSERT INTO T VALUES(SELECT 42)"


def test_no_placeholders_no_op():
    """无占位符时，函数是 no-op，restored 空"""
    state = _FakeState({0: "SELECT 1"})
    sql = "SELECT * FROM student_mst"
    out, restored = restore_subqueries(sql, state)
    assert out == "SELECT * FROM student_mst"
    assert restored == []


def test_preserves_surrounding_text():
    """占位符周围的 SQL 文本、分号、注释等必须完全保留"""
    state = _FakeState({0: "SELECT 1"})
    sql = "BEGIN\n  -- comment\n  INSERT INTO T VALUES(%SUBQUERY0%);\nEND;"
    out, _ = restore_subqueries(sql, state)
    assert out == "BEGIN\n  -- comment\n  INSERT INTO T VALUES(SELECT 1);\nEND;"


# ---------------------------------------------------------------------------
# 错误路径：缺索引必须抛错（绝不静默）
# ---------------------------------------------------------------------------


def test_missing_index_raises():
    """state.subquery_map 缺 idx=0 → 抛 SubqueryRestoreError"""
    state = _FakeState({})  # 空的
    sql = "SELECT * FROM (%SUBQUERY0%) alias0"
    with pytest.raises(SubqueryRestoreError) as ei:
        restore_subqueries(sql, state)
    assert "%SUBQUERY0%" in str(ei.value)
    assert "idx=0" in str(ei.value) or "0" in str(ei.value)


def test_partial_missing_raises():
    """map 里有 0 没有 1 → 仍抛错（哪怕 0 能还原，绝不部分成功）"""
    state = _FakeState({0: "SELECT 1"})
    sql = "SELECT * FROM (%SUBQUERY0%) a, (%SUBQUERY1%) b"
    with pytest.raises(SubqueryRestoreError):
        restore_subqueries(sql, state)


def test_error_lists_all_missing_indices():
    """错误消息要列出所有缺失 idx（便于排障）"""
    state = _FakeState({})
    sql = "SELECT * FROM (%SUBQUERY0%) a, (%SUBQUERY1%) b, (%SUBQUERY2%) c"
    with pytest.raises(SubqueryRestoreError) as ei:
        restore_subqueries(sql, state)
    msg = str(ei.value)
    # 三个 idx 都要在消息里
    for i in (0, 1, 2):
        assert str(i) in msg, f"expected idx {i} in error message, got: {msg}"


def test_string_body_preserved_verbatim():
    """body 内含特殊字符（换行、注释、引号）原样保留"""
    body = "SELECT\n  -- inline comment\n  'O''Brien' AS name\nFROM DUAL"
    state = _FakeState({0: body})
    sql = "SELECT * FROM (%SUBQUERY0%) a"
    out, _ = restore_subqueries(sql, state)
    assert out == "SELECT * FROM (" + body + ") a"


# ---------------------------------------------------------------------------
# 集成：与 extract_subqueries 端到端配对
# ---------------------------------------------------------------------------


def test_extract_then_restore_roundtrip():
    """extract_subqueries → restore_subqueries 应可逆"""
    import sys
    sys.path.insert(0, "ora2pg_py_perlstyle")
    from plsql_subs import extract_subqueries

    class _State:
        pass

    state = _State()
    state.subquery_idx = 0
    state.subquery_map = {}

    original = "SELECT * FROM (SELECT 1 FROM DUAL) a, (WITH x AS (SELECT 2) SELECT * FROM x) b"
    extracted = extract_subqueries(original, state)
    # 抽取后占位符数 == 抽取项数
    import re as _re
    placeholders = _re.findall(r"%SUBQUERY(\d+)%", extracted)
    assert len(placeholders) == 2

    restored, used = restore_subqueries(extracted, state)
    assert restored == original
    assert sorted(int(x) for x in used) == [0, 1]


# ---------------------------------------------------------------------------
# 端到端集成：跑 oracle_poc PROCEDURE 转换后产物中 %SUBQUERY 应为 0
# ---------------------------------------------------------------------------


def test_oracle_poc_procedure_has_zero_subquery_placeholders(tmp_path):
    """oracle_poc 端到端：PROCEDURE 产物中 %SUBQUERY%d% 必须为 0
    （验证 P2-1 验收标准：3→0）

    该测试是集成测试：完整跑 convert_function("...") → 产物不再含 %SUBQUERY。
    """
    import sys
    sys.path.insert(0, "ora2pg_py_perlstyle")
    from perlstyle_engine import convert_function

    src = (tmp_path / "proc.sql")
    # 复刻 HANDOFF §1.1 第 1 类数据丢失场景：MERGE USING (SELECT 1 FROM DUAL) D
    src.write_text(
        "CREATE OR REPLACE PROCEDURE p_test AS\n"
        "BEGIN\n"
        "  MERGE INTO score_rec sc\n"
        "  USING (SELECT 1 AS DUMMY FROM DUAL) d\n"
        "  ON (sc.id = 1)\n"
        "  WHEN MATCHED THEN UPDATE SET sc.score = 0;\n"
        "  SELECT COUNT(*) + 1 INTO v_n\n"
        "  FROM (SELECT id FROM student_mst) a0;\n"
        "  SELECT COUNT(*) + 1 INTO v_m\n"
        "  FROM (SELECT id FROM score_rec) a1;\n"
        "END;\n",
        encoding="utf-8",
    )
    out, warns = convert_function(src.read_text(encoding="utf-8"))
    assert "%SUBQUERY" not in out, (
        f"P2-1 验收失败：产物仍含 %SUBQUERY 占位符\n{out}"
    )
