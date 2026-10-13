"""
P3-3: 元数据提取单测

落点: workspace/tests/test_metadata.py
运行: pytest workspace/tests/test_metadata.py -v

覆盖（按内容断言，不写死行号）：
- 触发器 6/6 全字段正确，含正则拿不到的 COMPOUND
- INSTEAD OF 双 token 拆分
- 类型：AS OBJECT 列切分 / 行内注释 / 嵌套表 TABLE OF / MEMBER 方法
- 函数参数：方向词剥离 / DEFAULT 剥离
- 降级：AST 失败时回退正则且标记 degraded
- 边界：空输入 / None / 非字符串
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WORKSPACE = Path(__file__).resolve().parents[1]
if str(_WORKSPACE) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE))

from parsers.metadata import (  # noqa: E402
    extract_all,
    extract_routines,
    extract_triggers,
    extract_types,
    to_json_dict,
)

REPO = _WORKSPACE.parent
SRC_TRIGGER = REPO / "oracle_poc" / "01.SQL" / "TRIGGER" / "00_TRIGGER.sql"
SRC_TYPE = REPO / "oracle_poc" / "01.SQL" / "TYPE" / "001_TYPES.sql"
SRC_FUNCTION = REPO / "oracle_poc" / "01.SQL" / "FUNCTION" / "00_FUNCTION.sql"


def _read(p: Path) -> str:
    if not p.exists():
        pytest.skip(f"源文件不存在: {p}")
    return p.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 触发器 —— 真实语料
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def trigger_result():
    return extract_all(_read(SRC_TRIGGER))


def test_trigger_count_is_six(trigger_result):
    """源文件有 6 个触发器，必须全部识别（这是 DoD-7 的基础）。"""
    assert len(trigger_result.triggers) == 6


def test_all_trigger_fields_correct(trigger_result):
    """逐项核对 name/timing/events/table/compound。"""
    by_name = {t.name: t for t in trigger_result.triggers}
    expected = {
        "TRG_STUDENT_BI":       ("BEFORE",     ["INSERT"],                  "STUDENT_MST",           False),
        "TRG_STUDENT_AU":       ("AFTER",      ["UPDATE"],                  "STUDENT_MST",           False),
        "TRG_SCORE_BIU":        ("BEFORE",     ["INSERT", "UPDATE"],        "SCORE_REC",             False),
        "TRG_SYSTEM_CONFIG_BD": ("BEFORE",     ["DELETE"],                  "SYSTEM_CONFIG",         False),
        "TRG_SCORE_COMPOUND":   ("COMPOUND",   ["INSERT", "UPDATE", "DELETE"], "SCORE_REC",           True),
        "TRG_V_STUDENT_IO":     ("INSTEAD OF", ["INSERT", "UPDATE"],        "V_STUDENT_WITH_SCORE",  False),
    }
    for name, (timing, events, table, is_compound) in expected.items():
        assert name in by_name, f"{name} 未识别（实际: {sorted(by_name)}）"
        t = by_name[name]
        assert t.timing == timing, f"{name} timing: 期望 {timing} 实际 {t.timing}"
        assert sorted(t.events) == sorted(events), f"{name} events: 期望 {events} 实际 {t.events}"
        assert t.table == table, f"{name} table: 期望 {table} 实际 {t.table}"
        assert t.is_compound is is_compound, f"{name} is_compound: 期望 {is_compound} 实际 {t.is_compound}"


def test_compound_trigger_not_lost(trigger_result):
    """
    回归：旧正则版漏掉 TRG_SCORE_COMPOUND（5/6）。
    复合触发器是 P3-3 改用 AST 的核心价值，锁死这个用例。
    """
    names = [t.name for t in trigger_result.triggers]
    assert "TRG_SCORE_COMPOUND" in names
    cmpd = [t for t in trigger_result.triggers if t.is_compound]
    assert len(cmpd) == 1
    assert cmpd[0].name == "TRG_SCORE_COMPOUND"


def test_instead_of_timing_is_split_token(trigger_result):
    """
    回归：grammar 把 'INSTEAD' 和 'OF' 拆成两个 terminal，
    只看单 token 会取到空字符串。
    """
    t = next(t for t in trigger_result.triggers if t.name == "TRG_V_STUDENT_IO")
    assert t.timing == "INSTEAD OF"


def test_triggers_come_from_ast(trigger_result):
    """TRIGGER 文件实测 0 语法错，应走 AST 路径。"""
    assert trigger_result.parse_ok is True
    assert all(t.source == "ast" for t in trigger_result.triggers)


def test_trigger_events_text_joins(trigger_result):
    t = next(t for t in trigger_result.triggers if t.name == "TRG_SCORE_BIU")
    assert t.events_text == "INSERT, UPDATE"


# ---------------------------------------------------------------------------
# 类型 —— 真实语料（AST 解析失败 -> 正则降级）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def type_result():
    return extract_all(_read(SRC_TYPE))


def test_type_degrades_when_ast_fails(type_result):
    """
    TYPE 文件实测在 L31 连锁报错（CREATE TYPE ... BODY 触发），
    必须降级到正则且**如实标记** degraded，不许假装成功。
    """
    assert type_result.degraded is True
    assert type_result.parse_ok is False
    assert type_result.parse_errors


def test_type_as_object_columns_parsed(type_result):
    """回归：AS OBJECT ( ... ) 的列切分（7+ 列），曾因括号深度算错只认出 1 列。"""
    rec = next(t for t in type_result.types if t.name == "SCORE_RECORD")
    cols = dict(rec.columns)
    assert "CNT" in cols
    assert "STUDENT_NO" in cols
    assert "IS_PASS" in cols
    assert len(rec.columns) >= 7
    assert cols["CNT"].startswith("NUMBER")
    assert "NVARCHAR2" in cols["NAIYOU"]


def test_type_inline_comment_does_not_break_column(type_result):
    """回归：行内 '-- 计数' 注释曾把类型吞掉。"""
    rec = next(t for t in type_result.types if t.name == "SCORE_RECORD")
    for name, dtype in rec.columns:
        assert "--" not in dtype
        assert "  " not in dtype


def test_type_member_methods_extracted(type_result):
    """MEMBER FUNCTION 应提取为方法列表，不混入 columns。"""
    rec = next(t for t in type_result.types if t.name == "SCORE_RECORD")
    assert len(rec.methods) >= 2
    assert any("GET_STATUS" in m for m in rec.methods)
    # 方法名不能出现在列里
    assert not any("MEMBER" in n for n, _ in rec.columns)


def test_nested_table_types_detected(type_result):
    """AS TABLE OF xxx 形态（嵌套表）应标记 is_collection。"""
    names = {t.name for t in type_result.types}
    assert "A_CONS_PRM" in names
    at = next(t for t in type_result.types if t.name == "A_CONS_PRM")
    assert at.is_collection is True


def test_type_count_not_lost(type_result):
    """
    回归：旧正则 'AS\\s*\\(' 漏掉所有 'AS OBJECT ('，
    8 个对象类型只认出 4 个（且缺 SCORE_RECORD）。
    """
    names = {t.name for t in type_result.types}
    for expect in ("SCORE_RECORD", "STUDENT_RECORD", "REF_CURSOR_T", "CONFIG_RECORD",
                   "A_CONS_PRM", "SCORE_LIST", "STUDENT_LIST", "CONFIG_LIST"):
        assert expect in names, f"{expect} 漏识别（实际: {sorted(names)}）"


# ---------------------------------------------------------------------------
# 函数 / 过程
# ---------------------------------------------------------------------------


def test_function_routines_extracted():
    src = _read(SRC_FUNCTION)
    routines = extract_routines(src)
    assert len(routines) >= 9
    by_name = {r.name: r for r in routines}
    assert "F_GET_TOTAL_SCORE" in by_name
    f = by_name["F_GET_TOTAL_SCORE"]
    assert f.kind == "FUNCTION"
    assert f.return_type == "NUMBER"
    assert f.params, "参数不应为空"


def test_param_direction_word_stripped():
    """回归：'IN NUMBER I_NSTUDENT_NO' 应拆成 (I_NSTUDENT_NO, NUMBER)。"""
    src = _read(SRC_FUNCTION)
    f = next(r for r in extract_routines(src) if r.name == "F_GET_TOTAL_SCORE")
    names = [n for n, _ in f.params]
    types = [t for _, t in f.params]
    assert "I_NSTUDENT_NO" in names
    for n in names:
        assert not n.startswith("IN "), f"方向词未剥离: {n}"
    for t in types:
        assert "IN" not in t.split(), f"方向词残留在类型里: {t}"


def test_param_default_clause_stripped():
    """'i_nEXAM_TYPE IN NUMBER DEFAULT 1' 的 DEFAULT 及默认值应剥离。"""
    src = _read(SRC_FUNCTION)
    f = next(r for r in extract_routines(src) if r.name == "F_GET_TOTAL_SCORE")
    for _, t in f.params:
        assert "DEFAULT" not in t.upper(), f"DEFAULT 未剥离: {t}"


# ---------------------------------------------------------------------------
# 降级与边界
# ---------------------------------------------------------------------------


def test_degraded_path_marks_source_regex():
    """语法必然失败的输入应触发正则兜底，并标记 source=regex。"""
    broken = "CREATE OR REPLACE TRIGGER T_BROKEN BEFORE INSERT ON T_X ; @@@ ###"
    got, degraded = extract_triggers(broken)
    assert degraded is True
    if got:
        assert all(t.source == "regex" for t in got)


def test_empty_input_returns_empty_not_crash():
    for bad in ("", "   \n\t ", None):
        r = extract_all(bad)
        assert r.triggers == []
        assert r.types == []
        assert r.degraded is True


def test_non_string_input_does_not_crash():
    r = extract_all(12345)  # type: ignore[arg-type]
    assert r.triggers == []


def test_result_summary_shape():
    r = extract_all(_read(SRC_TRIGGER))
    s = r.summary()
    for k in ("triggers", "types", "routines", "parse_ok", "degraded", "error_count"):
        assert k in s
    assert s["triggers"] == 6


def test_to_json_dict_serializable():
    import json
    r = extract_all(_read(SRC_TRIGGER))
    payload = to_json_dict(r)
    text = json.dumps(payload, ensure_ascii=False)  # 必须能序列化
    back = json.loads(text)
    assert len(back["triggers"]) == 6
    assert back["triggers"][0]["name"] == "TRG_STUDENT_BI"


# ---------------------------------------------------------------------------
# 独立样本（不依赖真实语料，保证逻辑本身可测）
# ---------------------------------------------------------------------------


def test_trigger_extraction_on_inline_sample():
    sql = """
    CREATE OR REPLACE TRIGGER TRG_SAMPLE
    BEFORE INSERT OR UPDATE ON MY_TABLE
    FOR EACH ROW
    BEGIN
        NULL;
    END;
    /
    """
    got, _ = extract_triggers(sql)
    assert len(got) == 1
    assert got[0].name == "TRG_SAMPLE"
    assert got[0].timing == "BEFORE"
    assert sorted(got[0].events) == ["INSERT", "UPDATE"]
    assert got[0].table == "MY_TABLE"
    assert got[0].statement_level is False


def test_compound_trigger_on_inline_sample():
    sql = """
    CREATE OR REPLACE COMPOUND TRIGGER TRG_C
    FOR INSERT OR DELETE ON T2
    COMPOUND TRIGGER
      BEFORE EACH ROW IS
      BEGIN NULL; END BEFORE EACH ROW;
    END TRG_C;
    /
    """
    got, _ = extract_triggers(sql)
    assert len(got) == 1
    assert got[0].is_compound is True
    assert got[0].timing == "COMPOUND"
    assert sorted(got[0].events) == ["DELETE", "INSERT"]


def test_type_extraction_on_inline_sample():
    sql = """
    CREATE OR REPLACE TYPE T_REC AS OBJECT (
        ID    NUMBER(10,0),
        NAME  VARCHAR2(100),   -- 名字
        MEMBER FUNCTION GET_ID RETURN NUMBER
    );
    """
    got, _ = extract_types(sql)
    t = next(x for x in got if x.name == "T_REC")
    cols = dict(t.columns)
    assert cols["ID"].startswith("NUMBER")
    assert "VARCHAR2" in cols["NAME"]
    assert any("GET_ID" in m for m in t.methods)


def test_nested_array_type_on_inline_sample():
    sql = "CREATE OR REPLACE TYPE T_ARR AS TABLE OF NUMBER(10);"
    got, _ = extract_types(sql)
    t = next(x for x in got if x.name == "T_ARR")
    assert t.is_collection is True
