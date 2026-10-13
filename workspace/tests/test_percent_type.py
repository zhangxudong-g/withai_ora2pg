"""
P2-2: %TYPE 查表展开

设计：
- 建 `表.列 → PG 类型` 字典（从已转换的 TABLE 文件建）
- 仅在 DECLARE 块内替换（`DECLARE ... BEGIN` 之间）
- 查不到保留原文 + warning（**不抛错**——与 P2-1 严格策略区分）
- 仅处理 `table.col%TYPE` 形式（P2-2 范围），`var%TYPE` 形式留待后续
"""

import pytest

from rules.percent_type import (
    build_type_map_from_table_sql,
    resolve_percent_types,
)


# ---------------------------------------------------------------------------
# build_type_map_from_table_sql：纯函数，从 SQL 文本建表
# ---------------------------------------------------------------------------


def test_build_map_single_table_single_column():
    sql = """
    CREATE TABLE STUDENT_MST (
      STUDENT_NO BIGINT NOT NULL,
      GRADE BIGINT NOT NULL
    );
    """
    m = build_type_map_from_table_sql(sql)
    assert m[("STUDENT_MST", "STUDENT_NO")] == "BIGINT"
    assert m[("STUDENT_MST", "GRADE")] == "BIGINT"
    assert len(m) == 2


def test_build_map_case_insensitive():
    sql = """
    CREATE TABLE student_mst (
      student_no BIGINT
    );
    """
    m = build_type_map_from_table_sql(sql)
    # 键统一大写
    assert m[("STUDENT_MST", "STUDENT_NO")] == "BIGINT"


def test_build_map_varchar_with_size():
    sql = """
    CREATE TABLE SYSTEM_CONFIG (
      CONFIG_NAME VARCHAR(100) NOT NULL,
      CONFIG_VALUE VARCHAR(1000)
    );
    """
    m = build_type_map_from_table_sql(sql)
    # 类型 + 长度都保留
    assert m[("SYSTEM_CONFIG", "CONFIG_NAME")] == "VARCHAR(100)"
    assert m[("SYSTEM_CONFIG", "CONFIG_VALUE")] == "VARCHAR(1000)"


def test_build_map_multiple_tables():
    sql1 = "CREATE TABLE T1 (A BIGINT, B VARCHAR(10));"
    sql2 = "CREATE TABLE T2 (C NUMERIC(5,2));"
    m = build_type_map_from_table_sql(sql1 + "\n" + sql2)
    assert m[("T1", "A")] == "BIGINT"
    assert m[("T1", "B")] == "VARCHAR(10)"
    assert m[("T2", "C")] == "NUMERIC(5,2)"
    assert len(m) == 3


def test_build_map_skips_constraints():
    sql = """
    CREATE TABLE T (
      X BIGINT NOT NULL,
      Y VARCHAR(50) DEFAULT 'N',
      Z BIGINT,
      CONSTRAINT T_PK PRIMARY KEY (X)
    );
    """
    m = build_type_map_from_table_sql(sql)
    assert m[("T", "X")] == "BIGINT"
    assert m[("T", "Y")] == "VARCHAR(50)"
    assert m[("T", "Z")] == "BIGINT"
    assert len(m) == 3


def test_build_map_ignores_non_create():
    sql = """
    COMMENT ON TABLE T IS 'comment';
    CREATE TABLE T (X BIGINT);
    """
    m = build_type_map_from_table_sql(sql)
    assert m == {("T", "X"): "BIGINT"}


def test_build_map_handles_whitespace():
    sql = "CREATE TABLE T (   A    BIGINT   ,   B    NUMERIC(5,2)   );"
    m = build_type_map_from_table_sql(sql)
    assert m[("T", "A")] == "BIGINT"
    assert m[("T", "B")] == "NUMERIC(5,2)"


# ---------------------------------------------------------------------------
# resolve_percent_types：核心替换逻辑
# ---------------------------------------------------------------------------


def test_resolve_in_declare_block():
    """DECLARE 块内的 `table.col%TYPE` 必须替换为 PG 类型"""
    type_map = {("STUDENT_MST", "STUDENT_NO"): "BIGINT"}
    sql = """CREATE FUNCTION f() RETURNS void AS $$
DECLARE
  v_X STUDENT_MST.STUDENT_NO%TYPE;
BEGIN
  PERFORM 1;
END;
$$ LANGUAGE plpgsql;"""
    out, warnings = resolve_percent_types(sql, type_map)
    assert "STUDENT_MST.STUDENT_NO%TYPE" not in out
    assert "v_X BIGINT;" in out
    assert warnings == []


def test_resolve_ignores_outside_declare():
    """BEGIN 之后的 body 不替换（P2-2 仅 DECLARE 块）"""
    type_map = {("T", "X"): "BIGINT"}
    sql = """DECLARE
  v_X T.X%TYPE;
BEGIN
  SELECT T.X%TYPE INTO v_X FROM T;  -- body 中的 %TYPE 保留
END;"""
    out, warnings = resolve_percent_types(sql, type_map)
    # DECLARE 块内的 v_X 替换
    assert "v_X BIGINT;" in out
    # body 内的 %TYPE 保留（P2-2 范围外）
    assert "T.X%TYPE" in out
    assert warnings == []


def test_resolve_multiple_in_declare():
    """DECLARE 块内多个不同 %TYPE 都要替换"""
    type_map = {
        ("T1", "A"): "BIGINT",
        ("T2", "B"): "VARCHAR(50)",
    }
    sql = """DECLARE
  v_A T1.A%TYPE;
  v_B T2.B%TYPE;
BEGIN
  NULL;
END;"""
    out, _ = resolve_percent_types(sql, type_map)
    assert "v_A BIGINT;" in out
    assert "v_B VARCHAR(50);" in out


def test_resolve_with_default_value():
    """带默认值的声明也要替换：v_X TYPE := 60"""
    type_map = {("SUBJECT_MST", "PASS_SCORE"): "BIGINT"}
    sql = """DECLARE
  vPASS_SCORE SUBJECT_MST.PASS_SCORE%TYPE := 60;
BEGIN
  PERFORM 1;
END;"""
    out, _ = resolve_percent_types(sql, type_map)
    assert "vPASS_SCORE BIGINT := 60;" in out


def test_resolve_missing_warns_keeps_text():
    """找不到的 %TYPE 保留原文 + warning（不抛错）"""
    type_map = {("T", "X"): "BIGINT"}  # 缺 Y
    sql = """DECLARE
  v_X T.X%TYPE;
  v_Y T.Y%TYPE;
BEGIN
  NULL;
END;"""
    out, warnings = resolve_percent_types(sql, type_map)
    # v_X 替换了
    assert "v_X BIGINT;" in out
    # v_Y 保留原文
    assert "v_Y T.Y%TYPE;" in out
    # warning 包含缺失的列
    assert any("T.Y" in w for w in warnings), f"expected warning for T.Y, got {warnings}"


def test_resolve_ignores_qualified_only():
    """仅处理 table.col%TYPE 形式；var%TYPE（无点）保留"""
    type_map = {("T", "X"): "BIGINT"}
    sql = "DECLARE v_OTHER ROW_NO%TYPE;"  # 无表名
    out, warnings = resolve_percent_types(sql, type_map)
    # var%TYPE 不处理
    assert "v_OTHER ROW_NO%TYPE;" in out
    # 也不应进入 warnings（因为不是 P2-2 范围）
    assert all("ROW_NO" not in w for w in warnings)


def test_resolve_case_insensitive_keys():
    """type_map 键统一大写后能匹配小写 %TYPE 引用"""
    type_map = {("STUDENT_MST", "STUDENT_NO"): "BIGINT"}
    sql = """DECLARE
  v student_mst.student_no%TYPE;
BEGIN
  PERFORM 1;
END;"""
    out, _ = resolve_percent_types(sql, type_map)
    assert "v BIGINT;" in out


def test_resolve_does_not_touch_strings_or_comments():
    """字符串/注释内的 %TYPE 不替换"""
    type_map = {("T", "X"): "BIGINT"}
    sql = """DECLARE
  v_msg TEXT := 'T.X%TYPE not allowed';
  v_X T.X%TYPE;
BEGIN
  -- T.X%TYPE 出现在注释里
  NULL;
END;"""
    out, _ = resolve_percent_types(sql, type_map)
    # 真正的声明替换
    assert "v_X BIGINT;" in out
    # 字符串字面量内的 %TYPE 保留
    assert "'T.X%TYPE not allowed'" in out
    # 注释内的 %TYPE 保留
    assert "-- T.X%TYPE" in out


def test_resolve_multiple_declare_blocks():
    """多个 DECLARE 块都要处理（嵌套函数场景）"""
    type_map = {("T", "X"): "BIGINT"}
    sql = """CREATE FUNCTION f1() RETURNS void AS $$
DECLARE
  v_X1 T.X%TYPE;
BEGIN
  NULL;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION f2() RETURNS void AS $$
DECLARE
  v_X2 T.X%TYPE;
BEGIN
  NULL;
END;
$$ LANGUAGE plpgsql;"""
    out, _ = resolve_percent_types(sql, type_map)
    assert "v_X1 BIGINT;" in out
    assert "v_X2 BIGINT;" in out


def test_resolve_no_declare_block_no_op():
    """无 DECLARE 块时函数是 no-op"""
    type_map = {("T", "X"): "BIGINT"}
    sql = "SELECT T.X%TYPE FROM T;"  # body, no DECLARE
    out, warnings = resolve_percent_types(sql, type_map)
    # 不替换 body 内的 %TYPE
    assert out == sql
    assert warnings == []


def test_resolve_empty_inputs():
    """空输入/空 map 都是 no-op"""
    out1, w1 = resolve_percent_types("", {("T", "X"): "BIGINT"})
    assert out1 == ""
    assert w1 == []
    # 完整 PL/pgSQL 块，但 type_map 为空 → 保留原文 + warning
    sql2 = "DECLARE v T.X%TYPE; BEGIN PERFORM 1; END;"
    out2, w2 = resolve_percent_types(sql2, {})
    assert "v T.X%TYPE;" in out2
    assert any("T.X" in w for w in w2)


# ---------------------------------------------------------------------------
# 端到端：oracle_poc 验证
# ---------------------------------------------------------------------------


def test_oracle_poc_percent_type_count_drops_to_zero():
    """oracle_poc 端到端：调用 resolve_percent_types 后 %TYPE 数量应为 0

    验收：%TYPE 5→0
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, "scripts")
    sys.path.insert(0, "ora2pg_py_perlstyle")
    sys.path.insert(0, "workspace")

    # 收集所有已转换的文件
    out_dir = Path("oracle_poc/output")
    if not out_dir.exists():
        pytest.skip("oracle_poc/output not built")

    # 1. 从 TABLE 文件建 type_map
    type_map = {}
    for tbl_sql in sorted(out_dir.glob("TABLE/*.sql")):
        text = tbl_sql.read_text(encoding="utf-8")
        type_map.update(build_type_map_from_table_sql(text))

    # 2. 对 FUNCTION/PROCEDURE/PACKAGE/TRIGGER 跑 resolve
    all_warnings = []
    converted = {}  # filename -> (text, post_resolve_text)
    for sub in ("FUNCTION", "PROCEDURE", "PACKAGE", "PACKAGE_BODY", "TRIGGER"):
        for f in sorted(out_dir.glob(f"{sub}/*.sql")):
            text = f.read_text(encoding="utf-8")
            new, warns = resolve_percent_types(text, type_map)
            all_warnings.extend(warns)
            converted[f.name] = (text, new)

    # 3. 验收: 在 resolve 后的产物中, table.col%TYPE 形式应全部被替换
    #    残留的只能是 var%TYPE（无表名前缀）
    import re
    remaining = []
    for fname, (_orig, post) in converted.items():
        for m in re.finditer(r"\b\w+\.\w+%TYPE\b", post):
            remaining.append((fname, m.group()))

    assert remaining == [], (
        f"P2-2 验收失败: 仍有 {len(remaining)} 处 table.col%TYPE 残留:\n"
        + "\n".join(f"  {fn}: {pat}" for fn, pat in remaining)
    )
