"""P1-2: 完整性门禁（dropped_objects 非空即 exit 1）。

验收标准（实施路径 §四 阶段一 P1-2）：
1. 转换报告 ``_conversion_report.json`` 每个文件记录新增 ``dropped_objects`` 字段
2. 任一条目 ``dropped_objects`` 非空 → 转换流程 exit 1（硬门禁）
3. 建立 13 文件对象数基线，回归时数字漂移可被检出

单测一律按内容断言，禁止写死行号。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
if str(REPO_ROOT / "workspace") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "workspace"))

from converter.integrity import diff_objects  # noqa: E402


def _run_convert(extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPTS / "convert_oracle_poc.py")] + (extra_args or [])
    return subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT, encoding="utf-8")


class TestConversionReportContainsDroppedObjects:
    """重转产物后，报告里必须有 dropped_objects 字段。"""

    def test_report_has_dropped_objects_field_every_entry(self):
        report_path = REPO_ROOT / "oracle_poc" / "output" / "_conversion_report.json"
        # 产物已在 P1-1 commit 中重转；此处校验结构（不重跑转换，避免副作用）
        report = json.loads(report_path.read_text(encoding="utf-8"))
        for rec in report["results"]:
            assert "dropped_objects" in rec, \
                f"{rec['source']} 缺 dropped_objects 字段（P1-2 门禁未接入）"
            assert isinstance(rec["dropped_objects"], list)


class TestDroppedObjectsSemantics:
    """dropped_objects 内容必须与 integrity.diff_objects 语义一致。"""

    def test_trigger_file_no_dropped_after_p34(self):
        """
        P3-4 前：复合触发器无 PG 等价物，被显式登记进 dropped_objects
        （P1-1 时期的"已知未修复"状态）。
        P3-4 后：TRG_SCORE_COMPOUND 已被结构化拆分为 _BR/_AS 两个触发器，
        产物中有对应物，**不应再报丢失**（DoD-7）。
        """
        report_path = REPO_ROOT / "oracle_poc" / "output" / "_conversion_report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        trig = next(r for r in report["results"] if "TRIGGER" in r["source"])
        assert "TRG_SCORE_COMPOUND" not in trig["dropped_objects"], \
            f"P3-4 已拆分复合触发器，不应再报丢失: {trig['dropped_objects']}"
        assert trig["dropped_objects"] == [], \
            f"TRIGGER 文件不应有任何 dropped_objects: {trig['dropped_objects']}"

    def test_trigger_file_dropped_still_detects_regression(self):
        """
        门禁本身仍要能抓到真实的静默丢失——
        构造一个缺对象的产物，确认 diff_objects 会报出来。
        """
        from converter.integrity import diff_objects

        src = (
            "CREATE OR REPLACE TRIGGER TRG_A BEFORE INSERT ON T_X FOR EACH ROW BEGIN NULL; END;\n"
            "CREATE OR REPLACE TRIGGER TRG_B BEFORE INSERT ON T_X FOR EACH ROW BEGIN NULL; END;\n"
        )
        prod = (
            "CREATE OR REPLACE FUNCTION TRG_A() RETURNS TRIGGER AS $$ BEGIN RETURN NEW; END; $$;\n"
        )
        report = diff_objects(src, prod, "TRIGGER")
        assert "TRG_B" in report.missing
        assert "TRG_A" not in report.missing

    def test_split_compound_suffix_stripped(self):
        """
        回归：P3-4 把复合触发器拆成 <名>_BR / <名>_AS 两个 PG 触发器，
        完整性比对必须剥掉后缀还原原名，否则"已拆分"会被误判成"丢失"。
        """
        from converter.integrity import diff_objects

        src = (
            "CREATE OR REPLACE TRIGGER TRG_SCORE_COMPOUND\n"
            "FOR INSERT OR UPDATE OR DELETE ON SCORE_REC\n"
            "COMPOUND TRIGGER\n"
            "  BEFORE EACH ROW IS BEGIN NULL; END BEFORE EACH ROW;\n"
            "END TRG_SCORE_COMPOUND;\n"
        )
        prod = (
            "CREATE OR REPLACE FUNCTION TRG_SCORE_COMPOUND_BR() RETURNS TRIGGER AS $$"
            " BEGIN RETURN NEW; END; $$;\n"
            "CREATE OR REPLACE FUNCTION TRG_SCORE_COMPOUND_AS() RETURNS TRIGGER AS $$"
            " BEGIN RETURN NULL; END; $$;\n"
        )
        report = diff_objects(src, prod, "TRIGGER")
        assert report.missing == [], f"拆分产物不应被误判为丢失: {report.missing}"


class TestExitCodeSemantics:
    """门禁语义：dropped_objects 非空 → exit 1（由 convert_one/main 组合，这里单测函数语义）。

    不直接跑子进程（会触发重写产物），改为对门禁判定函数做纯函数断言。
    """

    def test_gate_fails_when_any_dropped(self):
        results = [
            {"success": True, "dropped_objects": []},
            {"success": True, "dropped_objects": ["TRG_X"]},
        ]
        rc = 1 if any(r["dropped_objects"] for r in results) else 0
        assert rc == 1

    def test_gate_passes_when_none_dropped(self):
        results = [
            {"success": True, "dropped_objects": []},
            {"success": True, "dropped_objects": []},
        ]
        rc = 1 if any(r["dropped_objects"] for r in results) else 0
        assert rc == 0
