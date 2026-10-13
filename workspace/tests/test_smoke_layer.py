"""P1-4: L3.5 首调冒烟层单测。

覆盖:
1. 纯函数: build_arg_expr / parse_trigger_fn / attribute_error_to_fn / quote_ident
2. 集成 (需 PG 15, 不可用时 skip): 真实建表/函数/过程/视图/序列/触发器,
   验证 pass / fail / 归因 / 回滚不留数据
"""
from __future__ import annotations

import random
import string
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "workspace") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "workspace"))

from verification.pg_sandbox import DEFAULT_PG, PGSandbox  # noqa: E402
from verification.sqlsplit import split_statements  # noqa: E402
from verification.smoke_check import (  # noqa: E402
    attribute_error_to_fn,
    build_arg_expr,
    parse_trigger_fn,
    quote_ident,
    smoke_all,
)


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------
class TestBuildArgExpr:
    def test_non_strict_uses_null(self):
        assert build_arg_expr("integer", strict=False) == "NULL::integer"
        assert build_arg_expr("text", strict=False) == "NULL::text"

    def test_strict_numeric_gets_dummy(self):
        assert build_arg_expr("numeric", strict=True) == "'1'::numeric"
        assert build_arg_expr("integer", strict=True) == "'1'::integer"

    def test_strict_text_gets_empty(self):
        assert build_arg_expr("character varying", strict=True) == "''::character varying"

    def test_strict_date(self):
        assert build_arg_expr("date", strict=True) == "'1970-01-01'::date"

    def test_strict_bool(self):
        assert build_arg_expr("boolean", strict=True) == "true"


class TestParseTriggerFn:
    def test_simple(self):
        assert parse_trigger_fn("EXECUTE PROCEDURE trg_student_bi()") == "trg_student_bi"

    def test_schema_qualified(self):
        assert parse_trigger_fn("EXECUTE PROCEDURE myschema.trg_x()") == "trg_x"

    def test_case_insensitive(self):
        assert parse_trigger_fn("execute procedure Trg_A()") == "trg_a"


class TestAttributeError:
    def test_plpgsql_context(self):
        err = ('syntax error at or near "%" | CONTEXT: '
               'PL/pgSQL function trg_student_bi() line 2 at statement block')
        assert attribute_error_to_fn(err) == "trg_student_bi"

    def test_plpgsql_context_chinese_locale(self):
        """中文 locale 的 CONTEXT 无空格: PL/pgSQL函数trg_x()"""
        err = ('记录"new"没有字段"col_not_exist" | CONTEXT: '
               'PL/pgSQL assignment "NEW.col_not_exist := 1" | '
               '在赋值的第1行的PL/pgSQL函数trg_bad_fn()')
        assert attribute_error_to_fn(err) == "trg_bad_fn"

    def test_constraint_error_no_attribution(self):
        assert attribute_error_to_fn('null value in column "id" violates not-null '
                                     'constraint') is None

    def test_empty(self):
        assert attribute_error_to_fn("") is None


class TestQuoteIdent:
    def test_simple(self):
        assert quote_ident("abc") == '"abc"'

    def test_embedded_quote(self):
        assert quote_ident('a"b') == '"a""b"'


# ---------------------------------------------------------------------------
# 集成测试（真实 PG）
# ---------------------------------------------------------------------------
def _pg_available() -> bool:
    try:
        import psycopg2
        conn = psycopg2.connect(connect_timeout=3, **DEFAULT_PG)
        conn.close()
        return True
    except Exception:
        return False


PG_OK = _pg_available()
pytestmark = pytest.mark.skipif(not PG_OK, reason="PG 不可用，冒烟集成测试跳过")


def _rand_suffix() -> str:
    return "".join(random.choices(string.ascii_lowercase, k=6))


TEST_DDL = """
CREATE TABLE t1 (id integer, payload text);
CREATE TABLE t2 (id integer, payload text);
CREATE VIEW v_ok AS SELECT 1 AS one;
CREATE SEQUENCE seq_ok START 1;
CREATE FUNCTION f_ok() RETURNS integer AS $$ BEGIN RETURN 1; END $$ LANGUAGE plpgsql;
CREATE FUNCTION f_args(a integer, b text) RETURNS integer
    AS $$ BEGIN RETURN COALESCE(a, 0); END $$ LANGUAGE plpgsql;
CREATE FUNCTION f_bad() RETURNS integer
    AS $$ BEGIN PERFORM 1/0; RETURN 0; END $$ LANGUAGE plpgsql;
CREATE FUNCTION f_strict(a integer) RETURNS integer
    AS $$ BEGIN RETURN a + 1; END $$ LANGUAGE plpgsql STRICT;
CREATE PROCEDURE p_ok() AS $$ BEGIN NULL; END $$ LANGUAGE plpgsql;
CREATE FUNCTION trg_bad_fn() RETURNS trigger
    AS $$ BEGIN NEW.col_not_exist := 1; RETURN NEW; END $$ LANGUAGE plpgsql;
CREATE FUNCTION trg_good_fn() RETURNS trigger
    AS $$ BEGIN NEW.payload := 'x'; RETURN NEW; END $$ LANGUAGE plpgsql;
CREATE FUNCTION trg_aft_fn() RETURNS trigger
    AS $$ BEGIN RETURN NULL; END $$ LANGUAGE plpgsql;
CREATE TRIGGER trg_bad BEFORE INSERT ON t1
    FOR EACH ROW EXECUTE PROCEDURE trg_bad_fn();
CREATE TRIGGER trg_good BEFORE INSERT ON t2
    FOR EACH ROW EXECUTE PROCEDURE trg_good_fn();
CREATE TRIGGER trg_aft AFTER UPDATE ON t2
    FOR EACH ROW EXECUTE PROCEDURE trg_aft_fn();
"""


@pytest.fixture()
def sandbox():
    schema = f"smoke_test_{_rand_suffix()}"
    sb = PGSandbox(schema=schema)
    ok, err = sb.available()
    if not ok:
        pytest.skip(f"PG 不可用: {err}")
    sb.reset()
    for st in split_statements(TEST_DDL):
        st = st.strip()
        if st:
            ok2, err2, _state = sb.execute(st)
            assert ok2, f"测试夹具安装失败: {st[:60]} -> {err2}"
    yield sb
    sb.drop()
    sb.close()


class TestSmokeIntegration:
    def _by_key(self, report):
        return {(r.kind, r.schema, r.name): r for r in report.results}

    def test_function_pass_and_fail(self, sandbox):
        report = smoke_all(sandbox, schemas=[sandbox.schema])
        d = self._by_key(report)
        assert d[("FUNCTION", sandbox.schema, "f_ok")].status == "pass"
        assert d[("FUNCTION", sandbox.schema, "f_bad")].status == "fail"
        assert d[("FUNCTION", sandbox.schema, "f_args")].status == "pass"

    def test_strict_function_gets_dummy_not_null(self, sandbox):
        """STRICT 函数传 NULL 不会执行函数体（假通过）——必须用哑值。"""
        report = smoke_all(sandbox, schemas=[sandbox.schema])
        d = self._by_key(report)
        r = d[("FUNCTION", sandbox.schema, "f_strict")]
        assert r.status == "pass"
        assert "NULL::" not in r.sql

    def test_procedure_and_view_and_sequence(self, sandbox):
        report = smoke_all(sandbox, schemas=[sandbox.schema])
        d = self._by_key(report)
        assert d[("PROCEDURE", sandbox.schema, "p_ok")].status == "pass"
        assert d[("VIEW", sandbox.schema, "v_ok")].status == "pass"
        assert d[("SEQUENCE", sandbox.schema, "seq_ok")].status == "pass"

    def test_trigger_fail_attributed_by_context(self, sandbox):
        report = smoke_all(sandbox, schemas=[sandbox.schema])
        d = self._by_key(report)
        r = d[("TRIGGER", sandbox.schema, "trg_bad")]
        assert r.status == "fail", f"trg_bad 应 fail, 实际 {r.status}: {r.error}"
        assert "trg_bad_fn" in r.error or "col_not_exist" in r.error

    def test_trigger_pass_on_healthy_table(self, sandbox):
        report = smoke_all(sandbox, schemas=[sandbox.schema])
        d = self._by_key(report)
        assert d[("TRIGGER", sandbox.schema, "trg_good")].status == "pass"
        assert d[("TRIGGER", sandbox.schema, "trg_aft")].status == "pass"

    def test_rollback_leaves_no_data(self, sandbox):
        smoke_all(sandbox, schemas=[sandbox.schema])
        n1 = sandbox.scalar(f'SELECT count(*) FROM "{sandbox.schema}".t1')
        n2 = sandbox.scalar(f'SELECT count(*) FROM "{sandbox.schema}".t2')
        assert n1 == 0 and n2 == 0, "冒烟 DML 必须 ROLLBACK，不留数据"

    def test_before_trigger_with_notnull_constraint_passes(self, sandbox):
        """BEFORE 触发器在约束检查前执行；若函数体不报错，就算 INSERT 被约束拒收，
        触发器本身应判 pass（不是 blocked）——这是 HANDOFF §1.1 关键场景。"""
        schema = f"smoke_test_{_rand_suffix()}"
        sb = PGSandbox(schema=schema)
        ok, err = sb.available()
        if not ok:
            pytest.skip(f"PG 不可用: {err}")
        sb.reset()
        ddl = """
        CREATE TABLE t3 (id integer, name text NOT NULL);
        CREATE FUNCTION trg_before_ok_fn() RETURNS trigger AS $$
        BEGIN NEW.id := 42; RETURN NEW; END $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_before_ok BEFORE INSERT ON t3
        FOR EACH ROW EXECUTE PROCEDURE trg_before_ok_fn();
        """
        for st in split_statements(ddl):
            st = st.strip()
            if st:
                ok2, err2, _ = sb.execute(st)
                assert ok2, err2
        try:
            report = smoke_all(sb, schemas=[schema])
            r = next(x for x in report.results
                     if x.kind == "TRIGGER" and x.name == "trg_before_ok")
            assert r.status == "pass", (
                f"BEFORE 触发器被约束拒收后应判 pass（函数体已成功执行），"
                f"实际 {r.status}: {r.error[:120]}")
        finally:
            sb.drop()
            sb.close()

    def test_report_ok_false_when_any_fail(self, sandbox):
        report = smoke_all(sandbox, schemas=[sandbox.schema])
        assert not report.ok
        assert report.total >= 8
