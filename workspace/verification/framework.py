"""
验证流程编排器

4 个 Level 的执行顺序与权重：

    L1 静态分析   15%   不碰数据库，最快，定位转换器残骸
    L2 语法验证   20%   pglast 逐语句解析 + 块结构配对
    L3 安装验证   25%   在 PG 沙箱里真正建对象（最权威的"能不能跑"）
    L4 行为等价   40%   金标准用例断言输出

与原 4-Level 的差异：
- 原来 L3 权重 50%，但 L3 的测试用例是占位假数据（check_positive/lookup_user），
  跟真实转换产物无关，实际几乎不产生有效信号；
- 原来 level4_combined.py 与 framework.py 各有一套权重（30/30/40 vs 20/30/50），
  同一份代码跑两个入口得到不同分数。现在只有一套。
- 原来没有任何一层验证"PG 能不能装下"，而这恰恰是迁移失败的首要原因。

权重在某个 Level 被跳过时按比例重新归一化，不会因为缺一层就凭空失分。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .behavior_check import load_cases, run_cases, score_cases
from .models import CaseResult, LevelResult, ObjectInstall, SuiteReport, VerificationReport
from .pg_sandbox import DEFAULT_PG, PGSandbox
from .smoke_check import SmokeReport, smoke_all
from .sqlsplit import KIND_RANK, classify, sort_statements, split_statements
from .static_check import score_static, static_analysis, summarize
from .syntax_check import check_pglast, check_structure, score_syntax

DEFAULT_WEIGHTS = {
    "L1": 0.15,
    "L2": 0.20,
    "L3": 0.25,
    "L4": 0.40,
    # P1-4: L3.5 首调冒烟不参与评分（权重 0），只进 Gate ——
    # 避免改变基线分数口径；DoD-3 的验收体现在 gate_passed 与 fail 明细上。
    "L3.5": 0.0,
}

LEVEL_NAMES = {
    "L1": "静态分析",
    "L2": "语法验证",
    "L3": "安装验证",
    "L3.5": "首调冒烟",
    "L4": "行为等价",
}

GRADE_BANDS = [
    (95, "A 优"),
    (85, "B 良"),
    (70, "C 中"),
    (50, "D 差"),
    (0, "F 不合格"),
]


def grade(score: float) -> str:
    for threshold, label in GRADE_BANDS:
        if score >= threshold:
            return label
    return "F 不合格"


@dataclass
class Artifact:
    """待验证的制品：一个待转换/已转换的 SQL 单元"""
    name: str
    code: str


@dataclass
class VerificationConfig:
    weights: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    pg_conn: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_PG))
    schema: str = "ora2pg_verify"
    use_pg: bool = True                 # 是否启用 L3/L4
    cases_file: Optional[str] = None    # 金标准用例库路径
    gate_fail_on_install_error: bool = True
    gate_fail_on_parse_error: bool = True
    gate_fail_on_logic_error: bool = True
    gate_require_cases: bool = True     # 没有任何可执行用例时 gate 失败
    smoke_enabled: bool = True          # P1-4: 是否启用 L3.5 首调冒烟
    gate_fail_on_smoke_error: bool = True   # L3.5 有 fail 时 gate 失败（DoD-3）
    drop_schema_on_exit: bool = True
    install_passes: int = 2             # 安装趟数，用于消化同类型内的跨文件依赖
    min_case_coverage: float = 0.5      # L4 可执行用例占比下限，低于此值不算通过


class Verifier:
    """验证器"""

    def __init__(self, config: Optional[VerificationConfig] = None):
        self.config = config or VerificationConfig()
        self._sandbox: Optional[PGSandbox] = None
        self._pg_error = ""

    # ---------------- PG 可用性 ----------------

    def _ensure_sandbox(self) -> Optional[PGSandbox]:
        if not self.config.use_pg:
            return None
        if self._sandbox is not None:
            return self._sandbox
        sb = PGSandbox(self.config.schema, self.config.pg_conn)
        ok, err = sb.available()
        if not ok:
            self._pg_error = err
            return None
        self._sandbox = sb
        return sb

    def close(self):
        if self._sandbox is not None:
            if self.config.drop_schema_on_exit:
                self._sandbox.drop()
            self._sandbox.close()
            self._sandbox = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # ---------------- L1 / L2 ----------------

    def check_l1_l2(self, code: str) -> List[LevelResult]:
        """静态 + 语法检查（纯本地，无需 DB）"""
        if not code or not code.strip():
            # 转换无产出：这不是"完美无问题"，而是根本没产出。
            # 必须显式判 0，否则转换崩溃的文件会拿到 100 分。
            empty = LevelResult(
                level="L1", name=LEVEL_NAMES["L1"], score=0.0,
                weight=self.config.weights["L1"], passed=False,
                details={"line_count": 0, "issue_count": 0,
                         "empty_output": True,
                         "summary": {"by_category": {}, "by_severity": {},
                                     "critical_count": 0},
                         "issues": []},
            )
            l2 = LevelResult(
                level="L2", name=LEVEL_NAMES["L2"], score=0.0,
                weight=self.config.weights["L2"], passed=False, skipped=True,
                skip_reason="转换无产出，无可解析内容",
                details={"statement_count": 0, "pglast": {"available": True, "total": 0,
                                                          "passed": 0, "failed": 0},
                         "pglast_errors": [], "structure_issues": [],
                         "note": "转换无产出"},
            )
            return [empty, l2]

        # 任务1: SQL*Plus 驱动脚本（@@include / PROMPT 等元命令）不是可执行 SQL
        # 跳过 L2 语法检查 (pglast 会报"@语法错"等误报)。
        # L1 仍跑，因为可能残存 Oracle 语法关键字需要报警。
        from .sqlsplit import is_driver_script
        if is_driver_script(code):
            line_count = code.count("\n") + 1
            issues = static_analysis(code)
            s_summary = summarize(issues)
            l1 = LevelResult(
                level="L1", name=LEVEL_NAMES["L1"],
                score=score_static(issues, line_count),
                weight=self.config.weights["L1"], passed=True,
                details={
                    "line_count": line_count,
                    "issue_count": sum(i.count for i in issues),
                    "summary": s_summary,
                    "issues": [i.to_dict() for i in issues[:40]],
                    "is_driver_script": True,
                },
            )
            l1.passed = s_summary.get("critical_count", 0) == 0
            l2 = LevelResult(
                level="L2", name=LEVEL_NAMES["L2"], score=100.0,
                weight=self.config.weights["L2"], passed=True, skipped=True,
                skip_reason="SQL*Plus 驱动脚本，含 @@include/PROMPT 等元命令，无可解析 SQL",
                details={"statement_count": 0,
                         "pglast": {"available": True, "total": 0, "passed": 0, "failed": 0},
                         "pglast_errors": [], "structure_issues": [],
                         "note": "driver script - 跳过 pglast 解析"},
            )
            return [l1, l2]

        line_count = code.count("\n") + 1

        issues = static_analysis(code)
        s_summary = summarize(issues)
        l1 = LevelResult(
            level="L1", name=LEVEL_NAMES["L1"],
            score=score_static(issues, line_count),
            weight=self.config.weights["L1"], passed=True,
            details={
                "line_count": line_count,
                "issue_count": sum(i.count for i in issues),
                "summary": s_summary,
                "issues": [i.to_dict() for i in issues[:40]],
            },
        )
        l1.passed = s_summary.get("critical_count", 0) == 0

        pglast_result = check_pglast(code)
        structure = check_structure(code)
        l2_score = score_syntax(pglast_result, structure)
        l2 = LevelResult(
            level="L2", name=LEVEL_NAMES["L2"], score=l2_score,
            weight=self.config.weights["L2"],
            passed=(pglast_result.get("failed", 0) == 0 and not structure),
            skipped=not pglast_result.get("available", False),
            skip_reason=pglast_result.get("error", ""),
            details={
                "statement_count": pglast_result.get("total", 0),
                "pglast": {k: v for k, v in pglast_result.items() if k != "errors"},
                "pglast_errors": pglast_result.get("errors", [])[:20],
                "structure_issues": [i.to_dict() for i in structure[:20]],
                "note": "pglast 只解析 SQL 外层语法，plpgsql 函数体内的错误需由 L3 判定",
            },
        )
        return [l1, l2]

    # ---------------- 单制品验证 ----------------

    def verify_code(self, target: str, code: str,
                    cases: Optional[List[Dict[str, Any]]] = None) -> VerificationReport:
        """
        验证单个制品：L1 + L2（若开启 PG 且给了用例，再跑 L3/L4）

        单制品模式假定依赖已存在；项目级验证请用 verify_suite()。
        """
        report = VerificationReport(target=target)
        report.levels = self.check_l1_l2(code)

        if not self.config.use_pg:
            reason = "配置未启用 PG 验证"
        else:
            sb = self._ensure_sandbox()
            if sb is None:
                reason = self._pg_error
            else:
                reason = ""

        if reason:
            # L3/L4 无论是否执行都占位，保证报告结构稳定
            report.levels.append(self._skipped("L3", reason))
            report.levels.append(self._skipped("L4", reason))
        else:
            sb = self._sandbox
            if not self._schema_ready:
                sb.reset()
                self._schema_ready = True
            installs = sb.install_script(code, label=target)
            report.levels.append(self._l3_result(installs))
            report.levels.append(self._l4_result(cases or []))

        self._finalize(report)
        return report

    _schema_ready = False

    # ---------------- 项目级验证 ----------------

    def verify_suite(self, name: str, artifacts: Sequence[Artifact],
                     cases: Optional[List[Dict[str, Any]]] = None) -> SuiteReport:
        """
        项目级验证：所有制品装进同一个沙箱（保留跨文件依赖），再统一跑行为用例。

        安装顺序按对象类型依赖重排（表 -> 视图 -> 函数 -> 触发器），
        因为转换产物单文件内部顺序不保证。
        """
        if cases is None and self.config.cases_file:
            cases = load_cases(self.config.cases_file)
        cases = cases or []

        suite = SuiteReport(name=name)
        all_installs: List[ObjectInstall] = []
        owner: Dict[int, str] = {}       # id(ObjectInstall) -> 制品名

        sb = self._ensure_sandbox()
        pg_ok = sb is not None

        if pg_ok:
            sb.reset()
            self._schema_ready = True
            # 全局按对象类型依赖稳定排序：表先于视图，视图先于函数，函数先于触发器。
            # 单文件内部的顺序不可靠，必须跨文件一起排。
            # 同类型内用原始序号保持相对顺序（稳定排序）。
            triples: List[tuple] = []
            seq = 0
            for art in artifacts:
                # SQL*Plus driver scripts (@@include / PROMPT) are not
                # executable SQL -- L1/L2 skip them, L3 must too.
                from .sqlsplit import is_driver_script
                if is_driver_script(art.code):
                    continue
                for st in split_statements(art.code):
                    triples.append((art.name, st, seq))
                    seq += 1
            triples.sort(key=lambda t: (
                KIND_RANK.get(classify(t[1]), KIND_RANK["OTHER"]), t[2]
            ))
            pending: List[tuple] = [(label, st) for label, st, _ in triples]

            # 多趟安装：消化同类型内的跨文件依赖（如函数 A 调函数 B）。
            # 只重试上一趟仍失败的语句——已经建成功的绝不重试，
            # 因为 CREATE TABLE / CREATE TYPE / CREATE SEQUENCE 没有
            # OR REPLACE 语义，重试会得到 42P07 "已存在" 的假失败。
            passes = max(1, self.config.install_passes)
            for attempt in range(passes):
                last = (attempt == passes - 1)
                still: List[tuple] = []
                for label, st in pending:
                    res = sb.install_script(st)
                    for r in res:
                        owner[id(r)] = label
                    ok = [r for r in res if r.installed]
                    if ok:
                        all_installs.extend(ok)          # 成功即最终成功
                    elif last:
                        all_installs.extend(res)         # 最后一趟仍失败才记为失败
                    else:
                        still.append((label, st))
                pending = still
                if not pending:
                    break
        else:
            self._pg_error = self._pg_error or "PG 不可用"

        # 逐制品的 L1/L2
        for art in artifacts:
            rep = VerificationReport(target=art.name)
            rep.levels = self.check_l1_l2(art.code)
            self._finalize(rep, cases=[])
            suite.reports.append(rep)

        # 套件级 L3
        if pg_ok:
            suite_l3 = self._l3_result(all_installs)
            suite_l3.details["per_artifact"] = _group_installs_by_owner(all_installs, owner)
        else:
            suite_l3 = self._skipped("L3", self._pg_error)

        # 套件级 L3.5 首调冒烟（P1-4 / DoD-3）
        # 范围 = 目标 schema + 本次安装新建的 schema：
        #   避免扫到上次运行遗留的 gkb* 公共表 / 旧 rt2 副本——这样 L3.5 fail 列表
        #   只反映"本次产物能否首调"，可重复、不漂移。
        smoke_schemas: Optional[List[str]] = None
        if pg_ok and self.config.smoke_enabled:
            from .smoke_check import list_target_schemas
            before_schemas = set(list_target_schemas(sb))
            new_schemas = set(list_target_schemas(sb)) - before_schemas
            smoke_schemas = [self.config.schema] + sorted(new_schemas)
            smoke = smoke_all(sb, schemas=smoke_schemas)
            suite_l35 = self._l35_result(smoke)
            suite.summary["smoke"] = smoke.to_dict()
            suite.summary["smoke"]["scanned_schemas"] = smoke_schemas
        else:
            suite_l35 = self._skipped("L3.5",
                                      self._pg_error if not pg_ok else "未启用冒烟层")

        # 套件级 L4
        if pg_ok and cases:
            results = run_cases(cases, sb)
            suite_l4 = self._l4_result(cases, results=results)
            suite.summary["cases"] = [c.to_dict() for c in results]
        else:
            reason = self._pg_error if not pg_ok else "未提供用例库"
            suite_l4 = self._skipped("L4", reason)

        suite.summary["levels"] = [lv.to_dict() for lv in (suite_l3, suite_l35, suite_l4)]
        suite.summary["static_levels"] = _aggregate_static(suite.reports)
        suite.summary["overall_score"], suite.summary["grade"] = _weighted_total(
            [lv for rep in suite.reports for lv in rep.levels]
            + [suite_l3, suite_l35, suite_l4],
            self.config.weights,
        )
        # W0-3: 报告 weight_covered = 实际计入总分 / 总配置权重。
        # 100% 表示所有 Level 都跑了；<100% 说明有 Level 被跳过（如 --no-pg
        # 跳 L3/L3.5/L4）—— 此时总分仅反映被跑层、不能误读为"能安装+可调用+行为等价全过"。
        #
        # 按 5 个固定 Level (L1/L2/L3/L3.5/L4) 判断覆盖。每个 Level 的状态：
        #   L1/L2: 只要有产物就会跑（static 始终 active）
        #   L3: pg_ok 才有结果
        #   L3.5: pg_ok + smoke_enabled
        #   L4: pg_ok + cases 非空
        active_levels: Dict[str, bool] = {
            "L1": bool(suite.reports),       # 至少 1 个产物
            "L2": bool(suite.reports),
            "L3": not suite_l3.skipped,
            "L3.5": not suite_l35.skipped,
            "L4": not suite_l4.skipped,
        }
        total_w = sum(self.config.weights.values())
        active_w = sum(
            self.config.weights.get(name, 0.0)
            for name, active in active_levels.items() if active
        )
        weight_covered = round(active_w / total_w, 3) if total_w else 1.0
        suite.summary["weight_covered"] = weight_covered
        suite.summary["weight_covered_active"] = active_levels
        suite.summary["weight_covered_warning"] = (
            weight_covered < 1.0 and self.config.use_pg
        )
        gate_failures = _suite_gate_failures(suite.reports, suite_l3, suite_l4, self.config)
        if (suite_l35.passed is False and not suite_l35.skipped
                and self.config.gate_fail_on_smoke_error):
            gate_failures.append(
                f"L3.5 首调冒烟 {len(suite_l35.details.get('failures', []))} 个对象失败"
                f"（总计 {suite_l35.details.get('total', 0)}）")
        suite.summary["gate_failures"] = gate_failures
        suite.summary["gate_passed"] = not gate_failures
        return suite

    # ---------------- 内部 ----------------

    def _skipped(self, level: str, reason: str) -> LevelResult:
        return LevelResult(
            level=level, name=LEVEL_NAMES[level], score=0.0,
            weight=self.config.weights[level], passed=False,
            skipped=True, skip_reason=reason or "未启用",
        )

    def _l3_result(self, installs: List[ObjectInstall]) -> LevelResult:
        structural = [i for i in installs if i.kind not in ("OTHER", "COMMENT", "GRANT")]
        total = len(installs)
        ok = sum(1 for i in installs if i.installed)
        s_total = len(structural)
        s_ok = sum(1 for i in structural if i.installed)
        score = round(ok / total * 100, 1) if total else 100.0
        by_kind: Dict[str, Dict[str, int]] = {}
        for i in structural:
            d = by_kind.setdefault(i.kind, {"total": 0, "ok": 0})
            d["total"] += 1
            if i.installed:
                d["ok"] += 1
        return LevelResult(
            level="L3", name=LEVEL_NAMES["L3"], score=score,
            weight=self.config.weights["L3"],
            passed=(total == 0 or ok == total),
            skipped=(total == 0),
            skip_reason="没有可安装的对象" if total == 0 else "",
            details={
                "total": total, "installed": ok, "failed": total - ok,
                "structural_total": s_total, "structural_installed": s_ok,
                "by_kind": by_kind,
                "failures": [i.to_dict() for i in installs if not i.installed][:30],
            },
        )

    def _l35_result(self, smoke: SmokeReport) -> LevelResult:
        """P1-4: L3.5 首调冒烟层结果（权重 0，不进评分，只进 Gate）。"""
        total = smoke.total
        fail_n = len(smoke.failed)
        score = 100.0 if total == 0 else round(100.0 * (total - fail_n) / total, 1)
        d = smoke.to_dict()
        return LevelResult(
            level="L3.5", name=LEVEL_NAMES["L3.5"], score=score,
            weight=self.config.weights.get("L3.5", 0.0),
            passed=(fail_n == 0),
            skipped=(total == 0),
            skip_reason="没有可冒烟的对象" if total == 0 else "",
            details={
                **d,
                "failures": [
                    {"kind": r.kind, "schema": r.schema, "name": r.name,
                     "sql": r.sql, "error": r.error}
                    for r in smoke.failed[:30]
                ],
            },
        )

    def _l4_result(self, cases: List[Dict[str, Any]],
                   results: Optional[List[CaseResult]] = None) -> LevelResult:
        if not cases:
            return self._skipped("L4", "未提供用例库")
        sb = self._sandbox
        if results is None:
            results = run_cases(cases, sb)
        stats = score_cases(results)
        # 只有"跑起来"的用例才证明得了行为等价。
        # 2/65 跑通拿 100 分是假象——执行覆盖率不足时 L4 不算通过。
        ratio = stats.get("execution_ratio", 0.0)
        enough = (ratio >= self.config.min_case_coverage
                  and not stats["no_applicable_cases"])
        return LevelResult(
            level="L4", name=LEVEL_NAMES["L4"], score=stats["score"],
            weight=self.config.weights["L4"],
            passed=(stats["failed"] == 0 and stats["errored"] == 0 and enough),
            details={**stats,
                     "min_case_coverage": self.config.min_case_coverage,
                     "coverage_insufficient": not enough,
                     "failures": [c.to_dict() for c in results
                                  if c.status in ("fail", "error")][:30]},
        )

    def _finalize(self, report: VerificationReport, cases: Optional[List] = None):
        report.overall_score, report.grade = _weighted_total(report.levels, self.config.weights)
        report.gate_failures = _gate_failures(report.levels, self.config)
        report.gate_passed = not report.gate_failures


# ---------------------------------------------------------------------------
# 聚合工具
# ---------------------------------------------------------------------------

def _weighted_total(levels: List[LevelResult], weights: Dict[str, float]):
    """按权重求总分；被跳过的 Level 其权重按比例分摊给其余 Level"""
    active = [lv for lv in levels if not lv.skipped]
    if not active:
        return 0.0, grade(0.0)
    total_w = sum(weights.get(lv.level, 0.0) for lv in active)
    if total_w <= 0:
        return 0.0, grade(0.0)
    score = sum(lv.score * weights.get(lv.level, 0.0) for lv in active) / total_w
    return round(score, 1), grade(score)


def _aggregate_static(reports: List[VerificationReport]) -> Dict[str, Any]:
    """多个制品的 L1/L2 取平均"""
    out: Dict[str, Any] = {}
    for level in ("L1", "L2"):
        scores = [r.level(level).score for r in reports if r.level(level)]
        skipped = [r.level(level) for r in reports if r.level(level) and r.level(level).skipped]
        if scores:
            out[level] = {
                "score": round(sum(scores) / len(scores), 1),
                "count": len(scores),
                "skipped_count": len(skipped),
            }
    return out


def _group_installs_by_owner(installs: List[ObjectInstall], owner: Dict[int, str]) -> Dict[str, Any]:
    groups: Dict[str, Dict[str, Any]] = {}
    for i in installs:
        label = owner.get(id(i), "?")
        g = groups.setdefault(label, {"total": 0, "ok": 0, "failures": []})
        g["total"] += 1
        if i.installed:
            g["ok"] += 1
        else:
            g["failures"].append(f"{i.kind} {i.object_name}: {i.error[:120]}")
    return groups


def _gate_failures(levels: List[LevelResult], config: VerificationConfig) -> List[str]:
    fails: List[str] = []
    for lv in levels:
        if lv.skipped:
            continue
        if lv.level == "L1":
            if lv.details.get("empty_output"):
                fails.append("转换无产出")
            elif config.gate_fail_on_logic_error:
                crit = lv.details.get("summary", {}).get("critical_count", 0)
                if crit:
                    fails.append(f"L1 存在 {crit} 个 critical 逻辑错误")
        elif lv.level == "L2" and config.gate_fail_on_parse_error:
            failed = lv.details.get("pglast", {}).get("failed", 0)
            if failed:
                fails.append(f"L2 有 {failed} 条语句解析失败")
            # W0-2: 检查 plpgsql 块结构问题。pglast 只看 SQL 外层，
            # 块结构问题（BEGIN/END 配对等）是体内语法的唯一早期信号。
            # 只计 critical（major 是 IF/END IF 不配对的"提醒"性信号，避免曝话）。
            structure_issues = lv.details.get("structure_issues", [])
            critical_struct = sum(
                1 for s in structure_issues
                if s.get("severity") == "critical"
            )
            if critical_struct:
                fails.append(
                    f"L2 有 {critical_struct} 个 critical 块结构问题"
                    f"（BEGIN/END 不配对）"
                )
        elif lv.level == "L3" and config.gate_fail_on_install_error:
            if lv.details.get("failed", 0):
                fails.append(f"L3 有 {lv.details['failed']} 个对象安装失败")
        elif lv.level == "L4":
            d = lv.details
            if config.gate_require_cases:
                if d.get("no_applicable_cases"):
                    fails.append("L4 没有任何可执行用例，行为未经验证")
                elif d.get("execution_ratio", 0.0) < config.min_case_coverage:
                    fails.append(
                        f"L4 仅执行 {d.get('applicable', 0)}/{d.get('total', 0)} 条用例"
                        f"（覆盖率 {d.get('execution_ratio', 0):.0%} < "
                        f"{config.min_case_coverage:.0%}），行为验证不充分")
            if d.get("failed") or d.get("errored"):
                fails.append(f"L4 用例未通过 {d.get('failed', 0) + d.get('errored', 0)} 条")
    return fails


def _suite_gate(reports, l3: LevelResult, l4: LevelResult, config) -> bool:
    return not _suite_gate_failures(reports, l3, l4, config)


def _suite_gate_failures(reports, l3: LevelResult, l4: LevelResult, config) -> List[str]:
    fails: List[str] = []
    for rep in reports:
        fails.extend(f"[{rep.target}] {m}" for m in _gate_failures(rep.levels, config))
    fails.extend(f"[suite] {m}" for m in _gate_failures([l3, l4], config))
    return fails
