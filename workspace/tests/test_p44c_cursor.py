"""P4-4c 阶段 6：Oracle 动态游标 `OPEN c FOR <函数>` → PG `OPEN c FOR EXECUTE <函数>`

PG 15.19 live 实测（改本测试前请先复测，别凭记忆改）：
- ``OPEN c FOR BUILD_QUERY(a, b);`` → **CREATE 直接失败**：
  ``ERROR: 语法错误 在 "build_query" 或附近的``
- ``OPEN c FOR EXECUTE BUILD_QUERY(a, b);`` → CREATE 成功，首调正确取到 3 行
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = REPO_ROOT / "workspace"

if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

from rules.p44c_cursor import (  # noqa: E402
    apply_p44c_cursor,
    fix_open_for_execute,
    scan_open_for_execute,
)


class TestOpenForExecute:
    def test_open_cursor_for_function(self):
        src = """CREATE OR REPLACE FUNCTION F() RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE v_cursor REFCURSOR;
BEGIN
    OPEN v_cursor FOR BUILD_QUERY(1, 2);
END$$;"""
        out = fix_open_for_execute(src)
        assert re.search(r"OPEN\s+v_cursor\s+FOR\s+EXECUTE\s+BUILD_QUERY\(", out, re.I)
        assert not re.search(r"FOR\s+BUILD_QUERY\(", out, re.I)

    def test_idempotent(self):
        src = """OPEN v_cursor FOR EXECUTE BUILD_QUERY(1, 2);"""
        out = fix_open_for_execute(src)
        assert out == src
        assert scan_open_for_execute(src)["count"] == 0

    def test_real_query_untouched(self):
        """真查询不能被加 EXECUTE。"""
        for q in ("SELECT i FROM t;", "WITH x AS (SELECT 1) SELECT * FROM x;",
                  "UPDATE t SET i = 1;", "INSERT INTO t VALUES (1);"):
            src = f"OPEN v_cursor FOR {q}"
            assert fix_open_for_execute(src) == src, q

    def test_variable_gets_execute(self):
        """PG 实测：变量形态也必须加 EXECUTE，否则语法错误。"""
        src = "OPEN v_cursor FOR v_sql;"
        out = fix_open_for_execute(src)
        assert re.search(r"FOR\s+EXECUTE\s+v_sql\s*;", out, re.I)

    def test_string_literal_gets_execute(self):
        """PG 实测：字符串字面量形态也必须加 EXECUTE。"""
        src = "OPEN v_cursor FOR 'SELECT i FROM t';"
        out = fix_open_for_execute(src)
        assert re.search(r"FOR\s+EXECUTE\s+'SELECT i FROM t'\s*;", out, re.I)

    def test_scan_count(self):
        src = ("OPEN a FOR F(1); OPEN b FOR G(2); OPEN c FOR EXECUTE H(3);")
        st = scan_open_for_execute(src)
        assert st["count"] == 2


@pytest.mark.skipif(
    not (REPO_ROOT / "oracle_poc/output/FUNCTION/00_FUNCTION.sql").exists(),
    reason="output not present",
)
class TestOraclePocOutput:
    def test_product_has_no_open_for_dynamic_expr(self):
        """产物中不得再有非查询形态的 `OPEN c FOR <表达式>`（PG CREATE 失败，DoD-1 阻塞）。"""
        bad: List[Tuple[str, int, str]] = []
        pat = re.compile(
            r"OPEN\s+\w+\s+FOR\s+(?!\s*(?:SELECT|WITH|INSERT|UPDATE|DELETE|MERGE|"
            r"VALUES|TABLE|EXECUTE)\b)[\s\S]{0,80}?;", re.I)
        for f in sorted((REPO_ROOT / "oracle_poc/output").rglob("*.sql")):
            text = f.read_text(encoding="utf-8", errors="ignore")
            for m in pat.finditer(text):
                ln = text[:m.start()].count("\n") + 1
                bad.append((f.name, ln, m.group(0).strip()))
        assert not bad, f"产物仍有非查询形态 OPEN FOR {len(bad)} 处:\n" + "\n".join(
            f"  {n}:{l}: {t}" for n, l, t in bad[:8])

    def test_rule_is_idempotent_on_product(self):
        """规则对产物必须是幂等的（重复跑不产生新差异）。"""
        for f in sorted((REPO_ROOT / "oracle_poc/output").rglob("*.sql")):
            text = f.read_text(encoding="utf-8", errors="ignore")
            once = apply_p44c_cursor(text)
            twice = apply_p44c_cursor(once)
            assert once == twice, f"{f.name}: 规则不幂等"