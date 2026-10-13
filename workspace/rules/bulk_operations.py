"""P4-3 批次 2：BULK COLLECT / FORALL 改写

策略：
- FORALL i IN 1..arr.COUNT <stmt> → FOR i IN 1..array_length(arr,1) LOOP <stmt>; END LOOP;
- BULK COLLECT INTO v1, v2, ...（多变量）→ RAISE EXCEPTION 桩（PG 不支持）
  单变量 BULK COLLECT 暂留（本批次不处理——需要数组类型上下文，留 P4-4）
- PIPE ROW 留给 P4-4（依赖 TYPE 重设计）

字符串/注释豁免复用 exception_map 的剥离工具。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# 复用 exception_map 的剥离工具
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rules.exception_map import (  # noqa: E402
    _strip_strings_and_comments,
    _restore_placeholders,
)


# ===========================================================================
# FORALL 改写
# ===========================================================================

# 匹配 `FORALL i IN 1..expr.COUNT` 或 `FORALL i IN 1..expr`（多行 DML）
# 用贪婪让 expr 尽可能长；用 DOTALL 跨行
_FORALL_RE = re.compile(
    r"\bFORALL\s+(\w+)\s+IN\s+(.+?\.COUNT|\w+)\s*\n",  # FORALL i IN arr.COUNT\n
    re.IGNORECASE | re.DOTALL,
)


def rewrite_forall(sql: str) -> str:
    """FORALL i IN 1..arr.COUNT <stmt> → FOR i IN 1..array_length(arr,1) LOOP <stmt>; END LOOP;

    范围表达式：start..end_expr，end_expr 中的 arr.COUNT 改为 array_length(arr, 1)。
    """
    stripped, restore = _strip_strings_and_comments(sql)
    lines = stripped.split("\n")
    new_lines: list = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"\s*FORALL\s+(\w+)\s+IN\s+(.+?)\s*$", line, re.IGNORECASE)
        if m:
            idx_var = m.group(1)
            range_expr = m.group(2)  # 例如 "1..v_list.COUNT"
            # 拆分为 start..end，只对 end 部分替换 .COUNT
            if ".." in range_expr:
                start_part, end_part = range_expr.split("..", 1)
                end_part = re.sub(
                    r"(\S+)\.COUNT\s*$", r"array_length(\1, 1)", end_part.strip()
                )
                range_expr = f"{start_part}..{end_part}"
            else:
                # 没有 .. → 直接处理（罕见）
                range_expr = re.sub(
                    r"(\S+)\.COUNT\s*$", r"array_length(\1, 1)", range_expr
                )
            indent_m = re.match(r"^(\s*)", line)
            indent = indent_m.group(1) if indent_m else ""
            new_lines.append(f"{indent}FOR {idx_var} IN {range_expr} LOOP")
            i += 1
            while i < len(lines):
                inner = lines[i]
                if re.match(r"^\s*END\s*;", inner, re.IGNORECASE):
                    new_lines.append(f"{indent}END LOOP;")
                    new_lines.append(inner)
                    i += 1
                    break
                new_lines.append(inner)
                i += 1
            continue
        new_lines.append(line)
        i += 1
    return _restore_placeholders("\n".join(new_lines), restore)


# ===========================================================================
# BULK COLLECT 多变量 → RAISE EXCEPTION 桩
# ===========================================================================

# 匹配 BULK COLLECT INTO v1, v2, v3（多个变量，至少 2 个）
# 跨行也支持
_BULK_MULTI_RE = re.compile(
    r"\bBULK\s+COLLECT\s+INTO\s+(\s*\w+\s*(?:,\s*\w+\s*){1,})\b",
    re.IGNORECASE | re.DOTALL,
)


def rewrite_bulk_collect_multivar(sql: str) -> str:
    """多变量 BULK COLLECT → RAISE EXCEPTION 桩（PG 不支持）。

    单变量 BULK COLLECT 暂留（需要数组类型上下文，留 P4-4）。
    """
    stripped, restore = _strip_strings_and_comments(sql)
    lines = stripped.split("\n")
    new_lines: list = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.search(r"\bBULK\s+COLLECT\s+INTO\s+", line, re.IGNORECASE)
        if m:
            # 取这一行（BULK COLLECT 之后）到本语句结束（; 或换行后非延续）
            head = line[:m.end()]
            tail = line[m.end():]
            # 解析变量列表（跨多行）
            var_text = tail
            while i + 1 < len(lines) and "," in var_text and ";" not in var_text:
                i += 1
                var_text += " " + lines[i]
            # 统计变量数
            var_count = len([v for v in var_text.split(";")[0].split(",") if v.strip()])
            if var_count >= 2:
                # 多变量：替换为 RAISE EXCEPTION 桩
                indent_m = re.match(r"^(\s*)", line)
                indent = indent_m.group(1) if indent_m else ""
                stub = (
                    f"{indent}-- BULK COLLECT into {var_count} variables: PG 不支持，"
                    f"请改用复合类型数组或拆为多条单标量 SELECT\n"
                    f"{indent}RAISE EXCEPTION 'not implemented: BULK COLLECT into "
                    f"{var_count} variables, please use composite type array';\n"
                )
                new_lines.append(stub)
                i += 1
                continue
            # 单变量：原样保留
            new_lines.append(line)
            i += 1
            continue
        new_lines.append(line)
        i += 1
    return _restore_placeholders("\n".join(new_lines), restore)


# ===========================================================================
# 组合
# ===========================================================================
def apply_bulk_operations(sql: str) -> str:
    """P4-3 批次 2：FORALL 改写 + BULK COLLECT 多变量桩。"""
    sql = rewrite_forall(sql)
    sql = rewrite_bulk_collect_multivar(sql)
    return sql
