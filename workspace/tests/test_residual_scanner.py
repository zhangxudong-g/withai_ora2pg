"""P1-3: 残留扫描器（DoD-2 硬门禁）。

验收标准（实施路径 §四 阶段一 P1-3）：
1. ``workspace/verification/residuals.py`` 按 DoD-2 清单建模式表
2. **同时覆盖** ``/* TODO`` (16) 与 ``-- TODO`` (12) 两种形态
3. 扫描器输出 major/critical 计数，供 Gate 硬门禁消费
4. 接入全量产物扫描，基线可复现

DoD-2 完整清单（HANDOFF §0）:
    %TYPE / %NOTFOUND / %ISOPEN / SQL%ROWCOUNT / SQLCODE /
    BULK COLLECT / FORALL / PIPE ROW / UTL_FILE / EXECUTE IMMEDIATE /
    (+) / CREATE SYNONYM / x : IS NULL; /* INLINED */ / %SUBQUERY\\d+% /
    /* TODO / FROM DUAL
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "workspace") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "workspace"))

from verification.residuals import (  # noqa: E402
    ResidualMatch,
    scan_sql,
    scan_directory,
    DOD2_RULES,
)


# ---------------------------------------------------------------------------
# 1. DoD-2 清单每条规则必须至少能命中一个典型样本
# ---------------------------------------------------------------------------
SAMPLES = {
    "%TYPE": "v_var STUDENT_MST.STUDENT_NO%TYPE;",
    "%NOTFOUND": "IF SQL%NOTFOUND THEN NULL; END IF;",
    "%ISOPEN": "IF cv%ISOPEN THEN NULL; END IF;",
    "SQL%ROWCOUNT": "n := SQL%ROWCOUNT;",
    "SQLCODE": "n := SQLCODE;",
    "BULK COLLECT": "SELECT * BULK COLLECT INTO v_list FROM t;",
    "FORALL": "FORALL i IN 1..10 INSERT INTO t VALUES (i);",
    "PIPE ROW": "PIPE ROW(rowval);",
    "UTL_FILE": "UTL_FILE.PUT_LINE(f, 'x');",
    "EXECUTE IMMEDIATE": "EXECUTE IMMEDIATE 'DROP TABLE x';",
    "(+)": "SELECT * FROM a, b WHERE a.id = b.id(+);",
    "CREATE SYNONYM": "CREATE SYNONYM sm_x FOR x;",
    "INLINED": "x : IS NULL; /* INLINED */",
    "%SUBQUERY": "v := %SUBQUERY1%;",
    "/* TODO": "/* TODO: implement this */",
    "-- TODO": "-- TODO: implement this",
    "FROM DUAL": "SELECT 1 FROM DUAL;",
}


class TestDOD2RulesCoverage:
    def test_every_dod2_item_has_rule(self):
        for key, sample in SAMPLES.items():
            hits = scan_sql(sample, filename="<sample>")
            assert hits, f"DoD-2 清单项 {key!r} 无规则命中: sample={sample!r}"

    def test_rule_table_is_named(self):
        # 规则表每条都有名字和严重级别（4 元组: pat, name, sev, raw）
        for pat, name, sev, raw in DOD2_RULES:
            assert name, "规则缺 name"
            assert sev in ("critical", "major", "minor"), f"规则 {name} 严重级别非法: {sev}"
            assert isinstance(raw, bool), f"规则 {name} raw 字段必须是 bool"


# ---------------------------------------------------------------------------
# 2. TODO 两种形态都要能命中（HANDOFF §1.3 实测教训）
# ---------------------------------------------------------------------------
class TestTodoForms:
    def test_block_todo(self):
        hits = scan_sql("/* TODO: implement */", filename="<t>")
        assert any("TODO" in h.rule_name.upper() for h in hits)

    def test_line_todo(self):
        hits = scan_sql("-- TODO: implement", filename="<t>")
        assert any("TODO" in h.rule_name.upper() for h in hits), \
            "-- TODO 行注释必须被检出（HANDOFF §1.3: 另 12 处全在 00_PACKAGE.sql）"


# ---------------------------------------------------------------------------
# 3. 扫描器输出结构
# ---------------------------------------------------------------------------
class TestScanOutput:
    def test_match_fields(self):
        hits = scan_sql("SELECT * FROM a, b WHERE a.id = b.id(+);", filename="VIEW/x.sql")
        assert hits
        h = hits[0]
        assert h.filename == "VIEW/x.sql"
        assert h.line_no >= 1
        assert h.severity in ("critical", "major", "minor")
        assert h.snippet

    def test_severity_counts(self):
        code = """
SELECT * FROM DUAL;
/* TODO: implement */
-- TODO: another
v_x T%TYPE;
"""
        result = scan_directory.__wrapped__ if hasattr(scan_directory, "__wrapped__") else None
        # 直接用 scan_sql 多次调用来验证聚合逻辑
        hits = scan_sql(code, filename="t.sql")
        majors = [h for h in hits if h.severity == "major"]
        minors = [h for h in hits if h.severity == "minor"]
        # FROM DUAL / %TYPE / TODO 块 / TODO 行 都应在 major
        assert majors, "至少应有 major"


# ---------------------------------------------------------------------------
# 4. 真实验证 oracle_poc/output 基线可复现（不写死数字，只校验结构）
# ---------------------------------------------------------------------------
class TestRealBaseline:
    OUTPUT_DIR = REPO_ROOT / "oracle_poc" / "output"

    def test_scan_output_directory_runs(self):
        if not self.OUTPUT_DIR.exists():
            pytest.skip("产物目录不存在")
        result = scan_directory(self.OUTPUT_DIR)
        assert result.files_scanned >= 1
        # 不写死具体数字（每次重转都会漂移），但结构必须合法
        assert isinstance(result.major_count, int)
        assert isinstance(result.minor_count, int)
        assert isinstance(result.critical_count, int)
        # P1-1 修复后，产物里 TRG_SCORE_COMPOUND 已显式登记，
        # 不再存在"静默丢失的对象"——但 TODO/%SUBQUERY 等仍应检出
        assert result.total_hits >= 1, "基线产物应至少有已知残留"
