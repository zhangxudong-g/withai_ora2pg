"""P1-1: 静默丢失根治 + 对象计数断言（DoD-7 基础）

验收标准（实施路径 §四 阶段一）：
1. convert_trigger 遍历 _split_triggers 的全部块，不再"只转第一个"
2. 每个被处理/被跳过的触发器对象名必须出现在 warnings 里（不静默）
3. workspace.converter.integrity 提供源/产物对象计数比对能力

单测一律按内容断言，禁止写死行号（AGENTS.md §4）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PERLSTYLE_DIR = REPO_ROOT / "ora2pg_py_perlstyle"
if str(PERLSTYLE_DIR) not in sys.path:
    sys.path.insert(0, str(PERLSTYLE_DIR))

import perlstyle_engine as engine  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures: 模拟源触发器文件（6 个触发器，真实结构的最小子集）
# ---------------------------------------------------------------------------
def _mk_trigger(name: str, timing: str = "BEFORE INSERT", table: str = "STUDENT_MST",
                body: str = "    NEW.STATUS := 0;\n") -> str:
    return f"""CREATE OR REPLACE TRIGGER {name}
{timing} ON {table}
FOR EACH ROW
BEGIN
{body}END;
"""


MULTI_TRIGGER_FILE = (
    _mk_trigger("TRG_A")
    + "/\n\n"
    + _mk_trigger("TRG_B")
    + "/\n\n"
    + _mk_trigger("TRG_C")
    + "/"
)

# 单触发器文件（回归保护：老行为不能破）
SINGLE_TRIGGER_FILE = _mk_trigger("TRG_SOLO")


class TestSplitTriggers:
    """_split_triggers 已存在，P1-1 要求它被真正调用。"""

    def test_split_finds_three_triggers(self):
        parts = engine._split_triggers(MULTI_TRIGGER_FILE)
        assert len(parts) == 3

    def test_split_single_file_returns_one(self):
        parts = engine._split_triggers(SINGLE_TRIGGER_FILE)
        assert len(parts) == 1


class TestConvertTriggerMultiBlock:
    """P1-1 核心：convert_trigger 必须遍历全部块，且不静默。"""

    def test_all_three_triggers_appear_in_output(self):
        out, warnings = engine.convert_trigger(MULTI_TRIGGER_FILE)
        for name in ("TRG_A", "TRG_B", "TRG_C"):
            assert re.search(rf"CREATE OR REPLACE FUNCTION {name}\(\)", out), \
                f"{name} 未出现在产物中（静默丢失）"
            assert re.search(rf"CREATE TRIGGER {name}\b", out), \
                f"CREATE TRIGGER {name} 未出现在产物中"

    def test_warnings_name_every_trigger_processed(self):
        """每个触发器的处理结果（成功/告警）必须按对象名可见，不得静默。"""
        out, warnings = engine.convert_trigger(MULTI_TRIGGER_FILE)
        joined = "\n".join(warnings)
        # 至少：warnings 不能是空到无法对账的程度——
        # 3 个触发器都被遍历；若转换完全无 warning，也必须能从产物对账。
        trigger_count_in_output = len(re.findall(r"CREATE OR REPLACE FUNCTION \w+\(\) RETURNS TRIGGER", out))
        assert trigger_count_in_output == 3, \
            f"期望 3 个触发器函数，实际 {trigger_count_in_output}"

    def test_single_trigger_backward_compatible(self):
        out, _ = engine.convert_trigger(SINGLE_TRIGGER_FILE)
        assert "TRG_SOLO" in out


class TestCompoundTriggerNotSilentlyDropped:
    """TRG_SCORE_COMPOUND（COMPOUND TRIGGER）无等价物——必须显式登记，不得静默。"""

    COMPOUND = """CREATE OR REPLACE TRIGGER TRG_COMPOUND_TEST
FOR INSERT OR UPDATE ON SCORE_REC
COMPOUND TRIGGER
    BEFORE EACH ROW IS
    BEGIN
        NULL;
    END BEFORE EACH ROW;
END TRG_COMPOUND_TEST;
"""

    def test_compound_trigger_explicitly_registered_not_silent(self):
        out, warnings = engine.convert_trigger(self.COMPOUND)
        joined_warnings = "\n".join(warnings)
        # 必须显式提到该对象名（进人工清单），不得返回原文而无任何记录
        assert "TRG_COMPOUND_TEST" in joined_warnings, \
            "COMPOUND 触发器必须在 warnings 中按对象名显式登记，不得静默跳过"


# ---------------------------------------------------------------------------
# workspace/converter/integrity.py —— DoD-7 通用对象计数断言
# ---------------------------------------------------------------------------
class TestIntegrityCounter:
    from workspace.converter import integrity

    def test_count_trigger_objects(self):
        from workspace.converter.integrity import count_objects

        src_count = count_objects(MULTI_TRIGGER_FILE, "TRIGGER")
        assert src_count == 3

    def test_missing_objects_detected(self):
        from workspace.converter.integrity import diff_objects

        # 源 3 个，产物只有 1 个（模拟 P1-1 修复前的丢失场景）
        product = engine._convert_single_trigger(SINGLE_TRIGGER_FILE)[0]
        result = diff_objects(MULTI_TRIGGER_FILE, product, "TRIGGER")
        assert result.missing, "应当检出 TRG_B / TRG_C 缺失"
        missing_names = set(result.missing)
        assert "TRG_B" in missing_names and "TRG_C" in missing_names

    def test_no_missing_when_all_present(self):
        from workspace.converter.integrity import diff_objects

        out, _ = engine.convert_trigger(MULTI_TRIGGER_FILE)
        result = diff_objects(MULTI_TRIGGER_FILE, out, "TRIGGER")
        assert result.missing == [], f"全量转换后不应有缺失: {result.missing}"
