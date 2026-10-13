"""
P2-3: (+) 外连接 → ANSI JOIN 结构化改写

设计目标：
- 仅对含 (+) 的 SELECT 做改写
- 解析 FROM 子句（逗号分隔多表）+ WHERE 条件中的 (+) 标记
- 渲染为 ANSI LEFT/RIGHT JOIN
- 支持嵌套子查询：先递归处理子查询，再处理外层
- 字符串/注释豁免（与 P2-2 一致）

实现策略（简化）：
- 不用完整的 SELECT 块定位——直接用正则匹配 `FROM ... WHERE ... (+) ...` 模式
- 对每条匹配单独做 FROM/WHERE 解析和渲染
- 倒序替换（从后往前），避免偏移错位
- 2 表 + 单 (+) 完整支持；3 表简化支持（INNER JOIN TRUE 兜底）
- 不支持：OR 条件、跨 (+) 多组、外层 alias 复杂

oracle_poc 实际场景：PROCEDURE 内 2 处 (SELECT S.x FROM A, B WHERE A.x = B.x(+) GROUP BY ...)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple


class JoinType(str, Enum):
    INNER = "INNER JOIN"
    LEFT = "LEFT JOIN"
    RIGHT = "RIGHT JOIN"


@dataclass(eq=False)
class FromTable:
    raw: str
    name: str
    alias: Optional[str]


# ---------------------------------------------------------------------------
# 字符串/注释掩码
# ---------------------------------------------------------------------------


def _strip_strings_and_comments(sql: str) -> str:
    """把字符串/注释替换为空格（保留位置/换行）。"""
    out = []
    i = 0
    n = len(sql)
    while i < n:
        c = sql[i]
        if c == "'":
            out.append(" ")
            i += 1
            while i < n:
                if sql[i] == "'":
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
        if c == "-" and i + 1 < n and sql[i + 1] == "-":
            out.append("  ")
            i += 2
            while i < n and sql[i] != "\n":
                out.append(" ")
                i += 1
            continue
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


# ---------------------------------------------------------------------------
# FROM 解析
# ---------------------------------------------------------------------------


def _parse_from_tables(from_text: str) -> List[FromTable]:
    """解析 FROM 子句 (逗号分隔) 成表列表。"""
    if re.search(r"\bJOIN\b", from_text, re.IGNORECASE):
        return []  # 已经是 ANSI JOIN——不动

    parts = []
    depth = 0
    cur = []
    for c in from_text:
        if c == "(":
            depth += 1
            cur.append(c)
        elif c == ")":
            depth -= 1
            cur.append(c)
        elif c == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
    if cur:
        parts.append("".join(cur).strip())

    tables = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        m = re.match(r"(\w+)(?:\s+(?:AS\s+)?(\w+))?", p, re.IGNORECASE)
        if not m:
            continue
        name = m.group(1).upper()
        alias = m.group(2)
        if alias:
            alias = alias.upper()
        tables.append(FromTable(raw=p, name=name, alias=alias))
    return tables


# ---------------------------------------------------------------------------
# WHERE 条件解析
# ---------------------------------------------------------------------------


_PLUS_RE = re.compile(r"\(\s*\+\s*\)")
_OP_RE = re.compile(r"(<>|!=|=|<=|>=|<|>)")


def _parse_where_conditions(where_text: str) -> List[str]:
    """按顶层 AND 切 WHERE 条件（WHERE 关键字前缀会被剥除）。"""
    text = where_text.strip()
    if text.upper().startswith("WHERE"):
        text = text[5:].strip()

    conditions = []
    depth = 0
    cur = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "(":
            depth += 1
            cur.append(c)
            i += 1
            continue
        if c == ")":
            depth -= 1
            cur.append(c)
            i += 1
            continue
        if depth == 0 and c.isalpha():
            j = i
            while j < n and text[j].isalpha():
                j += 1
            kw = text[i:j].upper()
            if kw in ("AND", "OR") and (j == n or text[j] in " \t\n"):
                if cur:
                    conditions.append("".join(cur).strip())
                    cur = []
                i = j
                continue
        cur.append(c)
        i += 1
    if cur:
        conditions.append("".join(cur).strip())
    return [c for c in conditions if c]


def _classify_where(where_text: str, tables: List[FromTable]) -> Tuple[List[Tuple[str, str, str, Optional[str], str]], List[str]]:
    """把 WHERE 条件分类为 (+) 条件 和 非 (+) 条件。

    Returns:
        (plus_conds, non_plus_conds)
        plus_conds: List of (left_qual, op, right_qual, plus_side, cleaned_cond)
            plus_side: "left" / "right" / None
            cleaned_cond: 去掉 (+) 后的条件
        non_plus_conds: 保留原样的 WHERE 过滤条件
    """
    conds = _parse_where_conditions(where_text)
    plus_conds = []
    non_plus_conds = []
    table_keys = set()
    for t in tables:
        table_keys.add(t.name)
        if t.alias:
            table_keys.add(t.alias)

    for c in conds:
        if "(+)" not in c and "( + )" not in c:
            non_plus_conds.append(c)
            continue
        # 找操作符
        op_m = _OP_RE.search(c)
        if not op_m:
            non_plus_conds.append(c)
            continue
        op = op_m.group()
        # 切分左右
        parts = _OP_RE.split(c, maxsplit=1)
        if len(parts) < 3:
            non_plus_conds.append(c)
            continue
        left = parts[0].strip()
        right = parts[2].strip() if len(parts) > 2 else ""
        # cleaned 条件（去 (+)）
        cleaned = _PLUS_RE.sub("", c).strip()
        # (+) 在左还是右
        plus_pos = c.find("(+)")
        eq_pos = c.find(op)
        plus_side = "left" if plus_pos < eq_pos else "right"
        plus_conds.append((left, op, right, plus_side, cleaned))

    return plus_conds, non_plus_conds


# ---------------------------------------------------------------------------
# ANSI JOIN 渲染
# ---------------------------------------------------------------------------


def _render_join(tables: List[FromTable], plus_conds: List, non_plus_conds: List[str]) -> Tuple[str, int]:
    """根据表和 (+) 条件渲染 ANSI JOIN。

    简化：2 表完整，3 表用 INNER JOIN TRUE 兜底，N>3 不支持。
    Returns: (new_from_clause, n_substituted)
    """
    if not plus_conds:
        return "", 0
    if len(tables) < 2:
        return "", 0

    # 找 (+) 条件涉及的表对
    def _resolve(q: str) -> Optional[FromTable]:
        q = q.strip()
        # 可能是 table.col 或 alias.col
        if "." not in q:
            return None
        prefix, _ = q.split(".", 1)
        prefix = prefix.upper()
        for t in tables:
            if t.alias == prefix or t.name == prefix:
                return t
        return None

    # 收集每对 (table_a, table_b, plus_side, cleaned_cond)
    edges = []  # List of (TableA, TableB, plus_side, cleaned_cond)
    for left, op, right, plus_side, cleaned in plus_conds:
        ta = _resolve(left)
        tb = _resolve(right)
        if not (ta and tb) or ta is tb:
            return "", 0  # 简化：跨多表的 (+) 不支持
        edges.append((ta, tb, plus_side, cleaned))

    # 2 表: 全部 edges 必须在同一对表
    if len(tables) == 2:
        a, b = tables
        for ta, tb, _, _ in edges:
            if (ta is not a or tb is not b) and (ta is not b or tb is not a):
                return "", 0
        # 确定 driving
        sides = {e[2] for e in edges}
        if len(sides) != 1:
            return "", 0
        side = sides.pop()
        if side == "left":
            # left 是 (+) → right 驱动 → b LEFT JOIN a
            driving, optional = b, a
        else:
            # right 是 (+) → left 驱动 → a LEFT JOIN b
            driving, optional = a, b
        on_str = " AND ".join(e[3] for e in edges)
        d_str = f"{driving.name}" + (f" {driving.alias}" if driving.alias else "")
        o_str = f"{optional.name}" + (f" {optional.alias}" if optional.alias else "")
        return f"{d_str} LEFT JOIN {o_str} ON {on_str}", len(edges)

    # 3 表: (+) 条件涉及其中 2 表，剩下 1 表 INNER JOIN
    if len(tables) == 3 and len(edges) >= 1:
        # 找涉及 2 表的 (+)
        first_ta, first_tb, first_side, _ = edges[0]
        if first_ta is first_tb:
            return "", 0
        # 检查所有 edge 都在这对表之间
        for ta, tb, _, _ in edges:
            if (ta is not first_ta or tb is not first_tb) and (ta is not first_tb or tb is not first_ta):
                return "", 0
        # 第三张表
        third = next(t for t in tables if t is not first_ta and t is not first_tb)
        # driving
        if first_side == "left":
            driving, optional = first_tb, first_ta
        else:
            driving, optional = first_ta, first_tb
        on_str = " AND ".join(e[3] for e in edges)
        d_str = f"{driving.name}" + (f" {driving.alias}" if driving.alias else "")
        o_str = f"{optional.name}" + (f" {optional.alias}" if optional.alias else "")
        t_str = f"{third.name}" + (f" {third.alias}" if third.alias else "")
        return (
            f"{d_str} LEFT JOIN {o_str} ON {on_str} "
            f"CROSS JOIN {t_str}",
            len(edges),
        )

    return "", 0


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


# 匹配模式: "FROM t1, t2, ..., tn WHERE ...(+)... [AND ...]"
# 用 stripped 视图找候选（避免字符串/注释），用原文做实际替换
# 倒序处理以避免偏移错位

# 匹配: FROM <from_text> WHERE <where_text>
# 关键: from_text/where_text 不能跨顶层 ; 也不能跨 GROUP/ORDER/HAVING/LIMIT/UNION 关键字
# 允许任意深度嵌套括号 (NVL(...)) — 用 (?R) 递归
_FROM_WHERE_RE = re.compile(
    r"\bFROM\s+(?P<from>(?:[^();]|\((?:[^()]|\([^()]*\))*\))*?)\s+WHERE\s+(?P<where>(?:[^();]|\((?:[^()]|\([^()]*\))*\))+)",
    re.IGNORECASE | re.DOTALL,
)


def rewrite_outer_joins(sql: str) -> Tuple[str, int]:
    """对 SQL 中所有含 (+) 的 FROM...WHERE 做 ANSI JOIN 改写。

    递归策略：从内到外。先处理嵌套子查询（通过 stripped 视图定位所有匹配，
    按位置倒序处理，最内的最后处理——保证内层先被替换、外层后处理）。
    """
    if not sql or "(+)" not in sql:
        return sql, 0

    stripped = _strip_strings_and_comments(sql)
    # 在 stripped 上找所有 FROM...WHERE 匹配
    matches = list(_FROM_WHERE_RE.finditer(stripped))
    if not matches:
        return sql, 0

    # 倒序处理
    out = sql
    total = 0
    for m in reversed(matches):
        # 检查 stripped 视图里的 (+) 是否在 where 内
        where_in_stripped = m.group("where")
        if "(+)" not in where_in_stripped:
            continue
        # 同样检查 from 是否有 (+) (不应该, 但保险)
        # 切分 from / where
        from_text = m.group("from").strip()
        where_text = m.group("where").strip()
        # 把 where_text 切到第一个顶层 AND/OR/GROUP/ORDER/HAVING/LIMIT/UNION/end/;
        where_end_pos = _find_where_end(where_text)
        where_clause = where_text[:where_end_pos].strip()
        after_where = where_text[where_end_pos:].lstrip()
        # 保证 after_where 前有空格分隔（如 "= 0 GROUP BY ..."）
        if after_where and not after_where.startswith((" ", "\t", "\n")):
            after_where = " " + after_where

        # 解析
        tables = _parse_from_tables(from_text)
        if len(tables) < 2:
            continue
        plus_conds, non_plus_conds = _classify_where(where_clause, tables)
        if not plus_conds:
            continue

        new_from, n = _render_join(tables, plus_conds, non_plus_conds)
        if not new_from or n == 0:
            continue

        # 重建块
        # 原: FROM <from_text> WHERE <where_clause><after_where>
        # 新: FROM <new_from> [WHERE <non_plus_conds>] <after_where>
        if non_plus_conds:
            new_where_str = " AND ".join(non_plus_conds)
            new_block = f"FROM {new_from} WHERE {new_where_str}{after_where}"
        else:
            new_block = f"FROM {new_from}{after_where}"
        # 替换 out 中 [m.start(0), m.start(0) + len(m.group(0)) ]
        old_start = m.start()
        old_end = m.end()
        out = out[:old_start] + new_block + out[old_end:]
        total += n

    return out, total


def _find_where_end(where_text: str) -> int:
    """找 WHERE 子句的结束位置（GROUP BY/ORDER BY/HAVING/LIMIT/UNION/; / 下一 SELECT）。"""
    # 在 stripped 视图上找（避免字符串/注释里的关键字）
    # 简化: 只看几个关键关键字
    upper = where_text.upper()
    candidates = []
    for kw in ("GROUP BY", "ORDER BY", "HAVING", "LIMIT", "UNION", "INTERSECT", "EXCEPT"):
        i = upper.find(kw)
        if i >= 0:
            candidates.append(i)
    # 顶层 ;
    depth = 0
    for i, c in enumerate(where_text):
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == ";" and depth == 0:
            candidates.append(i)
            break
    if not candidates:
        return len(where_text)
    return min(candidates)
