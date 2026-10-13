"""P4-4c 阶段 3: PIPE ROW 改写

Oracle PIPELINED 表函数（RETURNS nested_table_type + PIPE ROW(TYPE(...))）
转为 PG：RETURNS SETOF element_type + RETURN NEXT record。

策略：
- RETURNS STUDENT_LIST → RETURNS SETOF STUDENT_RECORD
  （STUDENT_LIST 来自 CREATE TYPE STUDENT_LIST AS (items STUDENT_RECORD[])）
- PIPE ROW(STUDENT_RECORD(args)) → RETURN NEXT (ROW(args))::STUDENT_RECORD
- RETURN; 在 set-returning 函数中删除

支持嵌套表名映射（_NESTED_TYPE_MAP）。
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


# 嵌套表名 → 元素类型（按 oracle_poc 实际 TYPE 文件配置）
# STUDENT_LIST 是 TABLE OF STUDENT_RECORD → element = STUDENT_RECORD
_NESTED_TYPE_MAP = {
    "STUDENT_LIST": "STUDENT_RECORD",
    "SCORE_LIST": "SCORE_RECORD",
    "CONFIG_LIST": "CONFIG_RECORD",
}

# RETURNS <nested>（不在 set-returning 转换时）→ 改 RETURNS SETOF <element>
_RETURNS_NESTED_RE = re.compile(
    r"RETURNS\s+(" + "|".join(_NESTED_TYPE_MAP.keys()) + r")\b",
    re.IGNORECASE,
)

# PIPE ROW(<TYPE>(...)) → RETURN NEXT (ROW(...))::<TYPE>
# 跨多行：\([^)]+ 用更宽容的匹配
_PIPE_ROW_RE = re.compile(
    r"PIPE\s+ROW\s*\(\s*(\w+)\s*\(((?:[^()]|\([^()]*\))*)\)\s*\)",
    re.IGNORECASE | re.DOTALL,
)


def rewrite_pipe_row(sql: str) -> str:
    """RETURNS <nested> → RETURNS SETOF <element> + PIPE ROW → RETURN NEXT + 删除 set-returning 中 RETURN;。"""
    stripped, restore = _strip_strings_and_comments(sql)

    # 1. RETURNS <NESTED> → RETURNS SETOF <ELEMENT>
    def _ret_replace(m: re.Match) -> str:
        nested = m.group(1).upper()
        element = _NESTED_TYPE_MAP.get(nested, nested)
        return f"RETURNS SETOF {element}"
    out = _RETURNS_NESTED_RE.sub(_ret_replace, stripped)

    # 2. PIPE ROW(TYPE(args)) → RETURN NEXT (ROW(args))::TYPE
    def _pipe_replace(m: re.Match) -> str:
        type_name = m.group(1)
        args = m.group(2).strip()
        return f"RETURN NEXT (ROW({args}))::{type_name}"
    out = _PIPE_ROW_RE.sub(_pipe_replace, out)

    # 3. 查找 RETURNS SETOF 的函数，删除函数末尾的 RETURN; 独立行
    # 逐函数扫描：找 CREATE FUNCTION...RETURNS SETOF...，到对应 END;，
    # 如果中间有独立 'RETURN;' 行（不带值），删掉
    out = _strip_return_in_setof(out)

    return _restore_placeholders(out, restore)


def _strip_return_in_setof(sql: str) -> str:
    """在 RETURNS SETOF 函数中删除独立 'RETURN;' 行。"""
    lines = sql.split("\n")
    n = len(lines)
    new_lines: list = []
    i = 0
    while i < n:
        line = lines[i]
        # 找 CREATE OR REPLACE FUNCTION 头
        m = re.match(
            r"^\s*CREATE\s+OR\s+REPLACE\s+FUNCTION\s+\S+\s*\([^)]*\)\s*$",
            line, re.IGNORECASE
        )
        if m:
            # 找 RETURNS SETOF
            j = i + 1
            is_setof = False
            while j < n and j < i + 10:
                if re.search(r"RETURNS\s+SETOF\b", lines[j], re.IGNORECASE):
                    is_setof = True
                    break
                j += 1
            new_lines.append(line)
            i += 1
            continue
        # 在 RETURNS SETOF 函数体内，删除 'RETURN;' 独立行
        # 检测：上一段到函数体内，是否 setof
        if re.match(r"^\s*RETURN\s*;\s*$", line, re.IGNORECASE):
            # 找上一个 CREATE OR REPLACE FUNCTION 头
            back_idx = len(new_lines) - 1
            is_setof = False
            while back_idx >= 0:
                if re.match(
                        r"^\s*CREATE\s+OR\s+REPLACE\s+FUNCTION\s+\S+\s*\(",
                        new_lines[back_idx], re.IGNORECASE):
                    # 找该函数是否 RETURNS SETOF
                    for k in range(back_idx + 1, min(back_idx + 10, len(new_lines))):
                        if re.search(r"RETURNS\s+SETOF\b", new_lines[k], re.IGNORECASE):
                            is_setof = True
                            break
                    break
                back_idx -= 1
            if is_setof:
                # 跳过这一行
                i += 1
                continue
        new_lines.append(line)
        i += 1
    return "\n".join(new_lines)


def apply_p44c_pipe(sql: str) -> str:
    return rewrite_pipe_row(sql)
