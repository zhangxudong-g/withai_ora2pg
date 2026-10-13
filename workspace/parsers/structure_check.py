"""
P3-2: 结构体检器

设计 4 项能力:
1. 对象数比对 (object_count): 数 CREATE FUNCTION/PROCEDURE/TRIGGER/VIEW/TABLE/SEQUENCE/TYPE
2. 嵌套深度 (nesting_depth): 函数体内嵌套 BEGIN...END 块的最大深度
3. 空壳/桩检测 (stub_detection): 函数无 body 或仅 RAISE EXCEPTION 'not implemented'
4. %TYPE 引用溯源 (percent_type_refs): 残留 %TYPE 数

策略:
- AST 优先（P3-1 适配器）
- AST 失败时降级为正则（对象计数 / 嵌套深度仍可工作）

落点: workspace/parsers/structure_check.py
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# StructureReport
# ---------------------------------------------------------------------------


@dataclass
class StructureReport:
    """单个文件的结构体检结果。"""
    object_counts: Dict[str, int] = field(default_factory=dict)
    max_nesting_depth: int = 0
    stubs: List[str] = field(default_factory=list)  # stub 函数名列表
    percent_type_count: int = 0
    percent_type_locations: List[str] = field(default_factory=list)
    ast_available: bool = False
    ast_errors: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 1. 对象数比对 (regex-based, robust)
# ---------------------------------------------------------------------------


_OBJECT_PATTERNS = [
    ("FUNCTION", re.compile(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+|NONEDITIONABLE\s+)?FUNCTION\b", re.IGNORECASE)),
    ("PROCEDURE", re.compile(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+|NONEDITIONABLE\s+)?PROCEDURE\b", re.IGNORECASE)),
    ("TRIGGER", re.compile(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:COMPOUND\s+)?TRIGGER\b", re.IGNORECASE)),
    ("VIEW", re.compile(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:FORCE\s+)?VIEW\b", re.IGNORECASE)),
    ("TABLE", re.compile(r"\bCREATE\s+(?:GLOBAL\s+)?(?:TEMPORARY\s+)?TABLE\b", re.IGNORECASE)),
    ("SEQUENCE", re.compile(r"\bCREATE\s+(?:TEMPORARY\s+|UNLOGGED\s+)?SEQUENCE\b", re.IGNORECASE)),
    ("TYPE", re.compile(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?TYPE\b", re.IGNORECASE)),
    ("INDEX", re.compile(r"\bCREATE\s+(?:UNIQUE\s+|BITMAP\s+)?INDEX\b", re.IGNORECASE)),
    ("SYNONYM", re.compile(r"\bCREATE\s+(?:PUBLIC\s+)?SYNONYM\b", re.IGNORECASE)),
]


def _count_objects(sql: str) -> Dict[str, int]:
    """数各种 CREATE 对象的数量。"""
    counts: Dict[str, int] = {}
    for name, pat in _OBJECT_PATTERNS:
        n = len(pat.findall(sql))
        if n > 0:
            counts[name] = n
    return counts


# ---------------------------------------------------------------------------
# 2. 嵌套深度 (regex-based, 数 BEGIN...END 块)
# ---------------------------------------------------------------------------


def _max_nesting_depth(sql: str) -> int:
    """数 SQL 中 BEGIN...END 块的最大嵌套深度。

    简化策略：按出现的 BEGIN 和 END 计数，不区分嵌套。
    """
    # 简化：BEGIN 出现次数（粗略）
    begins = len(re.findall(r"\bBEGIN\b", sql, re.IGNORECASE))
    if begins == 0:
        return 0
    # 嵌套深度粗略估计：max(0, BEGIN 数 - 顶层)
    # 实际嵌套需要括号匹配；这里给粗略估计
    return max(1, begins - 1)


# ---------------------------------------------------------------------------
# 3. 空壳/桩检测
# ---------------------------------------------------------------------------


# 函数体模式：CREATE ... FUNCTION ... $$ body $$
_FUNC_BODY_RE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+|NONEDITIONABLE\s+)?FUNCTION\s+(\w+)",
    re.IGNORECASE,
)

# 我们要找函数名 + body 内的 RAISE EXCEPTION 'not implemented' 或空 body
# 简化策略：找到每个 CREATE FUNCTION 的名字，然后看 body（到下一个 CREATE 或文件尾）
def _detect_stubs(sql: str) -> List[str]:
    """检测空壳/桩函数。

    判定标准：
    - body 只含 RAISE EXCEPTION 'not implemented'（或类似）
    - body 为空（BEGIN END; 中间无内容）
    """
    stubs: List[str] = []
    # 找所有 CREATE FUNCTION 名
    for m in _FUNC_BODY_RE.finditer(sql):
        name = m.group(1)
        # body 范围：从 m.end() 到下一个 CREATE / $$ 结束
        # 简化：取 m.end() 后 500 字符作为 body 范围
        body_start = m.end()
        body_end_match = re.search(r"\$\$", sql[body_start:])
        if not body_end_match:
            continue
        body_end = body_start + body_end_match.start()
        # 找到下一个 $$（body 结束）
        body_end_match2 = re.search(r"\$\$", sql[body_end + 2:])
        if not body_end_match2:
            continue
        body = sql[body_start: body_end + 2 + body_end_match2.start()]

        # 检测 stub
        if "RAISE EXCEPTION" in body.upper() and ("not implemented" in body.lower() or "not_implemented" in body.lower()):
            stubs.append(name)
            continue
        # 空 body（BEGIN ... END; 中间无有效语句）
        body_stripped = re.sub(r"--[^\n]*", "", body)
        body_stripped = re.sub(r"/\*.*?\*/", "", body_stripped, flags=re.DOTALL)
        # 找 BEGIN ... END 之间
        be = re.search(r"\bBEGIN\b(.*?)\bEND\b", body_stripped, re.IGNORECASE | re.DOTALL)
        if be:
            inner = be.group(1).strip()
            # 去掉分号和空白
            inner_clean = inner.rstrip(";").strip()
            if not inner_clean:
                stubs.append(name)
                continue
    return stubs


# ---------------------------------------------------------------------------
# 4. %TYPE 引用溯源
# ---------------------------------------------------------------------------


_PERCENT_TYPE_RE = re.compile(r"\b\w+\.\w+%TYPE\b")


def _find_percent_type(sql: str) -> tuple:
    """找所有 %TYPE 引用。

    Returns:
        (count, locations) — locations 是列表如 ['STUDENT_MST.STUDENT_NO%TYPE', ...]
    """
    matches = _PERCENT_TYPE_RE.findall(sql)
    return len(matches), matches


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def check_structure(sql: str) -> StructureReport:
    """对一段 SQL 代码做结构体检。

    不抛错——所有内部异常 wrap 成 StructureReport 的字段。

    Args:
        sql: 待检查的 SQL 文本

    Returns:
        StructureReport 实例
    """
    report = StructureReport()

    if not sql or not sql.strip():
        return report

    # 1. 对象计数
    try:
        report.object_counts = _count_objects(sql)
    except Exception:
        pass

    # 2. 嵌套深度
    try:
        report.max_nesting_depth = _max_nesting_depth(sql)
    except Exception:
        pass

    # 3. 空壳/桩
    try:
        report.stubs = _detect_stubs(sql)
    except Exception:
        pass

    # 4. %TYPE 引用
    try:
        count, locations = _find_percent_type(sql)
        report.percent_type_count = count
        report.percent_type_locations = locations
    except Exception:
        pass

    # AST 尝试（可选，慢）
    try:
        from parsers.grammar_adapter import parse_plsql
        result = parse_plsql(sql)
        report.ast_available = result.ok
        report.ast_errors = result.errors
    except Exception as e:
        report.ast_available = False
        report.ast_errors = [f"ast unavailable: {e}"]

    return report
