"""
Level 1: 静态分析

在不做任何数据库操作的前提下，从转换后的代码里找出：
- 逻辑错误（内联失败残骸、游标属性漏转）
- 占位符（TODO / NEEDS_MANUAL）
- PG 不接受的语法
- Oracle 残留（NVL/SYSDATE/ROWNUM ...）

关键改进：先剥离注释再匹配。转换器会保留 Oracle 原始注释，
若不剥离，`-- 原始: NVL(a, b)` 这类注释会造成大量误报。
"""
from __future__ import annotations

from typing import Any, Dict, List

from .models import Issue
from .patterns import (
    RULE_GROUPS,
    SEVERITY_PENALTY,
    strip_comments,
)

MAX_SAMPLES = 3


def _collect(rules, code: str) -> List[Issue]:
    issues: List[Issue] = []
    for pattern, name, severity in rules:
        count = 0
        samples: List[str] = []
        for m in pattern.finditer(code):
            count += 1
            if len(samples) < MAX_SAMPLES:
                frag = m.group(0).strip()
                samples.append(frag[:120])
        if count:
            issues.append(Issue(
                code="", name=name, severity=severity,
                count=count, samples=samples,
            ))
    return issues


def static_analysis(code: str, groups: Dict[str, List] = None) -> List[Issue]:
    """
    对代码执行静态检测，返回问题列表（已按类别标注 code）

    Args:
        code: 转换后的 PG 代码
        groups: 可选的规则覆盖，key 为类别名

    注意：占位符（TODO / NEEDS_MANUAL）在转换产物里本身就是注释标记
    `/* TODO: ... */`，如果先剥离注释再匹配就永远检不出来。
    因此占位符在原始代码上匹配，其余规则在剥离注释后的代码上匹配。
    """
    groups = groups or RULE_GROUPS
    clean = strip_comments(code)

    issues: List[Issue] = []

    # 占位符：在原始代码上匹配（它们就是注释）
    if groups.get("placeholder"):
        for iss in _collect(groups["placeholder"], code):
            iss.code = "placeholder"
            issues.append(iss)

    for group_name in ("logic_error", "invalid_syntax", "oracle_residual"):
        rules = groups.get(group_name)
        if not rules:
            continue
        for iss in _collect(rules, clean):
            iss.code = group_name
            issues.append(iss)

    order = {"critical": 0, "major": 1, "minor": 2, "info": 3}
    issues.sort(key=lambda i: (order.get(i.severity, 9), i.code, -i.count))
    return issues


def score_static(issues: List[Issue], line_count: int) -> float:
    """
    评分（0-100）

    penalty = Σ(命中数 × 严重级别权重)
    归一化分母 = max(1, 行数/100)，即"每百行的扣分单位"。
    这样小文件不会因为一处残留就被过度惩罚，大文件的残留密度才真正拉低分数。

    另外：只要存在 critical 问题，分数封顶 40。
    理由是 critical（IF NULL 残骸、PACKAGE BODY 残留等）意味着转换已经破坏了语义，
    这种情况下"扣几分"没有意义——它不是扣分项，是阻断项。
    真正的阻断判定由 gate 负责，这里只是不让 L1 分数虚高。
    """
    penalty = sum(i.count * SEVERITY_PENALTY.get(i.severity, 1.0) for i in issues)
    denominator = max(1.0, line_count / 100.0)
    score = max(0.0, 100.0 - penalty / denominator)
    if any(i.severity == "critical" for i in issues):
        score = min(score, 40.0)
    return round(score, 1)


def summarize(issues: List[Issue]) -> Dict[str, Any]:
    """按类别/严重级别汇总，供报告使用"""
    by_category: Dict[str, int] = {}
    by_severity: Dict[str, int] = {}
    for i in issues:
        by_category[i.code] = by_category.get(i.code, 0) + i.count
        by_severity[i.severity] = by_severity.get(i.severity, 0) + i.count
    return {
        "by_category": by_category,
        "by_severity": by_severity,
        "critical_count": by_severity.get("critical", 0),
    }
