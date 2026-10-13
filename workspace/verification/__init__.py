"""
验证框架 - Oracle → PostgreSQL 转换质量验证

本模块是完全独立的 4 层验证框架，不依赖 pipeline/verification/。

层级设计：
    L1 静态分析   Oracle 残留 / 逻辑错误 / 占位符（纯本地，最快）
    L2 语法验证   pglast 逐语句解析 + 块结构配对（纯本地）
    L3 安装验证   在隔离 schema 里真正建对象（最权威）
    L4 行为等价   金标准用例断言输出

权重: L1=15%, L2=20%, L3=25%, L4=40%（跳过层按比例归一化）

典型用法:

    # 单制品
    from verification import Verifier, VerificationConfig
    v = Verifier(VerificationConfig(use_pg=False))
    report = v.verify_code("PKG_SCORE_MANAGE", pg_code)
    print(report.overall_score, report.grade)

    # 项目级（保留跨文件依赖）
    from verification import Artifact
    suite = v.verify_suite("oracle_poc", artifacts, cases=load_cases("golden.json"))

    # 旧 API 兼容
    from verification import full_verification
    result = full_verification('', pg_code, use_pg=True)
"""
from .behavior_check import (
    load_cases,
    run_case,
    run_cases,
    score_cases,
    values_equal,
)
from .framework import (
    DEFAULT_WEIGHTS,
    Artifact,
    VerificationConfig,
    Verifier,
    grade,
)
from .models import (
    CaseResult,
    LevelResult,
    ObjectInstall,
    SuiteReport,
    VerificationReport,
)
from .patterns import RULE_GROUPS, SEVERITY_PENALTY, strip_comments
from .pg_sandbox import DEFAULT_PG, PGSandbox
from .report import render_report, render_suite, write_json, write_text
from .static_check import score_static, static_analysis, summarize
from .syntax_check import check_pglast, check_structure, score_syntax
from .sqlsplit import classify, extract_object_name, is_driver_script, split_statements

__all__ = [
    # 编排
    "Verifier",
    "VerificationConfig",
    "Artifact",
    "DEFAULT_WEIGHTS",
    "grade",
    # 模型
    "VerificationReport",
    "SuiteReport",
    "LevelResult",
    "CaseResult",
    "ObjectInstall",
    # 各 Level
    "static_analysis",
    "score_static",
    "summarize",
    "check_pglast",
    "check_structure",
    "score_syntax",
    "run_cases",
    "run_case",
    "score_cases",
    "values_equal",
    "load_cases",
    # SQL 工具
    "split_statements",
    "classify",
    "extract_object_name",
    "is_driver_script",
    "PGSandbox",
    "DEFAULT_PG",
    # 规则
    "RULE_GROUPS",
    "SEVERITY_PENALTY",
    "strip_comments",
    # 报告
    "render_report",
    "render_suite",
    "write_json",
    "write_text",
]


# ---------------------------------------------------------------------------
# 兼容旧名称（支持旧代码迁移）
# ---------------------------------------------------------------------------

def render_report_compat(report) -> str:
    """
    渲染单制品报告。

    接受 VerificationReport 或其 to_dict() 结果。
    """
    from .report import render_report as _render_report
    if isinstance(report, VerificationReport):
        return _render_report(report)
    if isinstance(report, dict):
        return _render_report(VerificationReport(
            target=report.get("target", "?"),
            timestamp=report.get("timestamp", ""),
            levels=[LevelResult(**lv) for lv in report.get("levels", [])
                    if isinstance(lv, dict)],
            cases=[CaseResult(**c) for c in report.get("cases", [])
                   if isinstance(c, dict)],
            overall_score=report.get("overall_score", 0.0),
            grade=report.get("grade", ""),
            gate_passed=report.get("gate_passed", False),
            gate_failures=report.get("gate_failures", []),
            meta=report.get("meta", {}),
        ))
    raise TypeError(f"不支持的报告类型: {type(report)}")


def full_verification(input_code: str = "", output_code: str = "",
                      test_cases=None,
                      use_pg: bool = True, pg_executor=None,
                      target: str = "artifact") -> dict:
    """
    完整转换验证（旧入口保留，内部走新框架）。

    Args:
        input_code: 原始 Oracle 代码，仅用于记录长度，新框架不做双向比对
        output_code: 转换后的 PG 代码
        test_cases: 行为用例列表
        use_pg: 是否连 PG 跑 L3/L4
        pg_executor: 兼容参数，忽略（沙箱由 Verifier 自行管理）
    """
    cfg = VerificationConfig(use_pg=use_pg, drop_schema_on_exit=True)
    v = Verifier(cfg)
    try:
        report = v.verify_code(target, output_code, cases=test_cases or [])
    finally:
        v.close()
    d = report.to_dict()
    d["input_length"] = len(input_code or "")
    d["output_length"] = len(output_code or "")
    d["method"] = "PG execution" if use_pg else "static only"
    return d


#: 旧名 -> 新名
LEGACY_ALIASES = {
    "check_pg_syntax_via_pglast": "check_pglast",
    "PGExecutor": "PGSandbox",
    "score_level1": "score_static",
    "score_level2": "score_syntax",
}

# 向后兼容：保留 render_report 作为别名
render_report = render_report_compat
