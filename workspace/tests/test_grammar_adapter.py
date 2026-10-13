"""
P3-1: AST 适配器（grammar_adapter）

设计：
- parse_plsql(sql): 输入 PL/SQL 源代码，返回 (parser, tree, errors)
- 统一异常处理: 解析失败不抛错，返回 degraded 结果（errors 列表非空）
- 降级策略: 上层调用方可以判断 errors 是否为空决定走 AST 路径还是回退
- 提供 visitor 帮助函数

落点: workspace/parsers/grammar_adapter.py
"""

import pytest

from parsers.grammar_adapter import (
    ParseResult,
    parse_plsql,
)


# ---------------------------------------------------------------------------
# 1. 正常解析
# ---------------------------------------------------------------------------


def test_parse_basic_procedure():
    """简单的 CREATE PROCEDURE 能解析"""
    sql = """CREATE OR REPLACE PROCEDURE p_test AS
BEGIN
  NULL;
END;"""
    result = parse_plsql(sql)
    assert isinstance(result, ParseResult)
    assert result.ok, f"expected parse success, got errors: {result.errors}"
    assert result.parser is not None
    assert result.tree is not None


def test_parse_basic_function():
    sql = """CREATE OR REPLACE FUNCTION f_test RETURN NUMBER AS
BEGIN
  RETURN 1;
END;"""
    result = parse_plsql(sql)
    assert result.ok, f"errors: {result.errors}"


def test_parse_package_spec():
    sql = """CREATE OR REPLACE PACKAGE pkg_test AS
  PROCEDURE p1;
  FUNCTION f1 RETURN NUMBER;
END pkg_test;"""
    result = parse_plsql(sql)
    # 某些 grammar 对 PACKAGE 支持有限——若解析失败也算 degraded
    # 本期只关心返回 ParseResult 不抛错
    assert isinstance(result, ParseResult)


# ---------------------------------------------------------------------------
# 2. 异常处理 (degradation)
# ---------------------------------------------------------------------------


def test_garbage_sql_returns_degraded_not_raise():
    """垃圾 SQL 不应抛异常——返回 degraded 结果（errors 非空）"""
    sql = "this is not valid plsql at all <<<< >>>>"
    result = parse_plsql(sql)
    assert isinstance(result, ParseResult)
    # grammar 可能 "宽容" 地解析成功，也可能 errors 非空
    # 关键：不抛错
    # 若 errors 为空，至少也要返回 tree（即使是 garbage tree）
    assert result.tree is not None or result.errors


def test_empty_sql_degraded():
    result = parse_plsql("")
    assert isinstance(result, ParseResult)
    assert result.tree is None or result.errors  # 空输入要么没 tree 要么有错误


def test_none_input():
    result = parse_plsql(None)  # type: ignore
    assert isinstance(result, ParseResult)
    assert not result.ok
    assert result.errors  # 应有错误说明


# ---------------------------------------------------------------------------
# 3. ParseResult 数据类
# ---------------------------------------------------------------------------


def test_parse_result_immutable_like():
    result = parse_plsql("CREATE OR REPLACE PROCEDURE p AS BEGIN NULL; END;")
    # ParseResult 应是 dataclass-ish（字段可访问）
    assert hasattr(result, "ok")
    assert hasattr(result, "tree")
    assert hasattr(result, "parser")
    assert hasattr(result, "errors")


# ---------------------------------------------------------------------------
# 4. 端到端: oracle_poc 实际文件
# ---------------------------------------------------------------------------


def test_oracle_poc_procedure_parse():
    """oracle_poc 端到端: 实际的 PROCEDURE 文件能被解析（或 degraded 不抛错）"""
    from pathlib import Path

    proc_file = Path("oracle_poc/output/PROCEDURE/00_PROCEDURE.sql")
    if not proc_file.exists():
        pytest.skip("oracle_poc/output not built")

    text = proc_file.read_text(encoding="utf-8")
    result = parse_plsql(text)
    # 关键：不抛错
    assert isinstance(result, ParseResult)
    # oracle_poc 是经过转换的 PG 代码，不是原 PL/SQL——grammar 可能解析不了
    # 但必须返回 ParseResult
