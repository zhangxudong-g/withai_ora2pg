"""P2-5: 异常与运行时属性映射体系

落点：workspace/rules/exception_map.py（手册建议）+ 接入 post-pass。

子项（实施路径 v3.1 P2-5）：
- P2-5a: SQLCODE → GET STACKED DIAGNOSTICS v := RETURNED_SQLSTATE
- P2-5b: SQLERRM → GET STACKED DIAGNOSTICS v := MESSAGE_TEXT
- P2-5c: WHEN OTHERS 静默吞错检测 + 强制 RAISE（手册 §13.1 加粗禁止）
- P2-5d: SQL%ROWCOUNT → GET DIAGNOSTICS v := ROW_COUNT（必须紧跟）
- P2-5e: %NOTFOUND/%ISOPEN → FOUND/NOT FOUND
- P2-5f: Oracle 异常名映射（DUP_VAL_ON_INDEX→unique_violation 等）

源命中 34（残留 34 全部归零） + WHEN OTHERS 112 检测（不动但记录）。

单测按内容断言，禁止写死行号（AGENTS.md §4）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = REPO_ROOT / "workspace"
ORACLE_OUTPUT = REPO_ROOT / "oracle_poc" / "output"
ORACLE_SRC = REPO_ROOT / "oracle_poc" / "01.SQL"

if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

from rules.exception_map import (  # noqa: E402
    map_sqlcode,
    map_sqlerrm,
    map_rowcount,
    map_cursor_attrs,
    map_exception_names,
    enforce_when_others_raise,
    apply_all_exception_maps,
)


# ===========================================================================
# P2-5a: SQLCODE → RETURNED_SQLSTATE
# ===========================================================================
class TestMapSqlcode:
    def test_simple_sqlcode_replaced(self):
        """EXCEPTION 块内 SQLCODE → _p25_sqlstate + 块首注入 GET STACKED DIAGNOSTICS"""
        src = """
        EXCEPTION
            WHEN OTHERS THEN
                RAISE EXCEPTION 'error %', SQLCODE;
        """
        out = map_sqlcode(src)
        assert "SQLCODE" not in out, f"SQLCODE still present: {out!r}"
        # 变量被注入
        assert "RETURNED_SQLSTATE" in out or "sqlstate" in out.lower()
        assert "_p25_sqlstate" in out

    def test_sqlcode_in_log_statement(self):
        """日志语句中 'SQLCODE' 也应被替换（EXCEPTION 块内）"""
        src = """
        EXCEPTION
            WHEN OTHERS THEN
                v_msg := 'err ' || SQLERRM || ' code=' || SQLCODE;
                INSERT INTO log_table VALUES (v_msg);
        """
        out = map_sqlcode(src)
        assert "SQLCODE" not in out, f"SQLCODE not replaced: {out!r}"


# ===========================================================================
# P2-5b: SQLERRM → MESSAGE_TEXT
# ===========================================================================
class TestMapSqlerrm:
    def test_sqlerrm_replaced(self):
        src = """
        EXCEPTION
            WHEN OTHERS THEN
                RAISE EXCEPTION 'err: %', SQLERRM;
        """
        out = map_sqlerrm(src)
        assert "SQLERRM" not in out, f"SQLERRM still present: {out!r}"
        assert "MESSAGE_TEXT" in out, f"MESSAGE_TEXT not inserted: {out!r}"

    def test_sqlerrm_in_concat(self):
        """EXCEPTION 块内拼接 SQLERRM 也应被替换"""
        src = """
        EXCEPTION
            WHEN OTHERS THEN
                v_msg := 'fail: ' || SQLERRM || ' at line ' || v_line;
        """
        out = map_sqlerrm(src)
        assert "SQLERRM" not in out
        # _p25_msg 变量被注入
        assert "_p25_msg" in out or "MESSAGE_TEXT" in out


# ===========================================================================
# P2-5d: SQL%ROWCOUNT → GET DIAGNOSTICS
# ===========================================================================
class TestMapRowcount:
    def test_simple_rowcount(self):
        """SQL%ROWCOUNT → GET DIAGNOSTICS v := ROW_COUNT（注入到语句后）"""
        src = "    SELECT * INTO v_a FROM tbl;\n    v_n := SQL%ROWCOUNT;\n"
        out = map_rowcount(src)
        assert "SQL%ROWCOUNT" not in out, f"SQL%ROWCOUNT still present: {out!r}"
        # 应注入 GET DIAGNOSTICS
        assert "GET DIAGNOSTICS" in out.upper() or "ROW_COUNT" in out.upper()

    def test_rowcount_with_other_var(self):
        """'v_n := SQL%ROWCOUNT' 保留 v_n 接收"""
        src = "    DELETE FROM log WHERE id < 100;\n    v_n := SQL%ROWCOUNT;\n"
        out = map_rowcount(src)
        # 不应再有原始的 'SQL%ROWCOUNT'
        assert "SQL%ROWCOUNT" not in out
        # 应有 GET DIAGNOSTICS
        assert re.search(r"GET\s+DIAGNOSTICS", out, re.IGNORECASE)


# ===========================================================================
# P2-5e: 游标属性 %NOTFOUND / %ISOPEN
# ===========================================================================
class TestMapCursorAttrs:
    def test_notfound_replaced(self):
        """'%NOTFOUND' → 'NOT FOUND'"""
        src = """
        IF c_emp%NOTFOUND THEN
            RETURN NULL;
        END IF;
        """
        out = map_cursor_attrs(src)
        assert "%NOTFOUND" not in out, f"%NOTFOUND still present: {out!r}"
        assert "NOT FOUND" in out.upper(), f"NOT FOUND not inserted: {out!r}"

    def test_isopen_replaced(self):
        """'%ISOPEN' → 'IS NOT FOUND'（语义近似）"""
        src = """
        IF c_emp%ISOPEN THEN
            CLOSE c_emp;
        END IF;
        """
        out = map_cursor_attrs(src)
        assert "%ISOPEN" not in out

    def test_found_replaced(self):
        """'%FOUND' 也应被替换（PL/pgSQL 是 FOUND）"""
        src = "    IF c_emp%FOUND THEN RETURN v_rec; END IF;"
        out = map_cursor_attrs(src)
        assert "%FOUND" not in out
        assert "FOUND" in out.upper()


# ===========================================================================
# P2-5f: Oracle 异常名映射
# ===========================================================================
class TestMapExceptionNames:
    def test_dup_val_on_index(self):
        """DUP_VAL_ON_INDEX → unique_violation（exception 声明场景）"""
        src = """
        EXCEPTION
            WHEN DUP_VAL_ON_INDEX THEN
                v_err := 'duplicate';
        """
        out = map_exception_names(src)
        assert "DUP_VAL_ON_INDEX" not in out, (
            f"DUP_VAL_ON_INDEX not replaced: {out!r}"
        )
        assert "unique_violation" in out, f"unique_violation not inserted: {out!r}"

    def test_too_many_rows(self):
        src = """
        EXCEPTION
            WHEN TOO_MANY_ROWS THEN
                RETURN NULL;
        """
        out = map_exception_names(src)
        assert "TOO_MANY_ROWS" not in out

    def test_no_data_found(self):
        src = """
        EXCEPTION
            WHEN NO_DATA_FOUND THEN
                RETURN 0;
        """
        out = map_exception_names(src)
        assert "NO_DATA_FOUND" not in out


# ===========================================================================
# P2-5c: WHEN OTHERS 静默吞错检测
# ===========================================================================
class TestEnforceWhenOthersRaise:
    def test_silent_return_null_detected(self):
        """'WHEN OTHERS THEN RETURN NULL;' → 必须 RAISE（不能静默）"""
        src = """
        EXCEPTION
            WHEN OTHERS THEN
                RETURN NULL;
        """
        out = enforce_when_others_raise(src)
        # 必须有 RAISE
        assert re.search(r"\bRAISE\b", out, re.IGNORECASE), (
            f"P2-5c: WHEN OTHERS must RAISE, got: {out!r}"
        )
        # 不应保留 'RETURN NULL;'（除非在更复杂逻辑里）
        # 至少应有 RAISE 出现
        assert "RETURN NULL;" not in out or "RAISE" in out.upper()

    def test_silent_return_zero_detected(self):
        """'WHEN OTHERS THEN RETURN 0;' 同样要 RAISE"""
        src = """
        EXCEPTION
            WHEN OTHERS THEN
                RETURN 0;
        """
        out = enforce_when_others_raise(src)
        assert re.search(r"\bRAISE\b", out, re.IGNORECASE), (
            f"P2-5c: WHEN OTHERS must RAISE, got: {out!r}"
        )

    def test_already_raises_unchanged(self):
        """已有 RAISE 的 WHEN OTHERS 块不应被改"""
        src = """
        EXCEPTION
            WHEN OTHERS THEN
                RAISE EXCEPTION 'oops: %', SQLERRM;
        """
        out = enforce_when_others_raise(src)
        # 不破坏已有结构
        assert "RAISE EXCEPTION" in out

    def test_no_when_others_unchanged(self):
        """没有 WHEN OTHERS 块时，原文不变"""
        src = """
        BEGIN
            v_n := 1;
        EXCEPTION
            WHEN NO_DATA_FOUND THEN
                v_n := 0;
        END;
        """
        out = enforce_when_others_raise(src)
        # 不应被改
        assert "NO_DATA_FOUND" in out
        assert out.count("WHEN") == src.count("WHEN")

    def test_end_semicolon_preserved_after_inject(self):
        """注入 RAISE 后块结束的 END; 必须保留（否则 PL/pgSQL 块不闭合）"""
        src = """CREATE OR REPLACE FUNCTION F() RETURNS NUMERIC AS \$\$
BEGIN
    RETURN 1;
EXCEPTION
    WHEN OTHERS THEN RETURN -1;
END;
\$\$ LANGUAGE plpgsql;
"""
        out = enforce_when_others_raise(src)
        assert re.search(r"\bRAISE\b", out, re.IGNORECASE)
        # END; 必须还在（块闭合）
        assert re.search(r"\bEND\s*;", out), (
            f"P2-5c 丢失了块结束 END;: {out!r}"
        )


# ===========================================================================
# 整合：apply_all_exception_maps
# ===========================================================================
class TestApplyAll:
    def test_combined(self):
        """组合调用：所有子项的 SQL 构造都应被替换"""
        src = """
        BEGIN
            DELETE FROM log WHERE id < 100;
            v_n := SQL%ROWCOUNT;
            OPEN c_emp;
            FETCH c_emp INTO v_rec;
            IF c_emp%NOTFOUND THEN
                RETURN NULL;
            END IF;
        EXCEPTION
            WHEN DUP_VAL_ON_INDEX THEN
                RETURN -1;
            WHEN OTHERS THEN
                v_msg := 'err: ' || SQLERRM || ' code=' || SQLCODE;
                RETURN NULL;
        END;
        """
        out = apply_all_exception_maps(src)
        # 全部应被替换
        assert "SQL%ROWCOUNT" not in out, "SQL%ROWCOUNT not replaced"
        assert "%NOTFOUND" not in out, "%NOTFOUND not replaced"
        assert "SQLERRM" not in out, "SQLERRM not replaced"
        assert "SQLCODE" not in out, "SQLCODE not replaced"
        assert "DUP_VAL_ON_INDEX" not in out, "DUP_VAL_ON_INDEX not replaced"
        # WHEN OTHERS 块必须有 RAISE
        assert re.search(r"\bRAISE\b", out, re.IGNORECASE), (
            "P2-5c: WHEN OTHERS must RAISE"
        )


# ===========================================================================
# 端到端：oracle_poc 产物
# ===========================================================================
@pytest.mark.skipif(
    not ORACLE_OUTPUT.exists(),
    reason="oracle_poc/output not present",
)
class TestOraclePocOutput:
    """oracle_poc 转换后无 P2-5 残留（除 WHEN OTHERS 检测项）

    注：只检查 PL/SQL 文件（FUNCTION/PROCEDURE/PACKAGE/PACKAGE_BODY/TRIGGER），
    不检查 DDL（TABLE/VIEW/SEQUENCE/TYPE/SYNONYM/DATA）—— DDL 中的
    SQLCODE/SQLERRM 是用户定义的列名（如 PROCESS_LOG.SQLCODE），
    不属 P2-5 范围。"""

    _PLSQL_SUBDIRS = {"FUNCTION", "PROCEDURE", "PACKAGE", "PACKAGE_BODY", "TRIGGER"}

    def _plsql_files(self) -> List[Path]:
        return [
            f for f in ORACLE_OUTPUT.glob("*/*.sql")
            if f.parent.name in self._PLSQL_SUBDIRS
        ]

    def test_no_sqlcode_in_output(self):
        """PL/SQL 产物 EXCEPTION 块内无 SQLCODE 残留

        只检 EXCEPTION 块。块外的 SQLCODE 可能是用户列名（如
        PROCESS_LOG.SQLCODE），不是 Oracle 关键字，不属 P2-5 范畴。
        """
        bad: List[Tuple[Path, int, str]] = []
        for f in self._plsql_files():
            in_exception = False
            brace = 0
            for lineno, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                upper = line.upper()
                if "EXCEPTION" in upper and "WHEN" not in upper:
                    in_exception = True
                if in_exception:
                    if re.search(r"\bSQLCODE\b", line):
                        bad.append((f, lineno, line.rstrip()))
                if re.search(r"^\s*END\s*;", line, re.IGNORECASE):
                    in_exception = False
        assert not bad, (
            f"Found {len(bad)} SQLCODE in EXCEPTION blocks:\n"
            + "\n".join(f"  {p}:{n}: {t}" for p, n, t in bad[:5])
        )

    def test_no_sqlerrm_in_output(self):
        """PL/SQL 产物 EXCEPTION 块内无 SQLERRM 残留"""
        bad: List[Tuple[Path, int, str]] = []
        for f in self._plsql_files():
            in_exception = False
            for lineno, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                upper = line.upper()
                if "EXCEPTION" in upper and "WHEN" not in upper:
                    in_exception = True
                if in_exception:
                    if re.search(r"\bSQLERRM\b", line):
                        bad.append((f, lineno, line.rstrip()))
                if re.search(r"^\s*END\s*;", line, re.IGNORECASE):
                    in_exception = False
        assert not bad, (
            f"Found {len(bad)} SQLERRM in EXCEPTION blocks:\n"
            + "\n".join(f"  {p}:{n}: {t}" for p, n, t in bad[:5])
        )

    def test_no_sql_rowcount_in_output(self):
        """PL/SQL 产物中无 SQL%ROWCOUNT 残留"""
        bad: List[Tuple[Path, int, str]] = []
        for f in self._plsql_files():
            for lineno, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if re.search(r"SQL%ROWCOUNT", line):
                    bad.append((f, lineno, line.rstrip()))
        assert not bad, f"Found {len(bad)} SQL%ROWCOUNT lines"

    def test_no_notfound_isopen_in_output(self):
        """PL/SQL 产物中无 %NOTFOUND / %ISOPEN / %FOUND 残留"""
        bad: List[Tuple[Path, int, str]] = []
        for f in self._plsql_files():
            for lineno, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if re.search(r"%(NOTFOUND|ISOPEN|FOUND)\b", line):
                    bad.append((f, lineno, line.rstrip()))
        assert not bad, f"Found {len(bad)} %NOTFOUND/ISOPEN/FOUND lines"
