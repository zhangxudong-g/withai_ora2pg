"""P4-4c 阶段 4：CREATE OR REPLACE 改写为 DROP IF EXISTS + CREATE

PG bug：CREATE OR REPLACE FUNCTION 在同名同参（不同参名）时报错
"无法改变变量"。oracle_poc 产物中嵌套子程序提升后产生重载
（参数名不同），触发该 bug。

策略：把每个 CREATE OR REPLACE FUNCTION 前面加
DROP FUNCTION IF EXISTS <name>(<pg_types>);
CREATE OR REPLACE FUNCTION <name>...;
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

from rules.p44c_create import (  # noqa: E402
    add_drop_before_replace,
    apply_p44c_create,
)


class TestAddDropBeforeReplace:
    def test_simple(self):
        src = """CREATE OR REPLACE FUNCTION build_sql(p1 NUMERIC, p2 NUMERIC)
RETURNS VARCHAR LANGUAGE plpgsql AS $$ BEGIN RETURN 'x'; END$$;"""
        out = add_drop_before_replace(src)
        assert "DROP FUNCTION IF EXISTS build_sql(numeric, numeric);" in out
        # 保留原 CREATE OR REPLACE
        assert "CREATE OR REPLACE FUNCTION build_sql" in out

    def test_3_params(self):
        src = """CREATE OR REPLACE FUNCTION add_cond(p1 VARCHAR, p2 NUMERIC, p3 NUMERIC)
RETURNS VARCHAR LANGUAGE plpgsql AS $$ BEGIN RETURN 'x'; END$$;"""
        out = add_drop_before_replace(src)
        assert "DROP FUNCTION IF EXISTS add_cond(varchar, numeric, numeric);" in out

    def test_default_value(self):
        src = """CREATE OR REPLACE FUNCTION f(p1 NUMERIC DEFAULT NULL)
RETURNS NUMERIC LANGUAGE plpgsql AS $$ BEGIN RETURN 1; END$$;"""
        out = add_drop_before_replace(src)
        # 包含 DEFAULT NULL 的参数化简为 numeric
        assert "DROP FUNCTION IF EXISTS f(numeric);" in out

    def test_no_create_unchanged(self):
        src = "SELECT 1;"
        out = add_drop_before_replace(src)
        assert out == src


@pytest.mark.skipif(
    not (REPO_ROOT / "oracle_poc/output/FUNCTION/00_FUNCTION.sql").exists(),
    reason="output not present",
)
class TestOraclePocOutput:
    def test_repeat_signatures_have_drop(self):
        """P4-4d 后：嵌套函数名已加父级前缀，不再有同名同参冲突"""
        text = (REPO_ROOT / "oracle_poc/output/FUNCTION/00_FUNCTION.sql").read_text(
            encoding="utf-8", errors="ignore"
        )
        new = apply_p44c_create(text)
        # 确认每个 CREATE 前都有对应的 DROP（无同名冲突）
        create_count = len(re.findall(r"^CREATE OR REPLACE (?:FUNCTION|PROCEDURE) ", new, re.MULTILINE))
        drop_count = len(re.findall(r"^DROP (?:FUNCTION|PROCEDURE) IF EXISTS ", new, re.MULTILINE))
        # 每个 CREATE 都应有对应的 DROP IF EXISTS
        assert drop_count >= create_count, f"DROP count {drop_count} < CREATE count {create_count}"
