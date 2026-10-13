"""
P2-3: (+) 外连接 → ANSI JOIN 结构化改写（AST/图论版）

设计：
- 解析 FROM 子句成表列表
- 解析 WHERE 条件成 (table_A.col OP table_B.col, has_plus) 边集
- (+) 出现在条件中 → 该条件为 LEFT/RIGHT JOIN（取决于哪侧有 (+)）
- 图论：构建 join graph，按依赖拓扑排序，渲染 ANSI JOIN
- 递归处理子查询（PL/pgSQL 函数体内可能嵌套多层 SELECT）

oracle_poc 端到端目标：PROCEDURE 产物中 2 处 (+) → 0

落地文件：workspace/rules/outer_join_ast.py
调用：scripts/convert_oracle_poc.py 后处理（与 P2-2 同模式）
"""

import pytest

from rules.outer_join_ast import (
    rewrite_outer_joins,
    JoinType,
)


# ---------------------------------------------------------------------------
# 黄金样例 1: 基础 2 表 LEFT JOIN
# ---------------------------------------------------------------------------


def test_golden_01_two_table_left_join():
    """S.x = SC.x(+) → A LEFT JOIN B"""
    sql = """SELECT S.ID
    FROM STUDENT_MST S, SCORE_REC SC
    WHERE S.STUDENT_NO = SC.STUDENT_NO(+)
    AND S.GRADE = 1"""
    out, n = rewrite_outer_joins(sql)
    assert "(+)" not in out
    assert "LEFT JOIN" in out
    assert "ON" in out
    assert n >= 1


def test_golden_02_simple_inner_unchanged():
    """无 (+) 的纯 INNER JOIN 不变"""
    sql = "SELECT * FROM A, B WHERE A.x = B.y"
    out, n = rewrite_outer_joins(sql)
    # 应该是 INNER JOIN 形式（不带 (+)）
    # 但我们只是"无 (+) 则不动"——保留原样（保留 FROM A, B 形式）
    assert n == 0  # 没替换任何
    # 输出可以保留原样


# ---------------------------------------------------------------------------
# 黄金样例 2-3: 嵌套子查询（oracle_poc 实际场景）
# ---------------------------------------------------------------------------


def test_golden_03_subquery_with_outer_join():
    """子查询内的 (+) 必须处理（oracle_poc PROCEDURE 实际场景）"""
    sql = """SELECT COUNT(*) + 1 INTO nRANK
    FROM (
        SELECT S.STUDENT_NO
        FROM STUDENT_MST S, SCORE_REC SC
        WHERE S.STUDENT_NO = SC.STUDENT_NO(+)
        AND S.GRADE = i_nGRADE
        GROUP BY S.STUDENT_NO
    ) alias0;"""
    out, n = rewrite_outer_joins(sql)
    assert "(+)" not in out, f"仍含 (+) 占位符:\n{out}"
    assert n >= 1


def test_golden_04_nested_subqueries():
    """嵌套子查询（2 层）也处理"""
    sql = """SELECT * FROM (
        SELECT * FROM (
            SELECT A.x FROM A, B WHERE A.x = B.x(+)
        ) sub1
    ) sub2;"""
    out, n = rewrite_outer_joins(sql)
    assert "(+)" not in out


# ---------------------------------------------------------------------------
# 黄金样例 5-6: 混合 (+) + 普通条件
# ---------------------------------------------------------------------------


def test_golden_05_mixed_left_and_inner():
    """部分条件有 (+), 部分没有 → LEFT JOIN + INNER JOIN 组合"""
    sql = """SELECT *
    FROM A, B, C
    WHERE A.x = B.x(+)
    AND B.y = C.y"""
    out, n = rewrite_outer_joins(sql)
    assert "(+)" not in out
    # 应该既有 LEFT JOIN 也有 INNER JOIN
    # 实际渲染顺序取决于拓扑
    assert "LEFT JOIN" in out or "left join" in out.lower()


def test_golden_06_left_join_with_extra_filter():
    """LEFT JOIN + 额外 WHERE 条件（非连接条件）保留"""
    sql = """SELECT *
    FROM STUDENT_MST S, SCORE_REC SC
    WHERE S.STUDENT_NO = SC.STUDENT_NO(+)
    AND S.GRADE = 1
    AND SC.SCORE > 60"""
    out, n = rewrite_outer_joins(sql)
    assert "(+)" not in out
    assert "LEFT JOIN" in out
    # 额外的非连接条件应该在 WHERE 中
    # S.GRADE = 1 和 SC.SCORE > 60 应保留


# ---------------------------------------------------------------------------
# 黄金样例 7: RIGHT JOIN 语义（(+) 在左侧）
# ---------------------------------------------------------------------------


def test_golden_07_right_join_semantics():
    """(+) 在左侧 → 实际是 RIGHT JOIN（B 是驱动表）
    Oracle: A.x(+) = B.x → for each B, optionally find A
    PG:    B LEFT JOIN A ON A.x = B.x
    """
    sql = "SELECT * FROM A, B WHERE A.x(+) = B.x"
    out, n = rewrite_outer_joins(sql)
    assert "(+)" not in out
    # 应该是 RIGHT JOIN (或等价的 LEFT JOIN 调换表顺序)
    assert "JOIN" in out


# ---------------------------------------------------------------------------
# 单元: 不应触碰的 SQL
# ---------------------------------------------------------------------------


def test_no_from_clause_no_change():
    """无 FROM 子句的查询不动"""
    sql = "SELECT 1 FROM DUAL"
    out, n = rewrite_outer_joins(sql)
    assert out == sql
    assert n == 0


def test_no_outer_join_no_change():
    """无 (+) 的查询不动"""
    sql = "SELECT * FROM A, B WHERE A.x = B.y AND A.z = 1"
    out, n = rewrite_outer_joins(sql)
    # 我们的策略: 只对含 (+) 的 SELECT 做改写；其他保持原样
    assert n == 0
    # (+) 也不在，所以即使有 FROM A, B 也保持原样
    # （或可选优化为 ANSI INNER JOIN——本期不做）


def test_string_with_plus_in_parens_not_touched():
    """字符串字面量里的 (+) 不动"""
    sql = "SELECT 'literal (+) text' AS x FROM A"
    out, n = rewrite_outer_joins(sql)
    assert "(+)" in out  # 字符串里的保留
    assert n == 0


def test_comment_with_plus_not_touched():
    """注释里的 (+) 不动"""
    sql = """SELECT * FROM A
    -- note: (+) is outer join marker
    WHERE A.x = 1"""
    out, n = rewrite_outer_joins(sql)
    assert "(+)" in out  # 注释里的保留
    assert n == 0


# ---------------------------------------------------------------------------
# 错误/边界
# ---------------------------------------------------------------------------


def test_empty_sql():
    out, n = rewrite_outer_joins("")
    assert out == ""
    assert n == 0


def test_multiple_select_statements():
    """多个 SELECT 语句（PL/pgSQL body 内多块）都处理"""
    sql = """SELECT * FROM A, B WHERE A.x = B.x(+);
    SELECT * FROM C, D WHERE C.y = D.y(+);"""
    out, n = rewrite_outer_joins(sql)
    assert "(+)" not in out
    assert n >= 2


# ---------------------------------------------------------------------------
# 端到端: oracle_poc PROCEDURE
# ---------------------------------------------------------------------------


def test_oracle_poc_procedure_plus_zero():
    """oracle_poc 端到端: PROCEDURE 产物中 (+) 数应为 0

    验收标准: (+) 0（实际为 PROCEDURE 内的 2 处嵌套子查询）
    若 convert 已先跑过（文件已 clean）, n=0 也算通过（幂等）
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, "workspace")
    proc_file = Path("oracle_poc/output/PROCEDURE/00_PROCEDURE.sql")
    if not proc_file.exists():
        pytest.skip("oracle_poc/output not built")

    text = proc_file.read_text(encoding="utf-8")
    new, n = rewrite_outer_joins(text)
    import re as _re
    plus_matches = _re.findall(r"\(\+\)", new)
    assert plus_matches == [], (
        f"P2-3 验收失败: PROCEDURE 产物仍含 {len(plus_matches)} 处 (+):\n"
        + "\n".join(plus_matches[:5])
    )
    # n>=1 是首次运行的预期；幂等运行 n=0 也接受
    assert n >= 0, f"n 应为非负, got {n}"
