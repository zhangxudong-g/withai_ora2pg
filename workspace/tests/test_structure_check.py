"""
P3-2: 结构体检器

设计 4 项能力:
1. 对象数比对 (object_count): 数 CREATE FUNCTION/PROCEDURE/TRIGGER/VIEW/TABLE 等
2. 嵌套深度 (nesting_depth): 函数体内嵌套子程序的最大深度
3. 空壳/桩检测 (stub_detection): 函数无 body 或仅 RAISE EXCEPTION 'not implemented'
4. %TYPE 引用溯源 (percent_type_refs): 残留 %TYPE 数

落点: workspace/parsers/structure_check.py

策略:
- AST 优先（P3-1 适配器）
- AST 失败时降级为正则（对象计数 / 嵌套深度仍可工作）
"""

import pytest

from parsers.structure_check import (
    check_structure,
    StructureReport,
)


# ---------------------------------------------------------------------------
# 1. 对象数比对
# ---------------------------------------------------------------------------


def test_count_basic_function():
    sql = """CREATE OR REPLACE FUNCTION f1() RETURNS void AS $$
BEGIN NULL; END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION f2() RETURNS void AS $$
BEGIN NULL; END;
$$ LANGUAGE plpgsql;"""
    report = check_structure(sql)
    assert report.object_counts["FUNCTION"] == 2


def test_count_multiple_types():
    sql = """CREATE TABLE t1 (x INT);
CREATE VIEW v1 AS SELECT * FROM t1;
CREATE OR REPLACE FUNCTION f1() RETURNS void AS $$ BEGIN NULL; END; $$ LANGUAGE plpgsql;"""
    report = check_structure(sql)
    assert report.object_counts["TABLE"] == 1
    assert report.object_counts["VIEW"] == 1
    assert report.object_counts["FUNCTION"] == 1


def test_count_procedure():
    sql = "CREATE OR REPLACE PROCEDURE p1() AS $$ BEGIN NULL; END; $$ LANGUAGE plpgsql;"
    report = check_structure(sql)
    assert report.object_counts["PROCEDURE"] == 1


# ---------------------------------------------------------------------------
# 2. 嵌套深度
# ---------------------------------------------------------------------------


def test_nesting_depth_basic():
    sql = """CREATE OR REPLACE FUNCTION outer_f() RETURNS void AS $$
DECLARE
  v_inner_function text;
BEGIN
  -- 嵌套函数模拟：一个函数体内嵌套的 BEGIN ... END 块
  BEGIN
    BEGIN
      NULL;
    END;
  END;
END;
$$ LANGUAGE plpgsql;"""
    report = check_structure(sql)
    # 至少应有嵌套深度信息
    assert report.max_nesting_depth >= 1


# ---------------------------------------------------------------------------
# 3. 空壳/桩检测
# ---------------------------------------------------------------------------


def test_detect_stub_with_raise():
    sql = """CREATE OR REPLACE FUNCTION stub_fn() RETURNS void AS $$
BEGIN
  RAISE EXCEPTION 'not implemented';
END;
$$ LANGUAGE plpgsql;"""
    report = check_structure(sql)
    assert len(report.stubs) >= 1
    assert any("stub_fn" in s for s in report.stubs)


def test_detect_empty_body():
    sql = """CREATE OR REPLACE FUNCTION empty_fn() RETURNS void AS $$
BEGIN
END;
$$ LANGUAGE plpgsql;"""
    report = check_structure(sql)
    # 空 body 也应识别为 stub
    assert any("empty_fn" in s for s in report.stubs)


def test_non_stub_function_not_flagged():
    sql = """CREATE OR REPLACE FUNCTION real_fn() RETURNS int AS $$
BEGIN
  RETURN 42;
END;
$$ LANGUAGE plpgsql;"""
    report = check_structure(sql)
    assert not any("real_fn" in s for s in report.stubs)


# ---------------------------------------------------------------------------
# 4. %TYPE 引用溯源
# ---------------------------------------------------------------------------


def test_percent_type_refs_present():
    sql = "DECLARE v_X STUDENT_MST.STUDENT_NO%TYPE;"
    report = check_structure(sql)
    assert report.percent_type_count == 1


def test_percent_type_refs_zero_after_p2_2():
    """P2-2 已展开 %TYPE，所以健康文件应该是 0"""
    sql = "DECLARE v_X BIGINT;"
    report = check_structure(sql)
    assert report.percent_type_count == 0


# ---------------------------------------------------------------------------
# 端到端: oracle_poc 全部产物
# ---------------------------------------------------------------------------


def test_oracle_poc_all_files_pass():
    """oracle_poc 端到端: 13 个产物文件应全部通过结构体检。

    验收：结构体检器在 13 个产物文件上全部通过
    """
    from pathlib import Path

    out_dir = Path("oracle_poc/output")
    if not out_dir.exists():
        pytest.skip("oracle_poc/output not built")

    failures = []
    total_files = 0
    for f in sorted(out_dir.glob("**/*.sql")):
        if f.name.startswith("00_CREATE_ALL"):
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        report = check_structure(text)
        total_files += 1
        # 健康文件的 %TYPE 引用数应为 0（P2-2 已展开）
        if report.percent_type_count > 0:
            failures.append(f"{f.name}: {report.percent_type_count} %TYPE 残留")

    print(f"\n  total_files: {total_files}, failures: {len(failures)}")
    assert failures == [], f"结构体检失败:\n" + "\n".join(failures[:5])
    assert total_files >= 10, f"应至少 10 个产物文件, got {total_files}"
