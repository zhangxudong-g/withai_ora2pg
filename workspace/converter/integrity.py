"""P1-1 / DoD-7: 产出完整性断言（对象计数比对）。

核心问题（实施路径 §1.3 实测）：
    源 6 个触发器 → 产物 1 个，转换报告却记 `success: true, warnings: []`。
    丢失的对象在产物里根本不存在，四层验证全都发现不了。

本模块提供最小能力：
    1. ``count_objects``     —— 按对象类型从源文本中数出对象名集合
    2. ``diff_objects``      —— 源 vs 产物 对象名集合比对，返回缺失清单
    3. ``IntegrityReport``   —— 结构化结果，供 P1-2 完整性门禁消费

设计原则（实施路径 §三）：
    - 本模块是"体检"，不做任何产物改写；
    - 只做可识别对象的名集合比对，不做语义判断（语义判断是 P3-2 AST 体检器的事）；
    - 不写死任何行号 / 具体对象名——全部按内容模式断言。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# 对象名提取模式（按类型）。与 scripts/convert_oracle_poc.py::detect_type
# 的类型表保持一致；这里只负责"数名字"。
# ---------------------------------------------------------------------------
_TRIGGER_NAME_RE = re.compile(
    r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:COMPOUND\s+)?TRIGGER\s+([A-Za-z0-9_$\-\."]+)',
    re.IGNORECASE,
)
_FUNCTION_NAME_RE = re.compile(
    r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+|NONEDITIONABLE\s+)?'
    r'FUNCTION\s+([A-Za-z0-9_$\-\."]+)',
    re.IGNORECASE,
)
_PROCEDURE_NAME_RE = re.compile(
    r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+|NONEDITIONABLE\s+)?'
    r'PROCEDURE\s+([A-Za-z0-9_$\-\."]+)',
    re.IGNORECASE,
)
_PACKAGE_NAME_RE = re.compile(
    r'CREATE\s+(?:OR\s+REPLACE\s+)?PACKAGE\s+(?!BODY\b)([A-Za-z0-9_$\-\."]+)',
    re.IGNORECASE,
)
_TYPE_NAME_RE = re.compile(
    r'CREATE\s+(?:OR\s+REPLACE\s+)?TYPE\s+(?!BODY\b)([A-Za-z0-9_$\-\."]+)',
    re.IGNORECASE,
)
_VIEW_NAME_RE = re.compile(
    r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:FORCE\s+)?VIEW\s+([A-Za-z0-9_$\-\."]+)',
    re.IGNORECASE,
)
_TABLE_NAME_RE = re.compile(
    r'CREATE\s+(?:GLOBAL\s+)?(?:TEMPORARY\s+)?TABLE\s+([A-Za-z0-9_$\-\."]+)',
    re.IGNORECASE,
)
_SEQUENCE_NAME_RE = re.compile(
    r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+|NONEDITIONABLE\s+)?'
    r'SEQUENCE\s+([A-Za-z0-9_$\-\."]+)',
    re.IGNORECASE,
)
_SYNONYM_NAME_RE = re.compile(
    r'CREATE\s+(?:PUBLIC\s+)?SYNONYM\s+([A-Za-z0-9_$\-\."]+)',
    re.IGNORECASE,
)

# 产物侧（PG）的对象名提取模式。
# 不同类型产物形态不同，各自专用：
_PG_TRIGGER_FUNC_RE = re.compile(
    r'CREATE\s+OR\s+REPLACE\s+FUNCTION\s+([A-Za-z0-9_$\-\.]+)\s*\(\s*\)\s*RETURNS\s+TRIGGER',
    re.IGNORECASE,
)
# P3-4：复合触发器被拆成多个 PG 触发器，函数名带时机后缀
# （<原名>_BR / <原名>_AS）。比对时要剥掉后缀还原成原始对象名，
# 否则"已成功拆分"会被误判成"静默丢失"。
_COMPOUND_TRIGGER_SUFFIXES = ("_BR", "_AS", "_BROW", "_AROW", "_BSTAT", "_ASTAT")
# SYNONYM 产物 = CREATE OR REPLACE VIEW sm_xxx AS ...
_PG_SYNONYM_VIEW_RE = re.compile(
    r'CREATE\s+OR\s+REPLACE\s+VIEW\s+(sm_[A-Za-z0-9_$\-\.]+)\b',
    re.IGNORECASE,
)
# PACKAGE 产物 = CREATE SCHEMA IF NOT EXISTS pkg_name;
_PG_PACKAGE_SCHEMA_RE = re.compile(
    r'CREATE\s+SCHEMA\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z0-9_$\-\.]+)\b',
    re.IGNORECASE,
)
# TYPE 产物 = CREATE TYPE xxx AS (...)
_PG_TYPE_RE = re.compile(
    r'CREATE\s+TYPE\s+([A-Za-z0-9_$\-\.]+)\b',
    re.IGNORECASE,
)

_NAME_PATTERNS: Dict[str, re.Pattern] = {
    "TRIGGER": _TRIGGER_NAME_RE,
    "FUNCTION": _FUNCTION_NAME_RE,
    "PROCEDURE": _PROCEDURE_NAME_RE,
    "PACKAGE": _PACKAGE_NAME_RE,
    "TYPE": _TYPE_NAME_RE,
    "VIEW": _VIEW_NAME_RE,
    "TABLE": _TABLE_NAME_RE,
    "SEQUENCE": _SEQUENCE_NAME_RE,
    "SYNONYM": _SYNONYM_NAME_RE,
}


def _norm(name: str) -> str:
    """归一化对象名：去引号、去 schema 前缀、大写。"""
    n = name.strip().strip('"')
    n = n.split('.')[-1]
    return n.upper()


def count_objects(sql: str, object_type: str) -> int:
    """数出源 SQL 中可识别的某类对象个数。"""
    pat = _NAME_PATTERNS.get(object_type.upper())
    if pat is None:
        raise ValueError(f"不支持的对象类型: {object_type}")
    return len(pat.findall(sql))


def list_object_names(sql: str, object_type: str) -> List[str]:
    """列出源 SQL 中某类对象的名字（归一化后去重保序）。"""
    pat = _NAME_PATTERNS.get(object_type.upper())
    if pat is None:
        raise ValueError(f"不支持的对象类型: {object_type}")
    seen = []
    for m in pat.findall(sql):
        n = _norm(m)
        if n not in seen:
            seen.append(n)
    return seen


_PG_PRODUCT_PATTERNS = {
    "TRIGGER": _PG_TRIGGER_FUNC_RE,
    "SYNONYM": _PG_SYNONYM_VIEW_RE,
    "PACKAGE": _PG_PACKAGE_SCHEMA_RE,
    "TYPE": _PG_TYPE_RE,
}


def _strip_split_suffix(name: str) -> str:
    """剥掉 P3-4 复合触发器拆分产生��时机后缀，还原原始对象名。"""
    up = name.upper()
    for suf in _COMPOUND_TRIGGER_SUFFIXES:
        if up.endswith(suf) and len(up) > len(suf):
            return up[: -len(suf)]
    return up


def _list_product_names(product_sql: str, object_type: str) -> List[str]:
    """从 PG 产物中提取对象名。

    不同对象类型的 PG 产物形态差异很大（SYNONYM→VIEW、PACKAGE→SCHEMA、
    COMPOUND TRIGGER→多触发器），必须用类型专用模式，否则会把
    "已转换"误判为"丢失"。
    """
    otype = object_type.upper()
    pat = _PG_PRODUCT_PATTERNS.get(otype)
    if pat is None:
        # 无专用模式的类型，回退到源通用模式（产物保留原名）
        return list_object_names(product_sql, object_type)
    seen = []
    for m in pat.findall(product_sql):
        n = _norm(m)
        if otype == "TRIGGER":
            n = _strip_split_suffix(n)
        if n not in seen:
            seen.append(n)
    return seen


@dataclass
class IntegrityReport:
    """源 vs 产物 对象名集合比对结果（供 P1-2 门禁消费）。"""
    object_type: str
    source_names: List[str] = field(default_factory=list)
    product_names: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)   # 源有、产物没有 = 静默丢失
    extra: List[str] = field(default_factory=list)    # 产物有、源没有（一般不该出现）

    @property
    def ok(self) -> bool:
        return not self.missing

    def __bool__(self) -> bool:  # pragma: no cover - 语法糖
        return self.ok


def diff_objects(source_sql: str, product_sql: str, object_type: str) -> IntegrityReport:
    """比对源与产物的对象名集合，返回完整性报告。"""
    src = list_object_names(source_sql, object_type)
    prod = _list_product_names(product_sql, object_type)
    src_set, prod_set = set(src), set(prod)
    return IntegrityReport(
        object_type=object_type.upper(),
        source_names=src,
        product_names=prod,
        missing=sorted(src_set - prod_set),
        extra=sorted(prod_set - src_set),
    )
