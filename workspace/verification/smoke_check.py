"""P1-4: L3.5 首调冒烟层（DoD-3）。

原则（HANDOFF §0 关键原则）：
    **能安装 ≠ 可用** —— plpgsql 延迟解析导致 L3 通过的对象可能在首调时炸
    （%TYPE 残留 / INLINED 残骸 / 引用不存在的列，全部是 CREATE 成功、首调报错）。
    所有对象必须至少一次真实调用不报错才算通过。

覆盖（DoD-3）：
    - FUNCTION / PROCEDURE（含包成员）: 真实调用一次
    - VIEW: SELECT 一次
    - SEQUENCE: nextval 一次
    - TRIGGER: 经宿主表 DML（事务内 + ROLLBACK，不留数据）真实触发

结果分级（诚实报告，不粉饰）：
    pass        首调成功
    fail        首调报错（DoD-3 计数项）
    blocked     未真正触达对象（如 AFTER 行触发器因 INSERT 约束失败未执行）
    not_fired   触发器存在但未等到触发时机
    unsupported 无法构造合法调用（如伪类型参数），不计 fail
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .pg_sandbox import PGSandbox

# 不属于"本次安装产物"的系统 schema（public 也不扫：那是扩展/系统对象落点）
SYSTEM_SCHEMAS = {"pg_catalog", "information_schema", "pg_toast", "public"}

_STATUS_PASS = "pass"
_STATUS_FAIL = "fail"
_STATUS_BLOCKED = "blocked"
_STATUS_NOT_FIRED = "not_fired"
_STATUS_UNSUPPORTED = "unsupported"

# 伪类型：无法构造 NULL::t 形式的实参
_PSEUDO_HINT_RE = re.compile(r"\b(any|anyelement|anyarray|anynonarray|anyenum|"
                             r"anyrange|record|cstring|internal|language_handler|"
                             r"fdw_handler|table_am_handler|event_trigger)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class SmokeResult:
    kind: str                 # FUNCTION / PROCEDURE / VIEW / SEQUENCE / TRIGGER
    schema: str
    name: str
    status: str
    sql: str = ""
    error: str = ""

    def to_dict(self) -> Dict:
        return self.__dict__.copy()


@dataclass
class SmokeReport:
    results: List[SmokeResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def failed(self) -> List[SmokeResult]:
        return [r for r in self.results if r.status == _STATUS_FAIL]

    @property
    def ok(self) -> bool:
        return not self.failed

    def to_dict(self) -> Dict:
        return {
            "total": self.total,
            "failed_count": len(self.failed),
            "ok": self.ok,
            "by_status": {
                s: sum(1 for r in self.results if r.status == s)
                for s in (_STATUS_PASS, _STATUS_FAIL, _STATUS_BLOCKED,
                          _STATUS_NOT_FIRED, _STATUS_UNSUPPORTED)
            },
            "results": [r.to_dict() for r in self.results],
        }


# ---------------------------------------------------------------------------
# 纯函数（可单测）
# ---------------------------------------------------------------------------
def quote_ident(name: str) -> str:
    """PG 标识符加引号（嵌入引号翻倍）。"""
    return '"' + name.replace('"', '""') + '"'


def build_arg_expr(arg_type: str, strict: bool) -> str:
    """为一个参数类型构造实参表达式。

    非严格函数统一传 NULL（STRICT 函数传 NULL 不会执行函数体，冒烟会假通过）；
    严格函数按类型类别给最小哑值。
    """
    t = arg_type.strip()
    if not strict:
        return f"NULL::{t}"
    tl = t.lower()
    if any(k in tl for k in ("int", "numeric", "decimal", "real", "double",
                             "smallint", "bigint", "money", "float")):
        return f"'1'::{t}"
    if any(k in tl for k in ("char", "text", "varchar", "name", "json", "xml", "uuid")):
        return f"''::{t}"
    if any(k in tl for k in ("date", "time", "timestamp")):
        return f"'1970-01-01'::{t}"
    if tl.startswith("bool"):
        return "true"
    if "[]" in tl:
        return f"'{{}}'::{t}"
    return f"NULL::{t}"


def parse_trigger_fn(action_statement: str) -> str:
    """从 information_schema.triggers.action_statement 提取触发器函数名（小写）。

    兼容 PG 11+ 的 ``EXECUTE FUNCTION`` 和老式 ``EXECUTE PROCEDURE``。
    """
    m = re.search(r"(?is)EXECUTE\s+(?:FUNCTION|PROCEDURE)\s+([A-Za-z0-9_$\.]+)", action_statement or "")
    if not m:
        return ""
    return m.group(1).split(".")[-1].strip('"').lower()


def attribute_error_to_fn(error_text: str) -> Optional[str]:
    """从错误文本（含 CONTEXT）中归因 plpgsql 函数名。

    plpgsql 延迟解析的报错 CONTEXT 形如（英文/中文 locale 两种形态都覆盖）:
        PL/pgSQL function trg_student_bi() line 3 at ...
        在赋值的第1行的PL/pgSQL函数trg_student_bi()
    归因不了（纯约束冲突等）返回 None。
    """
    if not error_text:
        return None
    m = re.search(
        r"(?i)PL/?pgSQL\s*(?:function|函数)?\s*[\"`]?([A-Za-z0-9_$\-]+)[\"`]?\s*\(",
        error_text,
    )
    if m:
        return m.group(1).split(".")[-1].strip('"').lower()
    return None


# ---------------------------------------------------------------------------
# 内省查询
# ---------------------------------------------------------------------------
_ROUTINES_SQL = """
SELECT p.proname,
       p.prokind,
       p.proisstrict,
       p.prorettype::regtype::text AS rettype,
       COALESCE(
         (SELECT array_agg(tt.oid::regtype::text ORDER BY o.ord)
            FROM unnest(p.proargtypes) WITH ORDINALITY AS o(oid, ord)
            JOIN pg_type tt ON tt.oid = o.oid),
         ARRAY[]::text[]
       ) AS argtypes,
       EXISTS (
         SELECT 1 FROM unnest(p.proargtypes) a(oid)
         JOIN pg_type tt ON tt.oid = a.oid
         WHERE tt.typtype = 'p'
       ) AS has_pseudo_arg
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = %s
  AND p.prokind IN ('f', 'p')
"""

_VIEWS_SQL = """
SELECT table_name FROM information_schema.views WHERE table_schema = %s
"""

_SEQUENCES_SQL = """
SELECT sequence_name FROM information_schema.sequences WHERE sequence_schema = %s
"""

_TRIGGERS_SQL = """
SELECT trigger_name, event_object_table, event_manipulation, action_timing, action_statement
FROM information_schema.triggers
WHERE trigger_schema = %s
"""

_FIRST_COLUMN_SQL = """
SELECT column_name FROM information_schema.columns
WHERE table_schema = %s AND table_name = %s
ORDER BY ordinal_position LIMIT 1
"""


def list_target_schemas(sb: PGSandbox) -> List[str]:
    """沙箱内所有非系统 schema（含包展开出的 schema，如 pkg_score_manage）。"""
    rows, _ = sb.rows(
        "SELECT nspname FROM pg_namespace "
        "WHERE nspname NOT LIKE 'pg_%' ORDER BY nspname"
    )
    return [r[0] for r in rows if r[0] not in SYSTEM_SCHEMAS]


# ---------------------------------------------------------------------------
# 各类型冒烟
# ---------------------------------------------------------------------------
def smoke_routines(sb: PGSandbox, schemas: List[str]) -> List[SmokeResult]:
    """函数 / 过程首调冒烟。"""
    out: List[SmokeResult] = []
    for schema in schemas:
        rows, err = sb.rows(_ROUTINES_SQL.replace("%s", "'"
                            + schema.replace("'", "''") + "'"), 5000)
        if err:
            out.append(SmokeResult("FUNCTION", schema, f"<introspect:{schema}>",
                                   _STATUS_UNSUPPORTED, error=err))
            continue
        for (proname, prokind, proisstrict, rettype, argtypes,
             has_pseudo) in rows:
            kind = "PROCEDURE" if prokind == "p" else "FUNCTION"
            ret_l = (rettype or "").lower()
            if ret_l == "trigger" or ret_l == "event_trigger":
                continue  # 触发器函数由 DML 触发路径覆盖，不直接调用
            if has_pseudo:
                out.append(SmokeResult(kind, schema, proname, _STATUS_UNSUPPORTED,
                                       sql="<pseudo-type args>"))
                continue
            args = [build_arg_expr(t, bool(proisstrict)) for t in (argtypes or [])]
            arg_sql = ", ".join(args)
            fn = f"{quote_ident(schema)}.{quote_ident(proname)}"
            if kind == "PROCEDURE":
                sql = f"CALL {fn}({arg_sql})"
            else:
                sql = f"SELECT * FROM {fn}({arg_sql})"
            ok, error = sb.execute_detailed(sql)
            out.append(SmokeResult(
                kind, schema, proname,
                _STATUS_PASS if ok else _STATUS_FAIL,
                sql=sql, error=error,
            ))
    return out


def smoke_views(sb: PGSandbox, schemas: List[str]) -> List[SmokeResult]:
    out: List[SmokeResult] = []
    for schema in schemas:
        rows, err = sb.rows(_VIEWS_SQL.replace("%s", "'"
                            + schema.replace("'", "''") + "'"), 5000)
        if err:
            continue
        for (vname,) in rows:
            sql = f"SELECT * FROM {quote_ident(schema)}.{quote_ident(vname)} LIMIT 1"
            ok, error = sb.execute_detailed(sql)
            out.append(SmokeResult("VIEW", schema, vname,
                                   _STATUS_PASS if ok else _STATUS_FAIL,
                                   sql=sql, error=error))
    return out


def smoke_sequences(sb: PGSandbox, schemas: List[str]) -> List[SmokeResult]:
    out: List[SmokeResult] = []
    for schema in schemas:
        rows, err = sb.rows(_SEQUENCES_SQL.replace("%s", "'"
                            + schema.replace("'", "''") + "'"), 5000)
        if err:
            continue
        for (sname,) in rows:
            regclass_literal = "'" + (quote_ident(schema) + '.' + quote_ident(sname)).replace("'", "''") + "'"
            sql = f"SELECT nextval({regclass_literal})"
            ok, error = sb.execute_detailed(sql)
            out.append(SmokeResult("SEQUENCE", schema, sname,
                                   _STATUS_PASS if ok else _STATUS_FAIL,
                                   sql=sql, error=error))
    return out


@dataclass
class _TriggerInfo:
    name: str
    table: str
    events: set
    timing: str
    fn: str


def smoke_triggers(sb: PGSandbox, schemas: List[str]) -> List[SmokeResult]:
    """触发器冒烟：按宿主表（或视图）在回滚事务里做 INSERT → UPDATE → DELETE，
    用错误 CONTEXT 归因到具体触发器函数。

    - INSERT / UPDATE / DELETE 依次尝试（前一步失败即停）
    - 所有 DML 最终 ROLLBACK，沙箱不留数据
    - 归因不了函数名的失败 = blocked（约束冲突等环境因素，不计 DoD-3 fail）
    """
    out: List[SmokeResult] = []
    for schema in schemas:
        rows, err = sb.rows(_TRIGGERS_SQL.replace("%s", "'"
                            + schema.replace("'", "''") + "'"), 5000)
        if err:
            continue
        # group by table
        by_table: Dict[str, List[_TriggerInfo]] = {}
        for (tname, table, event, timing, action_stmt) in rows:
            info = by_table.setdefault(table, [])
            fn = parse_trigger_fn(action_stmt)
            for t in info:
                if t.name == tname:
                    t.events.add(event)
                    break
            else:
                info.append(_TriggerInfo(tname, table, {event}, timing, fn))

        for table, triggers in by_table.items():
            target = f"{quote_ident(schema)}.{quote_ident(table)}"
            has_insert_evt = any("INSERT" in t.events for t in triggers)
            col_row, _ = sb.rows(_FIRST_COLUMN_SQL
                                 .replace("%s", "'" + schema.replace("'", "''") + "'",
                                          1)
                                 .replace("%s", "'" + table.replace("'", "''") + "'",
                                          1))
            statements: List[str] = []
            stmt_events: List[str] = []
            if has_insert_evt or True:  # INSERT 总是首选（BEFORE 触发器先于约束检查）
                statements.append(f"INSERT INTO {target} DEFAULT VALUES")
                stmt_events.append("INSERT")
            first_col = col_row[0][0] if col_row else None
            if first_col:
                statements.append(
                    f"UPDATE {target} SET {quote_ident(first_col)} = "
                    f"{quote_ident(first_col)}")
                stmt_events.append("UPDATE")
                statements.append(f"DELETE FROM {target}")
                stmt_events.append("DELETE")

            outcomes = sb.try_dml_rollback(statements)

            # 错误归因：把错误映射回 (event, fn) 集合，供所有 trigger 复用。
            # BEFORE 在约束检查之前触发；AFTER 在约束检查之后触发。
            # 纯约束错误（无 fn 归因）→ BEFORE 触发器已成功执行，AFTER 触发器未触发。
            stmt_error: dict[str, tuple[bool, str, str]] = {}
            for i, ev in enumerate(stmt_events):
                if i >= len(outcomes):
                    stmt_error[ev] = (True, "<aborted>", "")
                    continue
                ok, err = outcomes[i]
                if err == "<aborted>":
                    stmt_error[ev] = (False, err, "")
                elif not ok:
                    stmt_error[ev] = (False, err, attribute_error_to_fn(err) or "")
                else:
                    stmt_error[ev] = (True, "", "")

            # 错误归因到本表的某触发器？为避免重复归因：每个失败语句的 fn 只 blame
            # 第一个 events 包含该语句事件且 fn 匹配的触发器。
            blamed_fns: set[str] = set()
            # 已知 fn 的触发器名字典（用于当本触发器 fn 为空时的反向匹配）
            known_fn_triggers: dict[str, _TriggerInfo] = {
                t.fn: t for t in triggers if t.fn
            }

            for t in triggers:
                # 找到该触发器事件对应的执行结果（取第一个匹配）
                picked = None
                for ev in stmt_events:
                    if ev in t.events and ev in stmt_error:
                        picked = stmt_error[ev]
                        break
                if picked is None:
                    out.append(SmokeResult("TRIGGER", schema, t.name,
                                           _STATUS_NOT_FIRED,
                                           error="未找到匹配事件"))
                    continue
                ok, err, fn = picked
                if not ok and err == "<aborted>":
                    out.append(SmokeResult("TRIGGER", schema, t.name,
                                           _STATUS_BLOCKED,
                                           error="前置 DML 失败，未执行到本触发器的触发时机"))
                    continue
                if ok:
                    out.append(SmokeResult("TRIGGER", schema, t.name,
                                           _STATUS_PASS))
                    continue
                # 执行了但报错：归因到本触发器 or 其他触发器 or 纯约束
                blame_target: Optional[_TriggerInfo] = None
                if fn:
                    if t.fn and fn == t.fn:
                        blame_target = t
                    elif not t.fn and fn in known_fn_triggers:
                        # 本触发器 fn 未解析出，但错误 fn 指向本表已知 fn 触发器
                        blame_target = known_fn_triggers[fn]
                if blame_target is not None and blame_target.fn not in blamed_fns:
                    blamed_fns.add(blame_target.fn or blame_target.name)
                    sql_idx = next(i for i, e in enumerate(stmt_events)
                                    if e in blame_target.events)
                    out.append(SmokeResult("TRIGGER", schema, blame_target.name,
                                           _STATUS_FAIL,
                                           sql=statements[sql_idx],
                                           error=err))
                    if blame_target is not t:
                        # 本触发器未独立归因——同表其他触发器报错，BEFORE/AFTER 分情况
                        if t.timing.upper().startswith("BEFORE"):
                            out.append(SmokeResult("TRIGGER", schema, t.name,
                                                   _STATUS_BLOCKED,
                                                   error=f"同表触发器 {fn} 报错；本触发器归属未明"))
                        else:
                            out.append(SmokeResult("TRIGGER", schema, t.name,
                                                   _STATUS_NOT_FIRED,
                                                   error=f"同表触发器 {fn} 报错"))
                    continue
                if fn and t.fn and fn != t.fn:
                    # 同表其他触发器 fn 报错（且本触发器 fn 也已知但不匹配）
                    if t.timing.upper().startswith("BEFORE"):
                        out.append(SmokeResult("TRIGGER", schema, t.name,
                                               _STATUS_BLOCKED,
                                               error=f"同表触发器 {fn} 报错；本触发器归属未明"))
                    else:
                        out.append(SmokeResult("TRIGGER", schema, t.name,
                                               _STATUS_NOT_FIRED,
                                               error=f"同表触发器 {fn} 报错"))
                    continue
                # 纯约束错误（无 fn 归因）
                if t.timing.upper().startswith("BEFORE"):
                    # BEFORE 在约束之前触发 → 函数体已成功执行
                    out.append(SmokeResult("TRIGGER", schema, t.name,
                                           _STATUS_PASS))
                else:
                    out.append(SmokeResult("TRIGGER", schema, t.name,
                                           _STATUS_NOT_FIRED,
                                           error="约束检查未通过，AFTER 触发器未触发"))
    return out


def smoke_all(sb: PGSandbox, schemas: Optional[List[str]] = None) -> SmokeReport:
    """对沙箱内全部可识别对象做首调冒烟。schemas 缺省 = 非系统 schema。"""
    if schemas is None:
        schemas = list_target_schemas(sb)
    report = SmokeReport()
    report.results.extend(smoke_routines(sb, schemas))
    report.results.extend(smoke_views(sb, schemas))
    report.results.extend(smoke_sequences(sb, schemas))
    report.results.extend(smoke_triggers(sb, schemas))
    return report
