"""
验证报告渲染

- `render_report(report)`  单制品 Markdown
- `render_suite(suite)`     项目级 Markdown
- `write_json(obj, path)`   JSON 落盘
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .models import SuiteReport, VerificationReport

_STATUS_ICON = {"pass": "PASS", "fail": "FAIL", "error": "ERROR", "skipped": "SKIP"}


def _table(rows: List[List[Any]], header: List[str]) -> List[str]:
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join(["---"] * len(header)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return out


def render_levels(levels: List[Dict[str, Any]]) -> List[str]:
    rows = []
    for lv in levels:
        score = "-" if lv.get("skipped") else f"{lv['score']}"
        status = "跳过" if lv.get("skipped") else ("通过" if lv.get("passed") else "未通过")
        rows.append([lv["level"], lv["name"], f"{lv['weight']:.0%}", score, status])
    return _table(rows, ["Level", "名称", "权重", "得分", "结果"])


def render_report(report: VerificationReport) -> str:
    """单制品 Markdown 报告"""
    d = report.to_dict()
    md: List[str] = []
    md.append(f"# 转换验证报告 - {report.target}")
    md.append("")
    md.append(f"- 生成时间: {report.timestamp}")
    md.append(f"- **总分: {report.overall_score} - {report.grade}**")
    md.append(f"- Gate: {'通过' if report.gate_passed else '未通过'}")
    if report.gate_failures:
        md.append("")
        for f in report.gate_failures:
            md.append(f"  - {f}")
    md.append("")

    md.append("## 各层得分")
    md.append("")
    md.extend(render_levels(d["levels"]))
    md.append("")

    for lv in d["levels"]:
        if lv["skipped"]:
            md.append(f"### {lv['level']} {lv['name']}（跳过）")
            md.append("")
            md.append(f"原因: {lv.get('skip_reason') or '未启用'}")
            md.append("")
            continue

        md.append(f"### {lv['level']} {lv['name']} — {lv['score']}")
        md.append("")

        if lv["level"] == "L1":
            det = lv["details"]
            md.append(f"代码行数: {det.get('line_count')}，问题实例: {det.get('issue_count')}")
            md.append("")
            issues = det.get("issues", [])
            if issues:
                md.extend(_table(
                    [[i["severity"], i["code"], i["name"], i["count"],
                      " / ".join(s.replace("|", "\\|") for s in i["samples"][:2])]
                     for i in issues],
                    ["级别", "类别", "规则", "命中", "样例"],
                ))
            else:
                md.append("未发现问题")
            md.append("")

        elif lv["level"] == "L2":
            det = lv["details"]
            pg = det.get("pglast", {})
            md.append(f"语句数: {det.get('statement_count')}，"
                      f"pglast 解析成功: {pg.get('passed', 0)}，失败: {pg.get('failed', 0)}")
            md.append("")
            if det.get("pglast_errors"):
                md.extend(_table(
                    [[e["index"], e["head"].replace("|", "\\|")[:70], e["error"][:80]]
                     for e in det["pglast_errors"]],
                    ["#", "语句", "错误"],
                ))
                md.append("")
            if det.get("structure_issues"):
                md.extend(_table(
                    [[i["severity"], i["name"], " / ".join(i["samples"])[:60].replace("|", "\\|")]
                     for i in det["structure_issues"]],
                    ["级别", "问题", "位置"],
                ))
                md.append("")
            md.append(f"> {det.get('note', '')}")
            md.append("")

        elif lv["level"] == "L3":
            det = lv["details"]
            md.append(f"对象总数: {det.get('total')}，安装成功: {det.get('installed')}，"
                      f"失败: {det.get('failed')}")
            md.append("")
            if det.get("by_kind"):
                md.extend(_table(
                    [[k, v["total"], v["ok"], v["total"] - v["ok"]]
                     for k, v in sorted(det["by_kind"].items())],
                    ["类型", "总数", "成功", "失败"],
                ))
                md.append("")
            if det.get("failures"):
                md.append("**安装失败明细**")
                md.append("")
                md.extend(_table(
                    [[f["kind"], f["object_name"], f.get("sql_state", ""),
                      f["error"].replace("|", "\\|")[:100]]
                     for f in det["failures"]],
                    ["类型", "对象", "SQLSTATE", "错误"],
                ))
                md.append("")

        elif lv["level"] == "L4":
            det = lv["details"]
            md.append(f"用例 {det.get('total')} 条："
                      f"通过 {det.get('passed')}，失败 {det.get('failed')}，"
                      f"错误 {det.get('errored')}，跳过 {det.get('skipped')}")
            md.append("")
            ratio = det.get("execution_ratio")
            if ratio is not None:
                min_cov = det.get("min_case_coverage", 0)
                flag = "" if not det.get("coverage_insufficient") else "  ⚠️ 覆盖不足"
                md.append(f"可执行用例占比: {ratio:.0%}（下限 {min_cov:.0%}）{flag}")
                md.append("")

    if report.cases:
        md.append("### 用例明细")
        md.append("")
        md.extend(_table(
            [[c.case_id, _STATUS_ICON.get(c.status, c.status), c.description,
              c.expected, c.actual, c.detail.replace("|", "\\|")[:60]]
             for c in report.cases],
            ["用例", "状态", "描述", "期望", "实际", "说明"],
        ))
        md.append("")

    return "\n".join(md)


def render_suite(suite: SuiteReport) -> str:
    """项目级 Markdown 报告"""
    s = suite.summary
    md: List[str] = []
    md.append(f"# 项目验证报告 - {suite.name}")
    md.append("")
    md.append(f"- 生成时间: {suite.timestamp}")
    md.append(f"- 制品数: {len(suite.reports)}")
    md.append(f"- **总分: {s.get('overall_score', 0)} - {s.get('grade', '-')}**")
    md.append(f"- Gate: {'通过' if s.get('gate_passed') else '未通过'}")
    md.append("")

    if s.get("gate_failures"):
        md.append("## Gate 失败项")
        md.append("")
        for f in s["gate_failures"][:50]:
            md.append(f"- {f}")
        if len(s["gate_failures"]) > 50:
            md.append(f"- ... 另有 {len(s['gate_failures']) - 50} 项")
        md.append("")

    md.append("## 套件级 L3/L4")
    md.append("")
    md.extend(render_levels(s.get("levels", [])))
    md.append("")

    l3 = next((lv for lv in s.get("levels", []) if lv["level"] == "L3"), None)
    if l3 and not l3.get("skipped"):
        det = l3["details"]
        md.append("### L3 按类型安装统计")
        md.append("")
        md.extend(_table(
            [[k, v["total"], v["ok"], v["total"] - v["ok"]]
             for k, v in sorted(det.get("by_kind", {}).items())],
            ["类型", "总数", "成功", "失败"],
        ))
        md.append("")
        per = det.get("per_artifact", {})
        if per:
            md.append("### L3 按制品")
            md.append("")
            md.extend(_table(
                [[k, v["total"], v["ok"], len(v["failures"])]
                 for k, v in sorted(per.items())],
                ["制品", "对象数", "成功", "失败"],
            ))
            md.append("")

    # P1-4: L3.5 首调冒烟明细
    l35 = next((lv for lv in s.get("levels", []) if lv["level"] == "L3.5"), None)
    if l35 and not l35.get("skipped"):
        det = l35["details"]
        by_status = det.get("by_status", {})
        md.append("### L3.5 首调冒烟 (DoD-3: 能安装 ≠ 可用)")
        md.append("")
        md.append(f"对象总数 {det.get('total')}，"
                  f"通过 {by_status.get('pass', 0)}，失败 {by_status.get('fail', 0)}，"
                  f"受阻 {by_status.get('blocked', 0)}，"
                  f"未等到触发 {by_status.get('not_fired', 0)}，"
                  f"无法构造调用 {by_status.get('unsupported', 0)}")
        md.append("")
        failures = det.get("failures", [])
        if failures:
            md.append("**首调失败明细**（fail，计入 DoD-3）")
            md.append("")
            md.extend(_table(
                [[f["kind"], f"{f['schema']}.{f['name']}",
                  (f.get("sql") or "").replace("|", "\\|")[:50],
                  (f.get("error") or "").replace("|", "\\|")[:100]]
                 for f in failures],
                ["类型", "对象", "首调 SQL", "错误"],
            ))
            md.append("")
        blocked = [r for r in det.get("results", [])
                   if r.get("status") in ("blocked", "not_fired")]
        if blocked:
            md.append("**受阻/未触发**（不计 fail，归因用）")
            md.append("")
            md.extend(_table(
                [[r["kind"], f"{r['schema']}.{r['name']}", r["status"],
                  (r.get("error") or "").replace("|", "\\|")[:80]]
                 for r in blocked[:30]],
                ["类型", "对象", "状态", "原因"],
            ))
            md.append("")

    cases = s.get("cases", [])
    if cases:
        md.append("### L4 用例明细")
        md.append("")
        by_status: Dict[str, int] = {}
        for c in cases:
            by_status[c["status"]] = by_status.get(c["status"], 0) + 1
        md.append("状态分布: " + "，".join(f"{k}={v}" for k, v in sorted(by_status.items())))
        md.append("")
        bad = [c for c in cases if c["status"] in ("fail", "error")]
        if bad:
            md.extend(_table(
                [[c["case_id"], _STATUS_ICON.get(c["status"], c["status"]),
                  c["description"], c["expected"], c["actual"],
                  c["detail"].replace("|", "\\|")[:70]]
                 for c in bad],
                ["用例", "状态", "描述", "期望", "实际", "说明"],
            ))
            md.append("")

    md.append("## 制品明细")
    md.append("")
    md.extend(_table(
        [[r.target,
          r.level("L1").score if r.level("L1") else "-",
          r.level("L2").score if r.level("L2") else "-",
          r.overall_score, r.grade,
          "通过" if r.gate_passed else "未通过"]
         for r in suite.reports],
        ["制品", "L1", "L2", "总分", "等级", "Gate"],
    ))
    md.append("")

    return "\n".join(md)


def write_json(obj: Any, path: Any) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # W0-5: cases 接入后，summary.cases[].actual 是 psycopg2 返的 Decimal
    # （PG NUMERIC），json 默认不能序列化。加 default=str 兑底。
    p.write_text(json.dumps(obj.to_dict() if hasattr(obj, "to_dict") else obj,
                            indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return p


def write_text(text: str, path: Any) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p
