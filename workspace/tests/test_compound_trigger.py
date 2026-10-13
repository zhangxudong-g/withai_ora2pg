"""
P3-4: 复合触发器拆分单测

落点: workspace/tests/test_compound_trigger.py
运行: pytest workspace/tests/test_compound_trigger.py -v

原则：按内容断言，禁止写死行号。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WORKSPACE = Path(__file__).resolve().parents[1]
if str(_WORKSPACE) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE))

from converter.compound_trigger import (  # noqa: E402
    CompoundTriggerError,
    convert_compound_trigger,
    parse_compound_trigger,
    split_compound_trigger,
)

REPO = _WORKSPACE.parent
SRC_TRIGGER = REPO / "oracle_poc" / "01.SQL" / "TRIGGER" / "00_TRIGGER.sql"


SAMPLE = """
CREATE OR REPLACE TRIGGER TRG_SCORE_COMPOUND
FOR INSERT OR UPDATE OR DELETE ON SCORE_REC
COMPOUND TRIGGER
    -- 声明节
    TYPE t_score_list IS TABLE OF SCORE_REC%ROWTYPE INDEX BY PLS_INTEGER;
    v_scores t_score_list;
    v_count NUMBER := 0;

    -- 每个行处理前
    BEFORE EACH ROW IS
    BEGIN
        v_count := v_count + 1;
        v_scores(v_count).STUDENT_NO := :NEW.STUDENT_NO;
    END BEFORE EACH ROW;

    -- 语句执行后
    AFTER STATEMENT IS
    BEGIN
        DBMS_OUTPUT.PUT_LINE('处理了 ' || v_count || ' 条成绩记录');
    END AFTER STATEMENT;
END TRG_SCORE_COMPOUND;
/
"""


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def test_parse_header_fields():
    t = parse_compound_trigger(SAMPLE)
    assert t.name == "TRG_SCORE_COMPOUND"
    assert t.table == "SCORE_REC"
    assert t.events == ["INSERT", "UPDATE", "DELETE"]


def test_parse_timing_points():
    t = parse_compound_trigger(SAMPLE)
    assert len(t.points) == 2
    assert (t.points[0].timing, t.points[0].scope) == ("BEFORE", "EACH ROW")
    assert (t.points[1].timing, t.points[1].scope) == ("AFTER", "STATEMENT")


def test_parse_declare_section_captured():
    """声明节（含嵌套表类型）必须被单独取出——它承载跨时机点状态。"""
    t = parse_compound_trigger(SAMPLE)
    assert "t_score_list" in t.declare_section
    assert "TABLE OF" in t.declare_section
    assert "v_count" in t.declare_section


def test_point_body_excludes_end_label():
    """时机点 body 不应把 'END BEFORE EACH ROW;' 尾巴带进去。"""
    t = parse_compound_trigger(SAMPLE)
    b0 = t.points[0].body.upper()
    assert "END BEFORE EACH ROW" not in b0
    assert "V_COUNT := V_COUNT + 1" in b0


# ---------------------------------------------------------------------------
# 拆分
# ---------------------------------------------------------------------------


def test_split_produces_two_triggers():
    t = parse_compound_trigger(SAMPLE)
    gens = split_compound_trigger(t)
    assert len(gens) == 2
    assert gens[0].function_name == "TRG_SCORE_COMPOUND_BR"
    assert gens[1].function_name == "TRG_SCORE_COMPOUND_AS"
    assert gens[0].scope == "EACH ROW"
    assert gens[1].scope == "STATEMENT"


def test_split_all_points_have_original_events():
    """拆分后每个触发器都必须覆盖原始的全部事件，不能只取第一个。"""
    t = parse_compound_trigger(SAMPLE)
    for g in split_compound_trigger(t):
        assert g.events_text == "INSERT OR UPDATE OR DELETE"
        assert g.table == "SCORE_REC"


def test_collection_type_flagged_in_notes():
    """嵌套表类型需在 notes 中提示（P3-4 的已知语义缺口）。"""
    t = parse_compound_trigger(SAMPLE)
    notes = " ".join(n for g in split_compound_trigger(t) for n in g.notes)
    assert "T_SCORE_LIST" in notes.upper()
    assert "GUC" in notes.upper() or "临时表" in notes


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------


def test_render_contains_both_triggers():
    pg, warns = convert_compound_trigger(SAMPLE)
    assert "TRG_SCORE_COMPOUND_BR" in pg
    assert "TRG_SCORE_COMPOUND_AS" in pg
    assert pg.count("CREATE OR REPLACE FUNCTION") == 2
    assert pg.count("CREATE TRIGGER") == 2


def test_render_row_trigger_returns_new():
    pg, _ = convert_compound_trigger(SAMPLE)
    br = pg.split("CREATE OR REPLACE FUNCTION TRG_SCORE_COMPOUND_BR")[1]
    br = br.split("$$ LANGUAGE plpgsql")[0]
    assert "RETURN NEW;" in br


def test_render_statement_trigger_returns_null():
    """STATEMENT 级触发器必须 RETURN NULL（PG 要求行触发器才返回 NEW/OLD）。"""
    pg, _ = convert_compound_trigger(SAMPLE)
    seg = pg.split("CREATE OR REPLACE FUNCTION TRG_SCORE_COMPOUND_AS")[1]
    seg = seg.split("$$ LANGUAGE plpgsql")[0]
    assert "RETURN NULL;" in seg


def test_render_rewrites_new_old_prefix():
    pg, _ = convert_compound_trigger(SAMPLE)
    assert ":NEW." not in pg, "PG 触发器体内不能有 :NEW."
    assert "NEW.STUDENT_NO" in pg


def test_render_converts_dbms_output():
    pg, _ = convert_compound_trigger(SAMPLE)
    assert "DBMS_OUTPUT" not in pg
    assert "RAISE NOTICE" in pg


def test_render_declares_lifecycle_guards():
    """必须带 DROP TRIGGER IF EXISTS，保证脚本可重入。"""
    pg, _ = convert_compound_trigger(SAMPLE)
    assert pg.count("DROP TRIGGER IF EXISTS") == 2


def test_render_warnings_not_empty():
    _, warns = convert_compound_trigger(SAMPLE)
    assert warns, "拆分复合触发器必须产出 warning（提示语义降级），不许静默"
    assert all("[P3-4]" in w for w in warns)


def test_statement_trigger_has_no_double_granularity():
    """
    回归：曾生成 'FOR EACH STATEMENT' + 'FOR EACH ROW EXECUTE ...' 的
    双粒度子句，PG 会报语法错。STATEMENT 触发器只能是 FOR EACH STATEMENT。
    """
    pg, _ = convert_compound_trigger(SAMPLE)
    seg = pg.split("CREATE TRIGGER TRG_SCORE_COMPOUND_AS")[1]
    seg = seg.split("EXECUTE PROCEDURE")[0]
    assert "FOR EACH STATEMENT" in seg
    assert "FOR EACH ROW" not in seg, f"STATEMENT 触发器里不应出现 FOR EACH ROW: {seg!r}"


def test_declare_section_variables_are_declared():
    """
    回归：声明节里的 v_count 未搬进 DECLARE 块，PG 首调会报
    'column v_count does not exist'——本项目要消灭的"装上即坏"。
    """
    pg, _ = convert_compound_trigger(SAMPLE)
    br = pg.split("CREATE OR REPLACE FUNCTION TRG_SCORE_COMPOUND_BR")[1]
    decl = br.split("$$ LANGUAGE plpgsql")[0]
    assert "v_count numeric" in decl.lower() or "v_count" in decl.lower()
    # 必须出现在 DECLARE 段内
    declare_part = decl.split("BEGIN")[0]
    assert "v_count" in declare_part, "v_count 必须在 DECLARE 段中声明"


def test_collection_state_not_declared_as_rowtype():
    """嵌套表变量不应被当成标量搬进 DECLARE（PG 无对应嵌套表类型）。"""
    pg, _ = convert_compound_trigger(SAMPLE)
    assert "v_scores t_score_list" not in pg.lower()
    assert "v_scores numeric" not in pg.lower(), "嵌套表变量被错误声明成标量"
    # 但 GUC 承载的提示必须在
    assert "GUC" in pg.upper() or "ora2pg.trg_score_compound.state" in pg


def test_collection_index_assignment_marked_for_review():
    """
    回归：'v_scores(v_count).STUDENT_NO := ...' 是 Oracle 嵌套表下标赋值，
    PG 无直接等价物。原样输出会得到语法错，必须转成显式的人工复核标记。
    """
    pg, _ = convert_compound_trigger(SAMPLE)
    assert "v_scores(v_count).STUDENT_NO :=" not in pg, "嵌套表下标赋值未处理"
    assert "需人工复核" in pg, "集合下标赋值必须留人工复核标记"


# ---------------------------------------------------------------------------
# 失败路径：绝不静默
# ---------------------------------------------------------------------------


def test_raises_when_no_header():
    with pytest.raises(CompoundTriggerError):
        parse_compound_trigger("SELECT 1 FROM DUAL;")


def test_raises_when_no_timing_point():
    """有头部但没时机点 -> 抛错，不返回空壳。"""
    bad = """
    CREATE OR REPLACE COMPOUND TRIGGER T_BAD
    FOR INSERT ON T_X
    COMPOUND TRIGGER
        v_x NUMBER;
    END T_BAD;
    """
    with pytest.raises(CompoundTriggerError):
        parse_compound_trigger(bad)


def test_raises_when_for_clause_missing():
    bad = """
    CREATE OR REPLACE COMPOUND TRIGGER T_BAD2
    COMPOUND TRIGGER
        BEFORE EACH ROW IS BEGIN NULL; END BEFORE EACH ROW;
    END T_BAD2;
    """
    with pytest.raises(CompoundTriggerError):
        parse_compound_trigger(bad)


# ---------------------------------------------------------------------------
# 真实语料
# ---------------------------------------------------------------------------


def test_real_source_compound_trigger():
    """真实源文件里的 TRG_SCORE_COMPOUND 必须能拆成 2 个触发器。"""
    if not SRC_TRIGGER.exists():
        pytest.skip(f"源文件不存在: {SRC_TRIGGER}")
    sql = SRC_TRIGGER.read_text(encoding="utf-8", errors="replace")
    # 切出复合触发器块（以 COMPOUND TRIGGER 关键字定位）
    i = sql.upper().find("COMPOUND TRIGGER")
    if i < 0:
        pytest.skip("源文件里没有复合触发器")
    start = sql.upper().rfind("CREATE OR REPLACE", 0, i)
    pg, warns = convert_compound_trigger(sql[start:])
    assert "TRG_SCORE_COMPOUND_BR" in pg
    assert "TRG_SCORE_COMPOUND_AS" in pg
    assert "SCORE_REC" in pg
    assert warns
