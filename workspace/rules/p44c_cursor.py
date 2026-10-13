"""P4-4c 阶段 6：Oracle 动态游标 `OPEN c FOR <动态 SQL 表达式>` → PG 加 `EXECUTE`

PG 15.19 live 实测（改本规则前请先复测，别凭记忆改）：

===========================  ====================================  ==========
产物原写法                   PG 结果                                修法
===========================  ====================================  ==========
``OPEN c FOR f(a, b);``     ❌ 语法错误 在 "f" 附近的（CREATE 失败） ``FOR EXECUTE f(a,b)``
``OPEN c FOR v_sql;``       ❌ 语法错误 在 "v_sql" 附近的             ``FOR EXECUTE v_sql``
``OPEN c FOR 'SELECT ...';``❌ 语法错误 在 "'SELECT..." 附近的        ``FOR EXECUTE 'SELECT ...'``
``OPEN c FOR SELECT ...;``  ✅ 正常                                   不动
===========================  ====================================  ==========

修法实测：三种加 ``EXECUTE`` 后 CREATE 成功且**首调返回 3 行**（live 验证正确）。

PL/pgSQL 的 ``OPEN ... FOR`` 后面只接受**真查询**或 ``EXECUTE <字符串表达式>``，
不接受函数调用 / 变量 / 字符串字面量。Oracle「函数或变量返回 SQL 文本，OPEN 时用」
的惯用法在 PG 里的标准映射就是 ``OPEN ... FOR EXECUTE <表达式>``。

只处理「非查询」形态：以 SELECT/WITH/INSERT/UPDATE/DELETE/MERGE/VALUES/TABLE
开头的真查询**保持不动**；已有 ``EXECUTE`` 的不动（幂等）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rules.exception_map import (  # noqa: E402
    _restore_placeholders,
    _strip_strings_and_comments,
)

# 真查询开头 —— 这些后面不能加 EXECUTE
_QUERY_LEAD = (
    r"SELECT|WITH|INSERT|UPDATE|DELETE|MERGE|VALUES|TABLE|EXECUTE|"
    r"DECLARE|SHOW|REFRESH|ANALYZE|VACUUM|GRANT|REVOKE|TRUNCATE|COPY|CALL|DO"
)
# OPEN <cursor> FOR <表达式>；已有 EXECUTE 或真查询的用 (?!) 排除
_OPEN_FOR_EXPR = re.compile(
    r"\bOPEN\s+(\w+)\s+FOR\s+"
    r"(?!\s*(?:" + _QUERY_LEAD + r")\b)"
    r"(?!\s*EXECUTE\b)",
    re.IGNORECASE,
)


def scan_open_for_execute(sql: str) -> dict:
    """只统计不改文本，供转换脚本打印与单测断言。"""
    stripped, _restore = _strip_strings_and_comments(sql)
    sites = [f"OPEN {m.group(1)} FOR" for m in _OPEN_FOR_EXPR.finditer(stripped)]
    return {"count": len(sites), "sites": sites}


def fix_open_for_execute(sql: str) -> str:
    """给非查询形态的 `OPEN c FOR <表达式>` 补上 `EXECUTE`。"""
    stripped, restore = _strip_strings_and_comments(sql)
    if not _OPEN_FOR_EXPR.search(stripped):
        return _restore_placeholders(stripped, restore)
    result = _OPEN_FOR_EXPR.sub(
        lambda m: f"OPEN {m.group(1)} FOR EXECUTE ", stripped)
    return _restore_placeholders(result, restore)


def apply_p44c_cursor(sql: str) -> str:
    return fix_open_for_execute(sql)