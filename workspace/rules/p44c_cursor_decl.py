"""P4-4c 阶段 7：PL/SQL 游标声明 → PG plpgsql 游标声明（两种形态）

PG 15.19 live 实测（改本规则前请先复测，别凭记忆改）：

A. 绑定游标声明的**词序**不同
   - Oracle/产物现写法 ``CURSOR c1 FOR SELECT ...;`` → **CREATE 失败**：
     ``ERROR: 语法错误 在 "FOR" 或附近的`` /
     ``CONTEXT: 无效的类型名字 "c1 FOR SELECT ..."``
     （PG 把 ``CURSOR`` 当成了类型名，把 ``c1`` 当成了变量名）
   - PG 正确写法 ``c1 CURSOR FOR SELECT ...;`` → CREATE 成功，首调正确取到 3 行
     —— PG 语法是 ``name [ [ NO ] SCROLL ] CURSOR [ (args) ] FOR query``，**名字在前**

B. 「记录变量」不能用游标名当类型
   - ``R_STUDENT c1;``（``c1`` 是绑定游标）→ CREATE 失败：``类型 "c1" 不存在``
   - ``R_STUDENT RECORD;`` → CREATE 成功，首调正确取到 3 行
   —— PL/pgSQL 没有"游标行类型"，``FETCH c INTO R`` 要的是 record，不是 cursor

只改 DECLARE 段里的**声明**；``OPEN c;`` / ``CLOSE c;`` / ``FETCH c INTO r;``
是语句不是声明，一律不动。
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

# A) `CURSOR <name> FOR|IS`  →  `<name> CURSOR FOR`
_CURSOR_DECL = re.compile(
    r"^(\s*)CURSOR\s+(\w+)\s+(FOR|IS)\b", re.IGNORECASE | re.MULTILINE)
# 语句关键字（不是声明）—— 排除 OPEN/CLOSE/FETCH 等以免误伤
_STATEMENT_KW = {
    "OPEN", "CLOSE", "FETCH", "FOR", "LOOP", "IF", "WHILE", "RETURN",
    "RAISE", "EXECUTE", "PERFORM", "BEGIN", "END", "SELECT", "INSERT",
    "UPDATE", "DELETE", "COMMIT", "ROLLBACK", "GET", "RAISE", "CALL",
}
# B) `<var> <cursorName>;` —— 行内只有一个声明，且类型名是本文件的游标
_VAR_DECL = re.compile(r"^(\s*)(\w+)\s+(\w+)\s*;\s*$", re.MULTILINE)


def _cursor_names(stripped: str) -> set:
    names = set()
    for m in re.finditer(
            r"\bCURSOR\s+(\w+)\s+(?:FOR|IS|CURSOR)\b", stripped, re.IGNORECASE):
        names.add(m.group(1).upper())
    # 也收集 PG 正确写法 `name CURSOR FOR`
    for m in re.finditer(
            r"^\s*(\w+)\s+CURSOR\s+(?:FOR\b|SCROLL|\()", stripped,
            re.IGNORECASE | re.MULTILINE):
        names.add(m.group(1).upper())
    return names


def scan_cursor_decls(sql: str) -> dict:
    """只统计不改文本，供转换脚本打印与单测断言。"""
    stripped, _restore = _strip_strings_and_comments(sql)
    cursors = _cursor_names(stripped)
    order = len(_CURSOR_DECL.findall(stripped))
    recs = 0
    for m in _VAR_DECL.finditer(stripped):
        var, typ = m.group(2), m.group(3)
        if var.upper() in _STATEMENT_KW or typ.upper() in cursors:
            if var.upper() not in _STATEMENT_KW and typ.upper() in cursors:
                recs += 1
    return {"order_fixed": order, "record_fixed": recs}


def fix_cursor_decls(sql: str) -> str:
    """修正游标声明词序 + 游标类型记录变量。"""
    stripped, restore = _strip_strings_and_comments(sql)
    cursors = _cursor_names(stripped)

    # A) 词序：CURSOR name FOR -> name CURSOR FOR
    result = _CURSOR_DECL.sub(
        lambda m: f"{m.group(1)}{m.group(2)} CURSOR FOR", stripped)

    # B) `var <cursor>;` -> `var RECORD;`（跳过语句关键字行）
    def _fix_rec(m):
        indent, var, typ = m.group(1), m.group(2), m.group(3)
        if var.upper() in _STATEMENT_KW:
            return m.group(0)
        if typ.upper() in cursors:
            return f"{indent}{var} RECORD;"
        return m.group(0)

    result = _VAR_DECL.sub(_fix_rec, result)
    return _restore_placeholders(result, restore)


def apply_p44c_cursor_decl(sql: str) -> str:
    return fix_cursor_decls(sql)