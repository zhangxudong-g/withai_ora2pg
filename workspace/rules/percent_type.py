"""
P2-2: %TYPE 查表展开

设计：
- `build_type_map_from_table_sql(sql)`：从 CREATE TABLE 文件建 `表.列 → PG 类型` 字典
- `resolve_percent_types(sql, type_map)`：仅在 DECLARE 块内替换 `table.col%TYPE` 形式
  * 找到：替换为 type_map 中的 PG 类型
  * 找不到：**保留原文 + warning**（与 P2-1 严格策略区分 — P2-2 是宽松+警告）
- 仅处理 `table.col%TYPE` 限定形式（var%TYPE 无表名属另一任务，不在本期范围）

实现要点：
- DECLARE 块用"块定位"识别：`DECLARE ... BEGIN` 之间（不区分大小写）
  * 函数可能有多个 DECLARE 块（嵌套函数、多个 CREATE FUNCTION）
  * 用正则定位 DECLARE 起点、平衡 BEGIN/END 找匹配的 BEGIN
  * 简化策略：找到 DECLARE 后找最近的 BEGIN（不跨函数），BEGIN 之前的部分就是 DECLARE 块
- 大小写归一：表名/列名统一大写存储，匹配时也归一
- 字符串/注释内不替换：单引号字符串、`--` 行注释、`/* */` 块注释
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple


# 类型映射的键：(TABLE_UPPER, COL_UPPER) → "PG_TYPE"（保持原大小写）
TypeMap = Dict[Tuple[str, str], str]


# ---------------------------------------------------------------------------
# 1. 建表：从 CREATE TABLE SQL 解析 table.col → type
# ---------------------------------------------------------------------------

# 匹配 CREATE TABLE NAME ( ... ) 块。`(?s)` 让 . 跨行
_CREATE_TABLE_RE = re.compile(
    r"\bCREATE\s+(?:GLOBAL\s+)?(?:TEMPORARY\s+)?TABLE\s+"
    r"(?P<name>\w+)\s*\((?P<body>.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)

# 匹配列定义：COL TYPE [constraints] ， 或  CONSTRAINT ...
# 例: STUDENT_NO BIGINT NOT NULL,
#     CONFIG_VALUE VARCHAR(1000),
#     CONSTRAINT T_PK PRIMARY KEY (X)
_COLUMN_DEF_RE = re.compile(
    r"""
    (?:^|,)\s*                 # 起始或逗号
    (?P<constraint>CONSTRAINT)? # 可能是约束定义
    \s*
    (?P<col>\w+)               # 列名（或约束名）
    \s+
    (?P<type>\w+               # 类型关键字
        (?:\s*\([^)]*\))?     # 可选长度/精度
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def build_type_map_from_table_sql(sql: str) -> TypeMap:
    """解析 CREATE TABLE 文件，建 (TABLE, COL) → PG_TYPE 字典。

    大小写归一：键统一大写（PG 默认折叠为小写但 unquoted 标识符大写存储是 Oracle 习惯）
    """
    result: TypeMap = {}
    for m in _CREATE_TABLE_RE.finditer(sql):
        table = m.group("name").upper()
        body = m.group("body")
        for cm in _COLUMN_DEF_RE.finditer(body):
            # 跳过 CONSTRAINT 声明
            if cm.group("constraint"):
                continue
            col = cm.group("col").upper()
            # 进一步校验：col 不能是约束关键字 (PRIMARY, FOREIGN, UNIQUE, CHECK, INDEX)
            if col.upper() in ("PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "INDEX", "KEY"):
                continue
            type_str = cm.group("type").strip()
            # 进一步校验：type 必须是常见 PG 类型关键字
            upper = type_str.upper().split("(")[0].strip()
            if upper not in _KNOWN_PG_TYPES:
                # 不是类型声明（如某些情况下误匹配），跳过
                continue
            result[(table, col)] = type_str.upper()
    return result


# 常见 PG 类型关键字（用于过滤列定义中的非类型行）
_KNOWN_PG_TYPES = {
    "BIGINT", "INT", "INTEGER", "SMALLINT", "TINYINT",
    "NUMERIC", "DECIMAL", "REAL", "DOUBLE", "FLOAT",
    "VARCHAR", "CHAR", "CHARACTER", "TEXT", "NAME",
    "BOOLEAN", "BOOL",
    "DATE", "TIME", "TIMESTAMP", "TIMESTAMPTZ", "INTERVAL",
    "BYTEA", "UUID", "JSON", "JSONB", "XML",
    "MONEY",
}


# ---------------------------------------------------------------------------
# 2. 替换：DECLARE 块内的 table.col%TYPE → PG 类型
# ---------------------------------------------------------------------------

# 匹配 DECLARE 关键字（作为块的开始标志）
# 不匹配 "DECLARE" 嵌入到其他关键字的情形
_DECLARE_RE = re.compile(r"\bDECLARE\b", re.IGNORECASE)

# 匹配 BEGIN 关键字（作为块的结束标志）
# 不匹配 "BEGIN" 嵌入到其他关键字的情形（如 BEGIN_TRANSACTION）
_BEGIN_RE = re.compile(r"\bBEGIN\b", re.IGNORECASE)

# 匹配 table.col%TYPE 模式（仅在 DECLARE 块内匹配）
# 词边界：前面不是 \w（避免 var.flag%TYPE 这种）
# 后面是 \b（%TYPE 是关键字）
_PERCENT_TYPE_RE = re.compile(
    r"(?<![\w.])(\w+)\.(\w+)%TYPE\b",
    re.IGNORECASE,
)

# 裸 col%TYPE（无表名前缀）：如 "v_MAX ROW_NO%TYPE"
# 前面不能是 \w.（避免把 table.col%TYPE 重复匹配）
_BARE_PERCENT_TYPE_RE = re.compile(
    r"(?<![\w.])(\w+)%TYPE\b",
    re.IGNORECASE,
)


def _strip_strings_and_comments(sql: str) -> str:
    """把字符串/注释替换为空格（保留位置），便于后续定位 DECLARE 块时不踩进去。

    注意：只为了 DECLARE/BEGIN 关键字定位，**不**用于匹配 %TYPE。
    %TYPE 的匹配会用更精确的范围。
    """
    out = []
    i = 0
    n = len(sql)
    while i < n:
        c = sql[i]
        # 单引号字符串（含转义 ''）
        if c == "'":
            out.append(" ")
            i += 1
            while i < n:
                if sql[i] == "'":
                    # 检查转义 ''
                    if i + 1 < n and sql[i + 1] == "'":
                        out.append("  ")
                        i += 2
                        continue
                    out.append(" ")
                    i += 1
                    break
                out.append(" " if sql[i] != "\n" else "\n")
                i += 1
            continue
        # 双引号标识符（占位）
        if c == '"':
            out.append(" ")
            i += 1
            while i < n and sql[i] != '"':
                out.append(" " if sql[i] != "\n" else "\n")
                i += 1
            if i < n:
                out.append(" ")
                i += 1
            continue
        # $...$ dollar quoting（PG plpgsql body delimiters）
        # 重要：$$ ... $$ 是函数体定界符，里面的内容是**代码**而不是字符串。
        # 里面的 BEGIN/DECLARE/%TYPE 都要被扫描到。
        # 所以这里**不处理** dollar quoting，让字符原样透传。
        # 行注释
        if c == "-" and i + 1 < n and sql[i + 1] == "-":
            out.append("  ")
            i += 2
            while i < n and sql[i] != "\n":
                out.append(" ")
                i += 1
            continue
        # 块注释 /* ... */
        if c == "/" and i + 1 < n and sql[i + 1] == "*":
            out.append("  ")
            i += 2
            while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                out.append("\n" if sql[i] == "\n" else " ")
                i += 1
            if i + 1 < n:
                out.append("  ")
                i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _find_declare_blocks(stripped_sql: str) -> List[Tuple[int, int]]:
    """在去除了字符串/注释的 SQL 中定位 (start, end) DECLARE 块。

    一个 DECLARE 块 = DECLARE 起点到匹配的 BEGIN 终点（不含 BEGIN）。
    用最近 BEGIN 即可（PL/pgSQL 函数结构是 DECLARE ... BEGIN ... END；
    不会在 DECLARE 块内嵌另一个 BEGIN/END）。
    """
    blocks: List[Tuple[int, int]] = []
    for dm in _DECLARE_RE.finditer(stripped_sql):
        decl_start = dm.start()
        # 找 DECLARE 之后的最近 BEGIN
        bm = _BEGIN_RE.search(stripped_sql, decl_start + len("DECLARE"))
        if bm is None:
            continue  # 孤立 DECLARE（如 DECLARE 错误）—— 跳过
        blocks.append((decl_start, bm.start()))
    return blocks


def resolve_percent_types(
    sql: str,
    type_map: TypeMap,
) -> Tuple[str, List[str]]:
    """仅在 DECLARE 块内替换 `table.col%TYPE` → PG_TYPE。

    找不到时：保留原文 + warning（不抛错，**P2-2 宽松策略**）。

    Args:
        sql: 函数/过程体（可能含多个 CREATE FUNCTION，各自独立）
        type_map: (TABLE_UPPER, COL_UPPER) → "PG_TYPE"

    Returns:
        (new_sql, warnings)
    """
    if not sql:
        return sql, []
    # type_map 为空时也要走完整逻辑，以便生成 warning（保留原文 + 警告）

    # 归一化 type_map 键
    norm_map: Dict[Tuple[str, str], str] = {
        (t.upper(), c.upper()): v for (t, c), v in type_map.items()
    }

    # 用 _strip_strings_and_comments 标定 DECLARE/BEGIN 位置（不改变长度）
    stripped = _strip_strings_and_comments(sql)
    blocks = _find_declare_blocks(stripped)
    if not blocks:
        return sql, []

    # 为避免在字符串/注释中误匹配，组装 (start, end, is_real_text) 掩码
    # stripped[i] 是空格则表示原 sql[i] 在字符串/注释里，不应被匹配
    def _is_real_text(start: int, end: int) -> bool:
        """[start, end) 区间的字符是否都在真实代码里（不在字符串/注释里）"""
        return all(c not in (" ", "\t") for c in stripped[start:end])

    # 建 col → PG_TYPE 索引（用于裸 col%TYPE）：取第一个匹配的列
    col_to_type: Dict[str, str] = {}
    for (_t, c), pg in norm_map.items():
        col_to_type.setdefault(c, pg)

    # 收集替换与未解析（按 start 升序）
    replacements: List[Tuple[int, int, str]] = []  # (start, end, repl)
    unresolved: List[Tuple[int, str]] = []  # (start, table.col)

    for (b_start, b_end) in blocks:
        # 先处理 table.col%TYPE，把它们标记为已用（避免被裸 col%TYPE 再命中）
        used_ranges: List[Tuple[int, int]] = []
        for pm in _PERCENT_TYPE_RE.finditer(sql, b_start, b_end):
            if not _is_real_text(pm.start(), pm.end()):
                continue
            table = pm.group(1).upper()
            col = pm.group(2).upper()
            pg_type = norm_map.get((table, col))
            if pg_type is None:
                unresolved.append((pm.start(), f"{table}.{col}"))
                continue
            replacements.append((pm.start(), pm.end(), pg_type))
            used_ranges.append((pm.start(), pm.end()))

        # 再处理裸 col%TYPE（跳过已被 table.col%TYPE 命中的区间）
        for pm in _BARE_PERCENT_TYPE_RE.finditer(sql, b_start, b_end):
            if not _is_real_text(pm.start(), pm.end()):
                continue
            # 跳过在 table.col%TYPE 命中区间内的（如 "T.ROW_NO" 的 ROW_NO 部分）
            if any(rs <= pm.start() < re_ for rs, re_ in used_ranges):
                continue
            col = pm.group(1).upper()
            pg_type = col_to_type.get(col)
            if pg_type is None:
                unresolved.append((pm.start(), col))
                continue
            replacements.append((pm.start(), pm.end(), pg_type))

    # 倒序应用（保证偏移正确）
    if replacements:
        replacements.sort(key=lambda x: x[0], reverse=True)
        out = sql
        for start, end, repl in replacements:
            out = out[:start] + repl + out[end:]
    else:
        out = sql

    # 生成 warning
    warnings = [
        f"%TYPE 无法查表展开（保留原文）: {pat}"
        for _, pat in unresolved
    ]
    return out, warnings
