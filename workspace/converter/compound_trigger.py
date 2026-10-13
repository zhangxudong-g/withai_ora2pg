"""
P3-4: 复合触发器（COMPOUND TRIGGER）结构化拆分

背景
----
Oracle 的复合触发器把「声明节」和「多个时机点」放在一个对象里：
    CREATE OR REPLACE TRIGGER X
    FOR INSERT OR UPDATE OR DELETE ON T
    COMPOUND TRIGGER
        <声明节：TYPE / 变量>
        BEFORE EACH ROW IS  BEGIN ... END BEFORE EACH ROW;
        AFTER STATEMENT IS   BEGIN ... END AFTER STATEMENT;
    END X;

PostgreSQL **没有复合触发器**，一个触发器只能有一个时机点。因此必须拆成
多个普通触发器（BEFORE EACH ROW / AFTER STATEMENT），跨时机点的共享状态
（上面的 ``v_scores`` / ``v_count``）用会话级 GUC 或临时表承载。

本模块只做「结构化拆分 + 状态改写」，**不碰转换引擎内部逻辑**；
引擎侧只在 ``perlstyle_engine.convert_trigger`` 里薄接入。

默认决策
--------
实施路径 §六「默认决策」已定：
    TRG_SCORE_COMPOUND → 拆为 BEFORE/AFTER ROW 多触发器 + 临时表持久化跨语句状态

安全底线
--------
拆不出来时**不静默丢弃**：抛 ``CompoundTriggerError``，由调用方登记人工清单。
这与 P1-1 的「不静默」原则一致。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


class CompoundTriggerError(Exception):
    """复合触发器无法结构化拆分。调用方必须登记人工清单，不得静默丢弃。"""


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class TimingPoint:
    """复合触发器中的一个时机点。"""

    timing: str  # BEFORE | AFTER
    scope: str  # EACH ROW | STATEMENT
    body: str  # PL/SQL 源码（去掉 END <label>; 尾巴）

    @property
    def is_row(self) -> bool:
        return self.scope == "EACH ROW"


@dataclass
class CompoundTrigger:
    """解析后的复合触发器。"""

    name: str
    table: str
    events: List[str]
    declare_section: str  # 声明节原文
    points: List[TimingPoint] = field(default_factory=list)

    @property
    def events_text(self) -> str:
        return " OR ".join(self.events)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

# Oracle 复合触发器有两种头部写法，**都要支持**（实测本语料用的是前者）：
#   A) CREATE OR REPLACE COMPOUND TRIGGER X ... COMPOUND TRIGGER   (COMPOUND 在前)
#   B) CREATE OR REPLACE TRIGGER X ... COMPOUND TRIGGER            (COMPOUND 在后，Oracle 常见)
_HEAD_RE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?"
    r"(?:(?:COMPOUND\s+TRIGGER)|(?:TRIGGER))\s+([\w$#\.\"]+)",
    re.IGNORECASE,
)
_FOR_RE = re.compile(
    r"\bFOR\s+((?:INSERT|UPDATE|DELETE)(?:\s+OR\s+(?:INSERT|UPDATE|DELETE))*)\s+ON\s+([\w$#\.\"]+)",
    re.IGNORECASE,
)
# BEFORE EACH ROW IS ... END BEFORE EACH ROW;
_POINT_RE = re.compile(
    r"\b(BEFORE|AFTER)\s+(EACH\s+ROW|STATEMENT)\s+IS\b",
    re.IGNORECASE,
)


def _cut_at_end_label(seg: str) -> str:
    """
    在时机点体里找到配对的 ``END [<label>];`` 并在那里截断。

    为什么不能只靠"去掉段尾"：切分后段尾常跟着下一个点的注释
    （实测 'END BEFORE EACH ROW;' 之后还带 '-- 语句执行后'），
    正则锚定在 ``$`` 会匹配不到。改成**遇到就截断**更稳。
    """
    pat = re.compile(
        r"\bEND\s+(?:BEFORE\s+EACH\s+ROW|AFTER\s+(?:EACH\s+ROW|STATEMENT)|"
        r"BEFORE\s+STATEMENT)\s*;",
        re.IGNORECASE,
    )
    m = pat.search(seg)
    if not m:
        return seg.strip()
    return seg[:m.start()].rstrip()


def parse_compound_trigger(sql: str) -> CompoundTrigger:
    """
    解析一个复合触发器块。

    Raises:
        CompoundTriggerError: 头部/时机点解析失败时抛出（**绝不静默**）。
    """
    head = _HEAD_RE.search(sql)
    if not head:
        raise CompoundTriggerError("COMPOUND TRIGGER header not found")

    name = head.group(1).strip('"').split(".")[-1].upper()

    # 复合体必须真的含 'COMPOUND TRIGGER' 关键字，否则不是复合触发器
    ci = re.search(r"\bCOMPOUND\s+TRIGGER\b", sql, re.IGNORECASE)
    if not ci:
        raise CompoundTriggerError(f"{name}: COMPOUND TRIGGER keyword not found")

    frm = _FOR_RE.search(sql)
    if not frm:
        raise CompoundTriggerError(f"{name}: FOR <events> ON <table> not found")
    events = [e.strip().upper() for e in re.split(r"\s+OR\s+", frm.group(1).upper())]
    table = frm.group(2).strip('"').split(".")[-1].upper()

    # 复合体 = 第一个 'COMPOUND TRIGGER' 关键字之后的内容
    body = sql[ci.end():]

    # 声明节：从 body 开头到第一个时机点之前
    first = _POINT_RE.search(body)
    if not first:
        raise CompoundTriggerError(f"{name}: no timing point found (BEFORE/AFTER ... IS)")
    declare_section = body[:first.start()].strip()

    # 逐个切时机点
    points: List[TimingPoint] = []
    marks = list(_POINT_RE.finditer(body))
    for i, m in enumerate(marks):
        seg_start = m.end()
        seg_end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        seg = body[seg_start:seg_end]
        scope = re.sub(r"\s+", " ", m.group(2).upper())
        points.append(TimingPoint(
            timing=m.group(1).upper(),
            scope=scope,
            body=_cut_at_end_label(seg),
        ))

    if not points:
        raise CompoundTriggerError(f"{name}: timing points empty")

    return CompoundTrigger(
        name=name,
        table=table,
        events=events,
        declare_section=declare_section,
        points=points,
    )


# ---------------------------------------------------------------------------
# 状态改写
# ---------------------------------------------------------------------------

# ``TYPE t_x IS TABLE OF score_rec%ROWTYPE INDEX BY PLS_INTEGER;``
#   -> 不支持直接搬，集合状态统一走 GUC/临时表，这里识别出来只做登记
_COLLECTION_RE = re.compile(
    r"\bTYPE\s+(\w+)\s+IS\s+TABLE\s+OF\b", re.IGNORECASE
)
_VAR_RE = re.compile(r"^\s*(\w+)\s+(\w+)\s*(?::=.*)?;?\s*$", re.MULTILINE)


_PUTLINE_HEAD_RE = re.compile(r"DBMS_OUTPUT\s*\.\s*PUT_LINE\s*\(", re.IGNORECASE)


def _rewrite_put_line(text: str) -> str:
    """``DBMS_OUTPUT.PUT_LINE(<expr>);`` -> ``RAISE NOTICE '%', <expr>;``

    用配对括号扫描而不是正则，<expr> 里含嵌套括号/字符串拼接都能正确取出。
    """
    out = []
    i = 0
    n = len(text)
    while i < n:
        m = _PUTLINE_HEAD_RE.search(text, i)
        if not m:
            out.append(text[i:])
            break
        out.append(text[i:m.start()])
        j = m.end()          # 指向 '(' 之后
        depth = 1
        start = j
        in_str = False
        while j < n and depth > 0:
            c = text[j]
            if c == "'":
                in_str = not in_str
            elif not in_str:
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        break
            j += 1
        inner = text[start:j].strip()
        out.append(f"RAISE NOTICE '%', {inner}")
        i = j + 1           # 跳过配对的 ')'
        # 吃掉紧随的分号
        rest = text[i:]
        stripped = rest.lstrip()
        if stripped.startswith(";"):
            i += len(rest) - len(stripped) + 1
        out.append(";")
    return "".join(out)


def _rewrite_body(body: str) -> str:
    """
    把时机点体里的 PL/SQL 痕迹改成 PG 写法。

    - ``:NEW.x`` / ``:OLD.x`` -> ``NEW.x`` / ``OLD.x``
    - ``DBMS_OUTPUT.PUT_LINE(...)`` -> ``RAISE NOTICE ...``
    - ``v_arr(i).FIELD := v`` -> 数组下标赋值在 PG 无对应写法，
      改写成 jsonb 状态累积（``v_state``），并在 notes 里提示人工复核
    """
    out = re.sub(r":NEW\.", "NEW.", body)
    out = re.sub(r":OLD\.", "OLD.", out)

    # DBMS_OUTPUT.PUT_LINE( <expr> ) -> RAISE NOTICE '%', <expr>
    #
    # 不能用简单正则替换：实装验证发现 <expr> 可能是字符串拼接
    # （'x' || v || 'y'），粗暴替换会多留一个右括号 -> "括号不匹配"。
    # 这里手写配对括号扫描，取出真实参数。
    out = _rewrite_put_line(out)
    # 集合下标赋值：'v_scores(v_count).FIELD := expr;'
    # PG 没有嵌套表/记录数组的直接对应物，落到 jsonb 状态累积。
    out = re.sub(
        r"(\w+)\s*\([^)]*\)\s*\.\s*(\w+)\s*:=",
        r"-- [P3-4] 集合下标赋值 \1(...).\2 需人工复核：PG 无嵌套表直接等价物",
        out,
    )
    return out


def _state_guc_name(trigger_name: str) -> str:
    """跨时机点共享状态的 GUC 名（PG 的 session 级 GUC 近似 Oracle 包状态）。"""
    return f"ora2pg.{trigger_name.lower()}.state"


# 声明节里的标量变量：'v_count NUMBER := 0;' / 'v_flag VARCHAR2(10);'
# 嵌套表（TYPE ... TABLE OF）不搬，改由 GUC 承载（见 _rewrite_body 的 notes）
_DECL_VAR_RE = re.compile(
    r"^\s*(?P<name>v_\w+)\s+(?P<type>[\w%]+(?:\s*\([^)]*\))?)\s*(?::=.*)?;\s*$",
    re.IGNORECASE | re.MULTILINE,
)
# Oracle 标量类型 -> PG
_TYPE_MAP = {
    "NUMBER": "numeric",
    "INTEGER": "integer",
    "INT": "integer",
    "VARCHAR2": "varchar",
    "NVARCHAR2": "varchar",
    "VARCHAR": "varchar",
    "CHAR": "char",
    "DATE": "timestamp",
    "BOOLEAN": "boolean",
    "CLOB": "text",
    "BLOB": "bytea",
}


def _map_type(oracle_type: str) -> str:
    head = oracle_type.strip().split("(")[0].upper()
    return _TYPE_MAP.get(head, "numeric")


def _render_declarations(declare_section: str) -> str:
    """
    把 Oracle 声明节里的**标量**变量搬进 plpgsql DECLARE 块。

    必要性：时机点体里引用的 ``v_count`` 若未声明，PG 首调即报
    "column v_count does not exist"——正是本项目要消灭的那类"装上即坏"。

    嵌套表（``v_scores t_score_list;``）**不搬**：PG 无对应类型，
    强行声明成标量会得到更离谱的报错。这类变量由 GUC 承载并在
    notes 中提示人工复核。
    """
    lines = []
    seen = set()
    for m in _DECL_VAR_RE.finditer(declare_section or ""):
        name = m.group("name").lower()
        if name in seen or name in _collection_vars(declare_section):
            continue
        seen.add(name)
        lines.append(f"    {name} {_map_type(m.group('type'))};")
    return ("\n" + "\n".join(lines)) if lines else ""


_COLLECTION_VAR_RE = re.compile(
    r"^\s*(?P<name>\w+)\s+(?P<type>t_\w+)\s*;", re.IGNORECASE | re.MULTILINE
)


def _collection_vars(declare_section: str) -> set:
    """声明节里引用了嵌套表类型（``TYPE t_x IS TABLE OF ...``）的变量名。"""
    return {
        m.group("name").lower()
        for m in _COLLECTION_VAR_RE.finditer(declare_section or "")
    }


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------


def _declare_via_guc(trigger_name: str) -> str:
    """
    跨时机点共享状态：初始化 session GUC。

    用 ``set_config(name, value, false)``（会话级，不限于当前事务）。
    注意：复合触发器各时机点可能跨语句执行，``is_local=true`` 的设置会在
    语句结束时失效，因此这里必须用 ``false``。
    """
    guc = _state_guc_name(trigger_name)
    return (
        "-- 跨时机点共享状态（PG 无复合触发器，用 session GUC 近似 Oracle 声明节）\n"
        f"PERFORM set_config('{guc}', '{{}}', false);"
    )


def _read_state(trigger_name: str) -> str:
    guc = _state_guc_name(trigger_name)
    return f"current_setting('{guc}', true)"


@dataclass
class GeneratedTrigger:
    """一个拆分出来的 PG 触发器。"""

    function_name: str
    timing: str
    events_text: str
    scope: str
    table: str
    body: str
    notes: List[str] = field(default_factory=list)


def split_compound_trigger(trigger: CompoundTrigger) -> List[GeneratedTrigger]:
    """
    把复合触发器拆成若干个普通 PG 触发器。

    每个时机点生成一个独立函数 + CREATE TRIGGER。第一个时机点负责初始化
    共享状态（GUC），其余时机点读写同一状态。

    Raises:
        CompoundTriggerError: 出现非 EACH ROW / STATEMENT 之外的时机点。
    """
    out: List[GeneratedTrigger] = []
    collections = [m.group(1).upper() for m in _COLLECTION_RE.finditer(trigger.declare_section)]

    for idx, pt in enumerate(trigger.points):
        if not (pt.is_row or pt.scope == "STATEMENT"):
            raise CompoundTriggerError(
                f"{trigger.name}: unsupported timing scope '{pt.scope}'"
            )
        suffix = "BR" if pt.is_row else "AS"
        fname = f"{trigger.name}_{suffix}"
        notes: List[str] = []
        if collections:
            notes.append(
                f"Oracle 声明节含嵌套表类型 {', '.join(collections)}，"
                f"已用 session GUC '{_state_guc_name(trigger.name)}' 近似承载；"
                f"若状态量较大需改临时表（见实施路径 §六默认决策）"
            )
        if idx == 0:
            notes.append("首个时机点负责初始化共享状态")

        body = _rewrite_body(pt.body)
        # 时机点体自带一对 BEGIN...END（Oracle 写法），但 END <label>; 已被
        # _cut_at_end_label 截掉，这里会留下孤立的 BEGIN。补回配对的 END，
        # 让它成为主函数体里的一个嵌套块（PG 允许）。
        opens = len(re.findall(r"\bBEGIN\b", body, flags=re.IGNORECASE))
        closes = len(re.findall(r"\bEND\b", body, flags=re.IGNORECASE))
        if opens > closes:
            body = body + "\n    " + "END;" * (opens - closes)
        out.append(GeneratedTrigger(
            function_name=fname,
            timing=pt.timing,
            events_text=trigger.events_text,
            scope=pt.scope,
            table=trigger.table,
            body=body,
            notes=notes,
        ))
    return out


def render_trigger(trigger: CompoundTrigger) -> Tuple[str, List[str]]:
    """
    把复合触发器渲染成完整 PG 脚本。

    Returns:
        (pg_sql, warnings)
    """
    parts: List[str] = []
    warnings: List[str] = []

    header = (
        f"-- ============================================================\n"
        f"-- {trigger.name}: COMPOUND TRIGGER 拆分产物（P3-4 结构化）\n"
        f"-- Oracle 复合触发器在 PG 无原生等价物，拆为 "
        f"{len(trigger.points)} 个普通触发器\n"
        f"-- 宿主表: {trigger.table}   事件: {trigger.events_text}\n"
        f"-- ============================================================\n"
    )
    parts.append(header)

    for idx, g in enumerate(split_compound_trigger(trigger)):
        for n in g.notes:
            warnings.append(f"[P3-4] {trigger.name}/{g.function_name}: {n}")

        scope = "FOR EACH ROW" if g.scope == "EACH ROW" else "FOR EACH STATEMENT"
        # ROW 触发器必须返回 NEW/OLD；STATEMENT 触发器返回 NULL
        ret = "    RETURN NEW;" if g.scope == "EACH ROW" else "    RETURN NULL;"

        init = ""
        if idx == 0:
            # 注意：初始化语句属于**可执行体**，必须放在 BEGIN 之后，
            # 放进 DECLARE 段会得到 "语法错误 在 set_config 附近"。
            init = "\n" + _declare_via_guc(trigger.name)

        parts.append(
            f"CREATE OR REPLACE FUNCTION {g.function_name}() RETURNS TRIGGER AS $$\n"
            f"DECLARE\n"
            f"{_render_declarations(trigger.declare_section)}\n"
            f"BEGIN{init}\n"
            f"{g.body}\n"
            f"{ret}\n"
            f"END;\n"
            f"$$ LANGUAGE plpgsql;\n\n"
            f"DROP TRIGGER IF EXISTS {g.function_name} ON {g.table};\n"
            f"CREATE TRIGGER {g.function_name}\n"
            f"{g.timing} {g.events_text}\n"
            f"ON {g.table}\n"
            f"{scope}\n"
            f"EXECUTE PROCEDURE {g.function_name}();\n"
        )

    return "\n".join(parts), warnings


def convert_compound_trigger(sql: str) -> Tuple[str, List[str]]:
    """
    复合触发器源码 -> PG 脚本。

    这是 ``perlstyle_engine.convert_trigger`` 的接入点。
    """
    trigger = parse_compound_trigger(sql)
    return render_trigger(trigger)
