"""
验证框架数据模型

所有 Level 的结果统一收敛到这里的 dataclass，便于序列化、聚合和报告渲染。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

# 严重级别（从高到低）
SEVERITY_ORDER = ["critical", "major", "minor", "info"]


@dataclass
class Issue:
    """单条静态问题"""
    code: str                     # 规则组: oracle_residual / logic_error / placeholder / invalid_syntax
    name: str                     # 规则名
    severity: str                 # critical / major / minor / info
    count: int = 0
    samples: List[str] = field(default_factory=list)   # 命中的原始片段（最多 3 条）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ObjectInstall:
    """单个数据库对象的安装结果（Level 3）"""
    object_name: str
    kind: str                     # TABLE / FUNCTION / VIEW / TRIGGER / ...
    installed: bool
    error: str = ""
    sql_state: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CaseResult:
    """单条行为用例结果（Level 4）"""
    case_id: str
    description: str
    status: str                   # pass / fail / error / skipped
    expected: Any = None
    actual: Any = None
    detail: str = ""
    duration_ms: float = 0.0
    object_name: str = ""
    tags: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status == "pass"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["passed"] = self.passed
        return d


@dataclass
class LevelResult:
    """单个 Level 的评分结果"""
    level: str                     # L1 / L2 / L3 / L4
    name: str
    score: float                   # 0-100
    weight: float                  # 0-1
    passed: bool
    skipped: bool = False
    skip_reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def weighted(self) -> float:
        """对总分的贡献"""
        if self.skipped:
            return 0.0
        return self.score * self.weight

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["weighted"] = round(self.weighted, 2)
        return d


@dataclass
class VerificationReport:
    """单个被验证对象（文件/制品）的完整报告"""
    target: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    levels: List[LevelResult] = field(default_factory=list)
    cases: List[CaseResult] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    # 汇总字段（由 finalize 填充）
    overall_score: float = 0.0
    grade: str = "F 不合格"
    gate_passed: bool = False
    gate_failures: List[str] = field(default_factory=list)

    def level(self, name: str) -> Optional[LevelResult]:
        for lv in self.levels:
            if lv.level == name:
                return lv
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "timestamp": self.timestamp,
            "overall_score": self.overall_score,
            "grade": self.grade,
            "gate_passed": self.gate_passed,
            "gate_failures": self.gate_failures,
            "levels": [lv.to_dict() for lv in self.levels],
            "cases": [c.to_dict() for c in self.cases],
            "meta": self.meta,
        }


@dataclass
class SuiteReport:
    """一次项目级验证的汇总报告"""
    name: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    reports: List[VerificationReport] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "timestamp": self.timestamp,
            "summary": self.summary,
            "reports": [r.to_dict() for r in self.reports],
        }
