"""P4-4c 阶段 7：PL/SQL 游标声明 → PG plpgsql 游标声明（词序 + 记录变量类型）

PG 15.19 live 实测（改本测试前请先复测，别凭记忆改）：
- ``CURSOR c1 FOR SELECT ...;`` → CREATE 失败：
  ``语法错误 在 "FOR" 或附近的`` + ``无效的类型名字 "c1 FOR SELECT ..."``
  PG 语法是 ``name [ [NO] SCROLL ] CURSOR [ (args) ] FOR query``，**名字在前**
- ``c1 CURSOR FOR SELECT ...;`` → CREATE 成功，首调正确取到 3 行
- ``R_STUDENT c1;`` → CREATE 失败 ``类型 "c1" 不存在``（PG 没有"游标行类型"）
- ``R_STUDENT RECORD;`` → CREATE 成功，首调正确取到 3 行
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

from rules.p44c_cursor_decl import (  # noqa: E402
    apply_p44c_cursor_decl,
    fix_cursor_decls,
    scan_cursor_decls,
)

DECL = """DECLARE
    n       INTEGER := 0;
    {cursor}
    {rec}
BEGIN
    {body}
    RETURN n;
END"""


class TestCursorDecl:
    def test_order_is_fixed(self):
        src = DECL.format(cursor="CURSOR CUR_X FOR\n        SELECT i FROM t;",
                          rec="",
                          body="FOR r IN CUR_X LOOP n := n + 1; END LOOP;")
        out = fix_cursor_decls(src)
        assert re.search(r"CUR_X\s+CURSOR\s+FOR", out, re.I)
        assert not re.search(r"^\s*CURSOR\s+CUR_X\b", out, re.I | re.M)

    def test_is_keyword_converted_to_for(self):
        src = "    CURSOR CUR_Y IS\n        SELECT i FROM t;"
        out = fix_cursor_decls(src)
        assert re.search(r"CUR_Y\s+CURSOR\s+FOR", out, re.I)

    def test_record_variable_of_cursor_type(self):
        src = DECL.format(cursor="CURSOR CUR_X FOR\n        SELECT i FROM t;",
                          rec="R_STUDENT          CUR_X;",
                          body="FOR r IN CUR_X LOOP n := n + 1; END LOOP;")
        out = fix_cursor_decls(src)
        assert re.search(r"R_STUDENT\s+RECORD\s*;", out), out

    def test_statements_not_touched(self):
        """OPEN/CLOSE/FETCH 是语句不是声明，不能被当 RECORD 声明改掉。"""
        src = DECL.format(cursor="CURSOR CUR_X FOR\n        SELECT i FROM t;",
                          rec="",
                          body="OPEN CUR_X;\n    CLOSE CUR_X;")
        out = fix_cursor_decls(src)
        assert "OPEN CUR_X;" in out
        assert "CLOSE CUR_X;" in out
        assert "OPEN CUR_X RECORD" not in out

    def test_ordinary_var_untouched(self):
        src = "DECLARE\n    v_cnt INTEGER;\n    v_name VARCHAR(10);\nBEGIN\n    NULL;\nEND"
        assert fix_cursor_decls(src) == src

    def test_idempotent(self):
        src = DECL.format(cursor="CURSOR CUR_X FOR\n        SELECT i FROM t;",
                          rec="R_STUDENT CUR_X;",
                          body="FOR r IN CUR_X LOOP n := n + 1; END LOOP;")
        once = fix_cursor_decls(src)
        assert fix_cursor_decls(once) == once

    def test_scan_counts(self):
        src = DECL.format(cursor="CURSOR CUR_X FOR\n        SELECT i FROM t;",
                          rec="R_STUDENT CUR_X;",
                          body="FOR r IN CUR_X LOOP n := n + 1; END LOOP;")
        st = scan_cursor_decls(src)
        assert st["order_fixed"] == 1
        assert st["record_fixed"] == 1


@pytest.mark.skipif(
    not (REPO_ROOT / "oracle_poc/output/PROCEDURE/00_PROCEDURE.sql").exists(),
    reason="output not present",
)
class TestOraclePocOutput:
    def test_product_has_no_oracle_order_cursor(self):
        """产物中不得再有 `CURSOR <name> FOR/IS`（PG CREATE 失败，DoD-1 阻塞）。"""
        bad: List[Tuple[str, int, str]] = []
        pat = re.compile(r"^\s*CURSOR\s+\w+\s+(?:FOR|IS)\b", re.I | re.M)
        for f in sorted((REPO_ROOT / "oracle_poc/output").rglob("*.sql")):
            text = f.read_text(encoding="utf-8", errors="ignore")
            for m in pat.finditer(text):
                ln = text[:m.start()].count("\n") + 1
                bad.append((f.name, ln, m.group(0).strip()))
        assert not bad, f"产物仍有 Oracle 词序游标声明 {len(bad)} 处:\n" + "\n".join(
            f"  {n}:{l}: {t}" for n, l, t in bad[:8])

    def test_product_has_no_cursor_typed_record_var(self):
        """产物中不得有 `<var> <游标名>;` 这种非法记录变量声明。"""
        bad: List[Tuple[str, int, str]] = []
        cursors: set = set()
        texts: dict = {}
        for f in sorted((REPO_ROOT / "oracle_poc/output").rglob("*.sql")):
            text = f.read_text(encoding="utf-8", errors="ignore")
            texts[f.name] = text
            for m in re.finditer(r"\bCURSOR\s+(\w+)\s+(?:FOR|IS)\b", text, re.I):
                cursors.add(m.group(1).upper())
        kw = {"OPEN", "CLOSE", "FETCH", "FOR", "LOOP", "IF", "WHILE", "RETURN",
              "RAISE", "EXECUTE", "PERFORM", "BEGIN", "END", "SELECT", "INSERT",
              "UPDATE", "DELETE", "COMMIT", "ROLLBACK", "GET", "CALL", "NULL"}
        for name, text in texts.items():
            for m in re.finditer(r"^\s*(\w+)\s+(\w+)\s*;\s*$", text, re.M):
                if m.group(1).upper() not in kw and m.group(2).upper() in cursors:
                    ln = text[:m.start()].count("\n") + 1
                    bad.append((name, ln, m.group(0).strip()))
        assert not bad, f"产物仍有游标类型记录变量 {len(bad)} 处:\n" + "\n".join(
            f"  {n}:{l}: {t}" for n, l, t in bad[:8])

    def test_rule_is_idempotent_on_product(self):
        for f in sorted((REPO_ROOT / "oracle_poc/output").rglob("*.sql")):
            text = f.read_text(encoding="utf-8", errors="ignore")
            once = apply_p44c_cursor_decl(text)
            assert apply_p44c_cursor_decl(once) == once, f"{f.name}: 规则不幂等"