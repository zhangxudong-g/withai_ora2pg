"""
SQL 语句切分

原实现用 `CREATE FUNCTION ... \\$\\$\\s*LANGUAGE` 正则切分，有三个硬伤：
1. 不识别 `$func$` / `$BODY$` 等带标签的 dollar-quote，只有 `$$` 能切；
2. `.*?` 遇到函数体内部的 `$$` 会提前截断；
3. 切出来的"语句"仍可能含多条，无法逐条安装。

这里实现一个逐字符状态机，正确处理：
- 单引号字符串（`''` 转义）
- 双引号标识符（`""` 转义）
- `--` 行注释
- `/* */` 块注释（PL/pgSQL 支持嵌套）
- `$tag$` dollar-quote（标签可为空）
- 分号作为语句边界
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

# 顶层对象的类型（按 Oracle -> PG 创建顺序的依赖排名，越小越先建）
KIND_RANK = {
    "SCHEMA": 0,
    "SEQUENCE": 1,
    "TYPE": 2,
    "DOMAIN": 2,
    "TABLE": 3,
    "VIEW": 4,
    "MATERIALIZED VIEW": 4,
    "INDEX": 5,
    "FUNCTION": 6,
    "PROCEDURE": 6,
    "TRIGGER": 7,
    "PACKAGE": 8,
    "PACKAGE BODY": 8,
    "SYNONYM": 9,
    "COMMENT": 10,
    "GRANT": 11,
    "DATA": 12,
    "OTHER": 50,
}

_CREATE_RE = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?"
    r"(?:TEMP\s+|TEMPORARY\s+|GLOBAL\s+TEMPORARY\s+|UNLOGGED\s+)*"
    r"(?P<kind>TABLE|FUNCTION|PROCEDURE|VIEW|MATERIALIZED\s+VIEW|SEQUENCE|TYPE"
    r"|DOMAIN|INDEX|TRIGGER|SCHEMA|PACKAGE)\b",
    re.IGNORECASE,
)
_NAME_RE = re.compile(r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?[\w\s]*?"
                      r"(?:TABLE|FUNCTION|PROCEDURE|VIEW|SEQUENCE|TYPE|DOMAIN|INDEX|TRIGGER)\s+"
                      r"(?:IF\s+NOT\s+EXISTS\s+)?([\w.\"]+)", re.IGNORECASE)


def split_statements(sql: str) -> List[str]:
    """
    按分号切分 SQL，返回去除首尾空白与尾部注释的语句列表。

    SQL*Plus 常用的单行 `/` 分隔符会被丢弃。
    """
    stmts: List[str] = []
    buf: List[str] = []
    i = 0
    n = len(sql)
    dollar_tag: Optional[str] = None   # 当前 dollar-quote 的结束标记，如 $$ 或 $BODY$
    block_depth = 0                     # 嵌套块注释深度

    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        # --- dollar-quote 内部：只找结束标记 ---
        if dollar_tag is not None:
            if ch == "$" and sql.startswith(dollar_tag, i):
                buf.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
                continue
            buf.append(ch)
            i += 1
            continue

        # --- 嵌套块注释 ---
        if block_depth > 0:
            if ch == "/" and nxt == "*":
                block_depth += 1
                buf.append("/*")
                i += 2
                continue
            if ch == "*" and nxt == "/":
                block_depth -= 1
                buf.append("*/")
                i += 2
                continue
            buf.append(ch)
            i += 1
            continue

        # --- 行注释 ---
        if ch == "-" and nxt == "-":
            j = sql.find("\n", i)
            if j == -1:
                break
            i = j          # 换行交给下一轮
            continue

        # --- 块注释开始 ---
        if ch == "/" and nxt == "*":
            block_depth = 1
            buf.append("  ")     # 用空白占位，保持长度
            i += 2
            continue

        # --- 单引号字符串 ---
        if ch == "'":
            buf.append(ch)
            i += 1
            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        buf.append("''")
                        i += 2
                        continue
                    buf.append("'")
                    i += 1
                    break
                buf.append(sql[i])
                i += 1
            continue

        # --- 双引号标识符 ---
        if ch == '"':
            buf.append(ch)
            i += 1
            while i < n:
                if sql[i] == '"':
                    if i + 1 < n and sql[i + 1] == '"':
                        buf.append('""')
                        i += 2
                        continue
                    buf.append('"')
                    i += 1
                    break
                buf.append(sql[i])
                i += 1
            continue

        # --- dollar-quote 开始：$tag$ ---
        if ch == "$":
            m = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", sql[i:])
            if m:
                dollar_tag = m.group(0)
                buf.append(dollar_tag)
                i += len(dollar_tag)
                continue

        # --- 语句边界 ---
        if ch == ";":
            stmts.append("".join(buf))
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    if buf:
        stmts.append("".join(buf))

    return [_clean(s) for s in stmts if _clean(s)]


def _clean(stmt: str) -> str:
    """去掉首尾空白，以及 SQL*Plus 的单行 `/`"""
    s = stmt.strip()
    while s.endswith("/") and (len(s) == 1 or s[-2] in " \t\r\n"):
        s = s[:-1].rstrip()
    return s


def classify(stmt: str) -> str:
    """判断语句类型（用于依赖排序和统计）"""
    head = stmt.lstrip()[:400]
    up = head.upper()

    if up.startswith("INSERT") or up.startswith("MERGE") or up.startswith("UPDATE") \
            or up.startswith("DELETE") or up.startswith("COPY"):
        return "DATA"
    if up.startswith("COMMENT ON"):
        return "COMMENT"
    if up.startswith("GRANT") or up.startswith("REVOKE"):
        return "GRANT"
    if up.startswith("SET ") or up.startswith("SELECT SETVAL"):
        return "OTHER"

    m = _CREATE_RE.match(head)
    if m:
        return re.sub(r"\s+", " ", m.group("kind").upper())
    return "OTHER"


def extract_object_name(stmt: str) -> str:
    """提取 CREATE 语句的对象名"""
    m = _NAME_RE.match(stmt[:600])
    if m:
        return m.group(1).strip('"')
    return ""


def sort_statements(statements: List[str]) -> List[str]:
    """
    按依赖顺序排序：表 -> 视图 -> 函数 -> 触发器 ...
    同类型内保持原顺序（用稳定排序）。
    """
    return sorted(statements, key=lambda s: KIND_RANK.get(classify(s), KIND_RANK["OTHER"]))


def count_blocks(stmt: str) -> Tuple[int, int, int, int]:
    """
    统计 plpgsql 块结构配对数量 (BEGIN, END, IF, END IF)

    注意：这是"结构配对"的粗筛，只在语句层做，不能替代 pglast 解析。
    """
    begin = len(re.findall(r"\bBEGIN\b", stmt, re.IGNORECASE))
    end = len(re.findall(r"\bEND\b", stmt, re.IGNORECASE))
    ifs = len(re.findall(r"\bIF\b", stmt, re.IGNORECASE))
    endifs = len(re.findall(r"\bEND\s+IF\b", stmt, re.IGNORECASE))
    return begin, end, ifs, endifs


# SQL*Plus 的 include 指令：`@@file` / `@@ file` / `@file` 都合法，
# @@ 与文件名之间允许没有空格
_SQLPLUS_INCLUDE_RE = re.compile(r"^\s*@@?\s*\S+", re.MULTILINE)
# 可执行 SQL 的判定关键字
_SQL_VERB_RE = re.compile(
    r"\b(CREATE|INSERT|UPDATE|DELETE|SELECT|ALTER|DROP|MERGE|COPY|TRUNCATE)\b",
    re.IGNORECASE,
)


def is_driver_script(text: str) -> bool:
    """
    判断是否为 SQL*Plus 驱动脚本（只含 @@include / PROMPT 等元命令）。

    这类文件（如 oracle_poc/01.SQL/CREATE_ALL.sql）不是可执行代码，
    送进 PG 必然报语法错，计进验证结果只会制造噪声。

    判据：含有 @@ include，且剥离注释后没有任何可执行 SQL 关键字。
    比"把 include 行删掉看是否为空"更稳——驱动脚本尾部常有
    PROMPT / SET / SPOOL 等元命令残留。
    """
    body = re.sub(r"--[^\n]*", "", text)
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    if "@@" not in body:
        return False
    return _SQL_VERB_RE.search(body) is None
