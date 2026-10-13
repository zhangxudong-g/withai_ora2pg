"""P4-4a: TYPE 文件 SELF 标识符改写

PG 没有 Oracle OBJECT/MEMBER FUNCTION 概念。引擎提取的 MEMBER 函数（顶层
GET_xxx_of_TYPE）引用了 SELF_<TYPE>_<FIELD>，没声明参数 → PL/pgSQL 把
SELF_xxx 当列名查询 → "字段不存在"（L3.5 fail 4 个）。

策略：
- 已知 TYPE 表（_KNOWN_TYPES）: SCORE_RECORD, STUDENT_RECORD, REF_CURSOR_T
- 函数签名：GET_xxx_of_TYPE(...) → GET_xxx_of_TYPE(i_self TYPE, ...)
- 函数体：SELF_<TYPE>_<FIELD> → i_self.<FIELD>

只对 TYPE 文件（001_TYPES.sql）做后处理。
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

from rules.type_self import (  # noqa: E402
    rewrite_type_self,
    _KNOWN_TYPES,
    _SELF_REF_RE,
)


# ===========================================================================
# SELF 引用识别
# ===========================================================================
class TestSelfRefRegex:
    def test_self_score_record_is_pass(self):
        m = _SELF_REF_RE.search("SELF_SCORE_RECORD_IS_PASS = 1")
        assert m is not None
        assert m.group(1) == "SCORE_RECORD"
        assert m.group(2) == "IS_PASS"

    def test_self_student_record_birth_date(self):
        m = _SELF_REF_RE.search("vBIRTH_YEAR := (SUBSTRING((SELF_STUDENT_RECORD_BIRTH_DATE)::varchar")
        assert m is not None
        assert m.group(1) == "STUDENT_RECORD"
        assert m.group(2) == "BIRTH_DATE"

    def test_no_match_other_identifiers(self):
        # 不应误匹配普通变量
        assert _SELF_REF_RE.search("V_SCORE := 1") is None
        assert _SELF_REF_RE.search("SELF_DATA := 1") is None  # SELF_DATA 不是已知 TYPE


# ===========================================================================
# 函数签名 + 函数体改写
# ===========================================================================
class TestRewriteSignature:
    def test_add_param_to_function(self):
        src = """CREATE OR REPLACE FUNCTION GET_STATUS_of_SCORE_RECORD()
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
BEGIN
    IF SELF_SCORE_RECORD_IS_PASS = 1 THEN
        RETURN 'PASS';
    END IF;
END;
$$;"""
        out = rewrite_type_self(src)
        # 函数签名应加 i_self SCORE_RECORD 参数
        assert re.search(
            r"GET_STATUS_of_SCORE_RECORD\s*\(\s*i_self\s+SCORE_RECORD\s*\)",
            out, re.IGNORECASE,
        ), f"签名未加参数: {out}"
        # 函数体应替换为 i_self.IS_PASS
        assert "i_self.IS_PASS" in out
        assert "SELF_SCORE_RECORD_IS_PASS" not in out

    def test_existing_param_preserved(self):
        """已有参数时加 i_self 前缀，不要覆盖"""
        src = """CREATE OR REPLACE FUNCTION OPEN_SCORE_CURSOR_of_REF_CURSOR_T(p_STUDENT_NO NUMERIC)
RETURNS REFCURSOR
LANGUAGE plpgsql
AS $$
DECLARE
    vCURSOR REFCURSOR;
BEGIN
    OPEN vCURSOR FOR
        SELECT * FROM SCORE_REC WHERE STUDENT_NO = p_STUDENT_NO;
    RETURN vCURSOR;
END;
$$;"""
        out = rewrite_type_self(src)
        # 保留原有 p_STUDENT_NO
        assert "p_STUDENT_NO NUMERIC" in out, f"原参数丢失: {out}"
        # 加 i_self REF_CURSOR_T
        assert "i_self REF_CURSOR_T" in out, f"未加 i_self 参数: {out}"

    def test_multiple_self_refs_in_body(self):
        src = """CREATE OR REPLACE FUNCTION GET_LETTER_GRADE_of_SCORE_RECORD()
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
BEGIN
    IF SELF_SCORE_RECORD_SCORE >= 90 THEN
        RETURN 'A';
    ELSIF SELF_SCORE_RECORD_SCORE >= 80 THEN
        RETURN 'B';
    END IF;
END;
$$;"""
        out = rewrite_type_self(src)
        # 全部 SELF 引用应被替换
        assert out.count("SELF_SCORE_RECORD_") == 0
        assert out.count("i_self.SCORE") == 2


# ===========================================================================
# 完整 6 函数改写（真实产物）
# ===========================================================================
REAL_TYPES_SQL = """-- Header

CREATE TYPE SCORE_RECORD AS (
    CNT             NUMERIC,
    NAIYOU          VARCHAR(1000),
    IS_PASS         BIGINT,
    SCORE           NUMERIC(5,2));

CREATE OR REPLACE FUNCTION GET_STATUS_of_SCORE_RECORD()
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
BEGIN
    IF SELF_SCORE_RECORD_IS_PASS = 1 THEN
        RETURN 'pass';
    END IF;
END;
$$;


CREATE OR REPLACE FUNCTION GET_LETTER_GRADE_of_SCORE_RECORD()
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
BEGIN
    IF SELF_SCORE_RECORD_SCORE >= 90 THEN
        RETURN 'A';
    ELSIF SELF_SCORE_RECORD_SCORE >= 80 THEN
        RETURN 'B';
    ELSIF SELF_SCORE_RECORD_SCORE >= 70 THEN
        RETURN 'C';
    END IF;
END;
$$;


CREATE TYPE STUDENT_RECORD AS (
    STUDENT_NO      BIGINT,
    BIRTH_DATE      BIGINT,
    GENDER          BIGINT,
    RANK_NO         BIGINT);

CREATE OR REPLACE FUNCTION GET_AGE_of_STUDENT_RECORD()
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$
DECLARE
    vBIRTH_YEAR NUMERIC;
    vNOW_YEAR NUMERIC;
BEGIN
    vBIRTH_YEAR := (SUBSTRING((SELF_STUDENT_RECORD_BIRTH_DATE)::varchar FROM 1 FOR 4))::NUMERIC;
    vNOW_YEAR := (TO_CHAR(CURRENT_TIMESTAMP, 'YYYY'))::NUMERIC;
    RETURN vNOW_YEAR - vBIRTH_YEAR;
END;
$$;


CREATE OR REPLACE FUNCTION GET_GENDER_NAME_of_STUDENT_RECORD()
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
BEGIN
    IF SELF_STUDENT_RECORD_GENDER = 1 THEN
        RETURN 'M';
    ELSIF SELF_STUDENT_RECORD_GENDER = 2 THEN
        RETURN 'F';
    END IF;
END;
$$;


CREATE OR REPLACE FUNCTION GET_RANK_STR_of_STUDENT_RECORD()
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN (SELF_STUDENT_RECORD_RANK_NO)::varchar || '位';
END;
$$;"""


class TestRealTypesSql:
    def test_no_self_xxx_remaining(self):
        out = rewrite_type_self(REAL_TYPES_SQL)
        # 所有 SELF_xxx 引用应被替换
        for m in _SELF_REF_RE.finditer(out):
            pytest.fail(f"SELF 引用未替换: {out[max(0,m.start()-20):m.end()+20]!r}")
        # 6 个函数签名都加 i_self 参数
        assert out.count("i_self SCORE_RECORD") == 2
        assert out.count("i_self STUDENT_RECORD") == 3
        # 函数体里有 i_self. 字段访问
        assert out.count("i_self.IS_PASS") == 1
        assert out.count("i_self.SCORE") == 3  # GET_LETTER_GRADE 3 个 IF
        assert out.count("i_self.BIRTH_DATE") == 1
        assert out.count("i_self.GENDER") == 2
        assert out.count("i_self.RANK_NO") == 1


# ===========================================================================
# 端到端：真实产物
# ===========================================================================
@pytest.mark.skipif(
    not (REPO_ROOT / "oracle_poc/output/TYPE/001_TYPES.sql").exists(),
    reason="output not present",
)
class TestOraclePocOutput:
    def test_no_self_xxx_in_types(self):
        text = (REPO_ROOT / "oracle_poc/output/TYPE/001_TYPES.sql").read_text(
            encoding="utf-8", errors="ignore"
        )
        # 排除注释行
        for line in text.splitlines():
            if line.lstrip().startswith("--"):
                continue
            for m in _SELF_REF_RE.finditer(line):
                pytest.fail(f"TYPE 文件残留 SELF 引用: {line!r}")

    def test_all_extracted_functions_have_self_param(self):
        text = (REPO_ROOT / "oracle_poc/output/TYPE/001_TYPES.sql").read_text(
            encoding="utf-8", errors="ignore"
        )
        out = rewrite_type_self(text)
        # 6 个提取函数都应有 i_self 参数
        for fn_name, type_name in [
            ("GET_STATUS_of_SCORE_RECORD", "SCORE_RECORD"),
            ("GET_LETTER_GRADE_of_SCORE_RECORD", "SCORE_RECORD"),
            ("GET_AGE_of_STUDENT_RECORD", "STUDENT_RECORD"),
            ("GET_GENDER_NAME_of_STUDENT_RECORD", "STUDENT_RECORD"),
            ("GET_RANK_STR_of_STUDENT_RECORD", "STUDENT_RECORD"),
        ]:
            assert re.search(
                rf"{fn_name}\s*\([^)]*i_self\s+{type_name}",
                out, re.IGNORECASE,
            ), f"{fn_name} 未加 i_self {type_name} 参数"
