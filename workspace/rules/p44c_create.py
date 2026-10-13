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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rules.exception_map import (  # noqa: E402
    _strip_strings_and_comments,
    _restore_placeholders,
)


# 匹配 CREATE OR REPLACE FUNCTION <name>(<params>)
_CREATE_REPLACE_RE = re.compile(
    r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+(\w+)\s*\(([^)]*)\)",
    re.IGNORECASE,
)


def _extract_pg_types(params_str: str) -> list:
    """从参数列表提取 PG 类型（去 IN/INOUT/DEFAULT/NOT NULL）。"""
    params = [p.strip() for p in params_str.split(",") if p.strip()]
    types = []
    for p in params:
        # 去掉 IN/INOUT/OUT/DEFAULT/NOT NULL 等
        # 提取 TYPE（参数名 后面的）
        m = re.search(r"\)\s*$", p)  # VARCHAR(100)
        # 简化：取最后一个 word
        parts = p.split()
        # 找类型关键字
        pg_types = {"NUMERIC", "VARCHAR", "TEXT", "BOOLEAN", "INTEGER",
                    "BIGINT", "INT", "DATE", "TIMESTAMP", "JSON", "JSONB",
                    "BYTEA", "UUID", "FLOAT", "DOUBLE", "REAL", "SMALLINT",
                    "DECIMAL", "MONEY", "CHAR", "RECORD"}
        last_type = None
        for word in parts:
            if word.upper().split("(")[0] in pg_types:
                last_type = word.upper()
        if last_type:
            # 去掉参数修饰符
            # VARCHAR(100) → VARCHAR（保留括号）
            type_with_size = None
            for word in parts:
                if word.upper().split("(")[0] in pg_types:
                    type_with_size = word
                    break
            if type_with_size:
                types.append(type_with_size.lower())
            else:
                types.append(last_type.lower())
    return types


def add_drop_before_replace(sql: str) -> str:
    """在每个 CREATE OR REPLACE FUNCTION 前插入 DROP FUNCTION IF EXISTS。"""
    stripped, restore = _strip_strings_and_comments(sql)

    def _replace(m: re.Match) -> str:
        name = m.group(1)
        params = m.group(2)
        types = _extract_pg_types(params)
        if not types:
            return m.group(0)
        type_list = ", ".join(types)
        return f"DROP FUNCTION IF EXISTS {name.lower()}({type_list});\n{m.group(0)}"

    out = _CREATE_REPLACE_RE.sub(_replace, stripped)
    return _restore_placeholders(out, restore)


def apply_p44c_create(sql: str) -> str:
    return add_drop_before_replace(sql)
