"""
P3-3: 元数据提取（AST 版）

设计
----
- 走 P3-1 的 `parse_plsql()` 拿语法树，按 context 节点取值，**不靠正则猜**
- 覆盖正则版漏掉的场景（实测：`TRG_SCORE_COMPOUND` 复合触发器）
- AST 解析失败时**降级到正则兜底**并在结果里标记 `degraded=True`，
  绝不因为解析失败就返回空（那会重演"静默丢失"的老问题）

对象范围
--------
- 触发器：名字 / 时机 / 事件 / 宿主表 / 是否复合 / 语句级
- 类型：名字 / 列(名,类型) / 集合类型 / 对象类型方法
- 函数与过程：名字 / 参数 / 返回类型

落点: workspace/parsers/metadata.py

实测基线（2026-10-09）
--------------------
| 语料 | AST 识别 | 正确率 |
|---|---|---|
| TRIGGER/00_TRIGGER.sql | 6/6 unit | 6/6 字段全对（含 COMPOUND）|
| TYPE/001_TYPES.sql | 2/11 unit | 解析在 L31 连锁中断 → 降级正则 |
| FUNCTION/00_FUNCTION.sql | 0 语法错 | 可用 |
| PACKAGE_BODY/00_PACKAGE_BODY.sql | 8 语法错 | 降级正则（嵌套缺 DECLARE）|
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .grammar_adapter import ParseResult, parse_plsql


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class TriggerMeta:
    """触发器元数据。"""

    name: str
    timing: str  # BEFORE / AFTER / INSTEAD OF / COMPOUND / ""（未知）
    events: List[str]  # ['INSERT', 'UPDATE', 'DELETE']
    table: str
    is_compound: bool = False
    statement_level: bool = False
    source: str = "ast"  # ast | regex（降级来源，便于排查）

    @property
    def events_text(self) -> str:
        return ", ".join(self.events)


@dataclass
class TypeMeta:
    """类型元数据。"""

    name: str
    columns: List[Tuple[str, str]] = field(default_factory=list)
    is_collection: bool = False
    methods: List[str] = field(default_factory=list)
    source: str = "ast"

    @property
    def columns_text(self) -> str:
        return ", ".join(f"{n} {t}" for n, t in self.columns)


@dataclass
class RoutineMeta:
    """函数/过程元数据。"""

    name: str
    kind: str  # FUNCTION | PROCEDURE
    params: List[Tuple[str, str]] = field(default_factory=list)
    return_type: str = ""
    source: str = "ast"

    @property
    def params_text(self) -> str:
        return ", ".join(f"{t} {n}" for n, t in self.params)


@dataclass
class MetadataResult:
    """一次提取的完整结果（含降级标记）。"""

    triggers: List[TriggerMeta] = field(default_factory=list)
    types: List[TypeMeta] = field(default_factory=list)
    routines: List[RoutineMeta] = field(default_factory=list)
    parse_ok: bool = True
    degraded: bool = False
    parse_errors: List[str] = field(default_factory=list)

    def summary(self) -> Dict[str, Any]:
        return {
            "triggers": len(self.triggers),
            "types": len(self.types),
            "routines": len(self.routines),
            "parse_ok": self.parse_ok,
            "degraded": self.degraded,
            "error_count": len(self.parse_errors),
        }


# ---------------------------------------------------------------------------
# 语法树遍历工具
# ---------------------------------------------------------------------------

_MAX_DEPTH = 40


def _iter_nodes(node: Any, max_depth: int = _MAX_DEPTH, depth: int = 0):
    """深度优先遍历所有子节点。"""
    if node is None or depth > max_depth:
        return
    try:
        children = [node.getChild(i) for i in range(node.getChildCount())]
    except Exception:  # pragma: no cover - 防御性
        return
    for c in children:
        if c is None:
            continue
        yield c
        yield from _iter_nodes(c, max_depth, depth + 1)


def _find_first(node: Any, ctx_names: Tuple[str, ...], max_depth: int = _MAX_DEPTH):
    """按 context 类名（后缀匹配）深度优先找第一个命中节点。"""
    for n in _iter_nodes(node, max_depth):
        tname = type(n).__name__
        if any(tname.endswith(s) for s in ctx_names):
            return n
    return None


def _find_all(node: Any, ctx_names: Tuple[str, ...], max_depth: int = _MAX_DEPTH):
    """按 context 类名（后缀匹配）找出全部命中节点。"""
    out = []
    for n in _iter_nodes(node, max_depth):
        tname = type(n).__name__
        if any(tname.endswith(s) for s in ctx_names):
            out.append(n)
    return out


def _text(node: Any) -> str:
    try:
        return (node.getText() or "").strip()
    except Exception:  # pragma: no cover
        return ""


def _units(tree: Any) -> List[Any]:
    """取顶层 unit_statement 节点。"""
    if tree is None:
        return []
    return [
        c for c in tree.getChildren()
        if c is not None and type(c).__name__.endswith("Unit_statementContext")
    ]


# ---------------------------------------------------------------------------
# 触发器（AST）
# ---------------------------------------------------------------------------

_TIMING_TOKENS = ("BEFORE", "AFTER", "INSTEAD OF")


def _trigger_timing(dml_ctx: Any) -> str:
    """
    从 Simple_dml_triggerContext 的直接子节点里取时机。

    grammar 把时机放在 dml_ctx 的直接 TerminalNodeImpl 上，但
    - `BEFORE` / `AFTER` 是单个 token
    - `INSTEAD OF` 被拆成 **两个** token（`INSTEAD` + `OF`）

    所以这里按顺序扫直接子节点，遇 INSTEAD 就看下一个是不是 OF。
    """
    children = []
    try:
        children = [dml_ctx.getChild(i) for i in range(dml_ctx.getChildCount())]
    except Exception:  # pragma: no cover
        return ""
    for i, c in enumerate(children):
        up = _text(c).upper()
        if up == "INSTEAD":
            nxt = _text(children[i + 1]).upper() if i + 1 < len(children) else ""
            return "INSTEAD OF" if nxt == "OF" else "INSTEAD"
        if up in ("BEFORE", "AFTER"):
            return up
    return ""


def _trigger_events(dml_ctx: Any) -> List[str]:
    """取 Dml_event_clause 下的事件列表。"""
    events: List[str] = []
    for clause in _find_all(dml_ctx, ("Dml_event_clauseContext",), max_depth=2):
        for el in _find_all(clause, ("Dml_event_elementContext",), max_depth=2):
            t = _text(el).upper()
            if t in ("INSERT", "UPDATE", "DELETE") and t not in events:
                events.append(t)
    return events


def _trigger_table(dml_ctx: Any) -> str:
    """取 ON 后面的宿主表名。"""
    for clause in _find_all(dml_ctx, ("Dml_event_clauseContext",), max_depth=2):
        for tv in _find_all(clause, ("Tableview_nameContext",), max_depth=3):
            return _text(tv).upper()
    return ""


def _triggers_from_tree(tree: Any) -> List[TriggerMeta]:
    """从语法树提取触发器元数据。"""
    out: List[TriggerMeta] = []
    for unit in _units(tree):
        create = _find_first(unit, ("Create_triggerContext",), max_depth=2)
        if create is None:
            continue

        name_node = _find_first(create, ("Trigger_nameContext",), max_depth=3)
        name = _text(name_node).upper() if name_node else ""
        if not name:
            continue

        # 复合触发器：Compound_dml_triggerContext（无 BEFORE/AFTER 头）
        compound_ctx = _find_first(create, ("Compound_dml_triggerContext",), max_depth=2)
        simple_ctx = _find_first(create, ("Simple_dml_triggerContext",), max_depth=2)

        dml_ctx = compound_ctx if compound_ctx is not None else simple_ctx
        if dml_ctx is None:
            out.append(TriggerMeta(name=name, timing="", events=[], table="",
                                   is_compound=compound_ctx is not None))
            continue

        timing = "COMPOUND" if compound_ctx is not None else _trigger_timing(dml_ctx)
        statement_level = _find_first(dml_ctx, ("For_each_rowContext",), max_depth=3) is None

        out.append(TriggerMeta(
            name=name,
            timing=timing,
            events=_trigger_events(dml_ctx),
            table=_trigger_table(dml_ctx),
            is_compound=compound_ctx is not None,
            statement_level=statement_level,
        ))
    return out


# ---------------------------------------------------------------------------
# 触发器（正则兜底——AST 不可用时）
# ---------------------------------------------------------------------------

# 注意：复合触发器没有 BEFORE/AFTER 头，正则需单独分支
_TRIGGER_SIMPLE_RE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?TRIGGER\s+(?P<name>[\w\.\$]+)\s+"
    r"(?P<timing>BEFORE|AFTER|INSTEAD\s+OF)\s+"
    r"(?P<events>(?:INSERT|UPDATE|DELETE)(?:\s+OR\s+(?:INSERT|UPDATE|DELETE))*)\s+"
    r"ON\s+(?P<table>[\w\.\$]+)",
    re.IGNORECASE,
)

_TRIGGER_COMPOUND_RE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?COMPOUND\s+TRIGGER\s+(?P<name>[\w\.\$]+)\s+"
    r"FOR\s+(?P<events>(?:INSERT|UPDATE|DELETE)(?:\s+OR\s+(?:INSERT|UPDATE|DELETE))*)\s+"
    r"ON\s+(?P<table>[\w\.\$]+)",
    re.IGNORECASE,
)


def _triggers_by_regex(sql: str) -> List[TriggerMeta]:
    if not isinstance(sql, str) or not sql:
        return []
    out: List[TriggerMeta] = []
    seen = set()
    for m in _TRIGGER_COMPOUND_RE.finditer(sql):
        name = m.group("name").upper()
        seen.add(name)
        out.append(TriggerMeta(
            name=name,
            timing="COMPOUND",
            events=[e.strip() for e in re.split(r"\s+OR\s+", m.group("events").upper())],
            table=m.group("table").upper(),
            is_compound=True,
            source="regex",
        ))
    for m in _TRIGGER_SIMPLE_RE.finditer(sql):
        name = m.group("name").upper()
        if name in seen:
            continue
        seen.add(name)
        out.append(TriggerMeta(
            name=name,
            timing=m.group("timing").upper().replace("  ", " "),
            events=[e.strip() for e in re.split(r"\s+OR\s+", m.group("events").upper())],
            table=m.group("table").upper(),
            source="regex",
        ))
    return out


# ---------------------------------------------------------------------------
# 类型（AST）
# ---------------------------------------------------------------------------


def _types_from_tree(tree: Any) -> List[TypeMeta]:
    """
    从语法树提取类型元数据。

    节点结构（实测 ANTLR 4.13.2 community grammar）：
        Create_typeContext
        └── Type_definitionContext
            ├── Type_nameContext          -> 类型名
            └── Object_type_defContext
                ├── Object_as_partContext   (AS OBJECT)
                └── Object_member_specContext × N   (列 / MEMBER 声明)
                    ├── IdentifierContext    -> 列名
                    └── Type_specContext     -> 列类型
    """
    out: List[TypeMeta] = []
    for create in _find_all(tree, ("Create_typeContext",), max_depth=2):
        name_node = _find_first(create, ("Type_nameContext",), max_depth=3)
        name = _text(name_node).upper() if name_node else ""
        if not name:
            continue

        cols: List[Tuple[str, str]] = []
        methods: List[str] = []
        for spec in _find_all(create, ("Object_member_specContext",), max_depth=6):
            spec_text = _text(spec).upper()
            # ANTLR 的 getText() 会去掉所有空白：'MEMBER FUNCTION GET_ID' -> 'MEMBERFUNCTIONGET_ID'
            # 所以判断和提取都必须用"去空白"模式，不能用 \s+
            if spec_text.startswith("MEMBER") or spec_text.startswith("MAP"):
                m = re.search(
                    r"(?:MEMBER|MAP)(?:PROCEDURE|FUNCTION)([A-Z0-9_$#]+)", spec_text
                )
                if m:
                    methods.append(m.group(1).upper())
                continue
            ident = _find_first(spec, ("IdentifierContext",), max_depth=2)
            ident_txt = _text(ident).upper() if ident else ""
            tspec = _find_first(spec, ("Type_specContext",), max_depth=2)
            if ident_txt and tspec is not None:
                cols.append((ident_txt, _text(tspec).upper()))

        obj_def = _find_first(create, ("Object_type_defContext",), max_depth=3)
        is_collection = False
        if obj_def is not None:
            # AS TABLE OF xxx 形态
            is_collection = "TABLEOF" in re.sub(r"\s+", "", _text(obj_def).upper())

        out.append(TypeMeta(
            name=name,
            columns=cols,
            is_collection=is_collection,
            methods=methods,
        ))
    return out


# ---------------------------------------------------------------------------
# 类型（正则兜底）
# ---------------------------------------------------------------------------

# 匹配 CREATE TYPE name AS OBJECT ( ... )
# 注意：源里是 'AS OBJECT ('，不是 'AS ('——写死 AS( 会漏掉所有对象类型
_TYPE_RE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?TYPE\s+(?P<name>\w+)\s+"
    r"AS\s+(?:OBJECT\s+)?\(",
    re.IGNORECASE,
)


def _balanced_body(sql: str, start: int) -> str:
    """
    从 start（= 正则 'AS (' 匹配结束处，已消费掉开括号）开始，
    用平衡括号取到配对的 ')' 为止的 body 文本。

    depth 必须从 1 起算：调用方已消费 'AS (' 的开括号，当前在第 1 层内部。
    用 0 会让 'VARCHAR2(1000)' 的右括号被误判成闭合点
    （实测：只切出 1 列，7 列的类型只认出 CNT）。
    """
    depth, i, n = 1, start, len(sql)
    while i < n:
        c = sql[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return sql[start:i]
        i += 1
    return sql[start:]  # 未闭合，返回剩余全部


def _split_top_commas(body: str) -> List[str]:
    depth, cur, parts = 0, [], []
    for c in body:
        if c == "(":
            depth += 1
            cur.append(c)
        elif c == ")":
            depth -= 1
            cur.append(c)
        elif c == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
    if cur:
        parts.append("".join(cur).strip())
    return [p for p in parts if p]


_COL_RE = re.compile(
    r"^\s*(?P<col>\w+)\s+(?P<type>[\w%]+(?:\s*\([^)]*\))?(?:\[\s*\])?)\s*$",
    re.IGNORECASE,
)
_MEMBER_RE = re.compile(r"\bMEMBER\s+(?:PROCEDURE|FUNCTION)\s+(\w+)", re.IGNORECASE)
# 行内注释：'-- x' 与块注释 '/* x */'
_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


def _types_by_regex(sql: str) -> List[TypeMeta]:
    if not isinstance(sql, str) or not sql:
        return []
    out: List[TypeMeta] = []
    for m in _TYPE_RE.finditer(sql):
        name = m.group("name").upper()
        body = _balanced_body(sql, m.end())
        cols: List[Tuple[str, str]] = []
        is_collection = False
        for p in _split_top_commas(body):
            # 行内注释会吞掉类型：'CNT NUMBER,  -- 计数'
            # 必须在切分后、匹配前列把 '-- ...' 去掉
            p = _COMMENT_RE.sub("", p).strip()
            if not p:
                continue
            if p.upper().startswith(("MEMBER", "MAP", "FINAL", "NOT", "PRAGMA")):
                continue
            if p.endswith("[]") or p.endswith("[ ]"):
                is_collection = True
            cm = _COL_RE.match(p)
            if cm:
                cols.append((cm.group("col").upper(), cm.group("type").strip().upper()))
        out.append(TypeMeta(
            name=name,
            columns=cols,
            is_collection=is_collection,
            methods=[x.upper() for x in _MEMBER_RE.findall(body)],
            source="regex",
        ))
    # TABLE OF 嵌套表（AS TABLE OF xxx）不在 AS ( 分支内，单独抓
    for m in re.finditer(
        r"\bCREATE\s+(?:OR\s+REPLACE\s+)?TYPE\s+(?P<name>\w+)\s+AS\s+TABLE\s+OF\b",
        sql, re.IGNORECASE,
    ):
        out.append(TypeMeta(name=m.group("name").upper(), is_collection=True, source="regex"))
    return out


# ---------------------------------------------------------------------------
# 函数 / 过程（正则兜底为主——Community grammar 对包内嵌套支持有限）
# ---------------------------------------------------------------------------

_ROUTINE_RE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?P<kind>FUNCTION|PROCEDURE)\s+(?P<name>[\w\.\$]+)\s*"
    r"\((?P<params>[^)]*)\)(?P<ret>\s+RETURN\s+[\w\.\%\(\)]+)?",
    re.IGNORECASE,
)

_PARAM_SPLIT_RE = re.compile(r"\b(INOUT|OUT|IN)\b", re.IGNORECASE)
# Oracle 内置类型 / 常见自定义类型特征词
_TYPE_HINT_RE = re.compile(
    r"^(?:NUMBER|VARCHAR2|NVARCHAR2|VARCHAR2?|CHAR|NCHAR|DATE|TIMESTAMP|"
    r"INTEGER|INT|SMALLINT|BIGINT|DECIMAL|NUMERIC|REAL|DOUBLE\s+PRECISION|"
    r"FLOAT|BOOLEAN|CLOB|BLOB|RAW|LONG|ROWID|UROWID|JSON|JSONB|XMLTYPE)\b",
    re.IGNORECASE,
)
_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_$#]*$")


def _parse_one_param(seg: str) -> Optional[Tuple[str, str]]:
    """
    解析单个参数段 -> (名字, 类型)。

    Oracle 的参数顺序两种都合法，且本语料两种都出现：
        `i_nSTUDENT_NO IN NUMBER`        （名字在前）
        `IN NUMBER i_nSTUDENT_NO`        （类型在前）

    剥离 IN/OUT/INOUT 与 DEFAULT 后按"类型特征词"判定哪一端是类型，
    不做固定顺序假设。
    """
    s = _COMMENT_RE.sub("", seg)
    s = _PARAM_SPLIT_RE.sub(" ", s)          # 去掉方向词
    s = re.split(r"\bDEFAULT\b", s, flags=re.IGNORECASE)[0]
    s = s.strip().strip(",").strip()
    if not s:
        return None

    parts = s.split()
    if len(parts) < 2:
        return None

    # 找第一个"像类型"的 token
    for i, tok in enumerate(parts):
        head = tok.split("(")[0]
        if _TYPE_HINT_RE.match(head) or re.fullmatch(r"[\w%]+_TYPE", head, re.IGNORECASE):
            if i == 0:
                # 类型在前：'NUMBER I_X'  -> 名字是最后一个
                name = parts[-1].upper()
                dtype = " ".join(parts[:-1]).upper()
            else:
                # 名字在前：'I_X NUMBER'  -> 名字是 i 之前的那个 token
                name = parts[i - 1].upper()
                dtype = " ".join(parts[i:]).upper()
            if not _NAME_RE.match(name):
                return None
            return name, dtype

    # 没识别出类型特征词：保守取"最后一个 token 当名字"
    name = parts[-1].upper()
    dtype = " ".join(parts[:-1]).upper()
    if not _NAME_RE.match(name):
        return None
    return name, dtype


def _routines_by_regex(sql: str) -> List[RoutineMeta]:
    if not isinstance(sql, str) or not sql:
        return []
    out: List[RoutineMeta] = []
    for m in _ROUTINE_RE.finditer(sql):
        raw = m.group("params").strip()
        params: List[Tuple[str, str]] = []
        if raw:
            for seg in re.split(r",(?![^()]*\))", raw):
                p = _parse_one_param(seg)
                if p:
                    params.append(p)
        ret = re.sub(r"\s+", " ", m.group("ret") or "").upper().replace("RETURN", "").strip()
        out.append(RoutineMeta(
            name=m.group("name").upper(),
            kind=m.group("kind").upper(),
            params=params,
            return_type=ret,
            source="regex",
        ))
    return out


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------


def extract_triggers(sql: str, pr: Optional[ParseResult] = None) -> Tuple[List[TriggerMeta], bool]:
    """
    提取触发器元数据。AST 优先，失败降级正则。

    Returns: (触发器列表, 是否降级)
    """
    r = pr if pr is not None else parse_plsql(sql)
    if r.ok and r.tree is not None:
        got = _triggers_from_tree(r.tree)
        if got:
            return got, False
    return _triggers_by_regex(sql), not (r.ok and r.tree is not None)


def extract_types(sql: str, pr: Optional[ParseResult] = None) -> Tuple[List[TypeMeta], bool]:
    """
    提取类型元数据。AST 优先，失败降级正则。

    Returns: (类型列表, 是否降级)
    """
    r = pr if pr is not None else parse_plsql(sql)
    if r.ok and r.tree is not None:
        got = _types_from_tree(r.tree)
        if got:
            return got, False
    return _types_by_regex(sql), not (r.ok and r.tree is not None)


def extract_routines(sql: str, pr: Optional[ParseResult] = None) -> List[RoutineMeta]:
    """
    提取函数/过程元数据。

    走正则：Community grammar 对包内嵌套子程序支持不足
    （嵌套函数缺 DECLARE 会连锁报错），而函数/过程元数据主要来自
    签名行，正则在这类场景比 AST 更稳。AST 留给结构体检。
    """
    return _routines_by_regex(sql)


def extract_all(sql: str) -> MetadataResult:
    """
    一次解析，提取全部元数据（推荐入口）。

    共享同一个 ParseResult，避免重复解析（ANTLR 很慢）。
    """
    res = MetadataResult()
    if not isinstance(sql, str) or not sql.strip():
        res.parse_ok = False
        res.degraded = True
        res.parse_errors = [
            f"input is not a non-empty str (got {type(sql).__name__})"
        ]
        return res

    pr = parse_plsql(sql)
    res.parse_ok = pr.ok
    res.parse_errors = list(pr.errors)
    res.degraded = not pr.ok

    res.triggers, dg1 = extract_triggers(sql, pr)
    res.types, dg2 = extract_types(sql, pr)
    res.routines = extract_routines(sql, pr)
    res.degraded = res.degraded or dg1 or dg2
    return res


def to_json_dict(res: MetadataResult) -> Dict[str, Any]:
    """转成可 JSON 序列化的 dict（报告落盘用）。"""
    return {
        "summary": res.summary(),
        "parse_errors": res.parse_errors[:20],
        "triggers": [
            {
                "name": t.name, "timing": t.timing, "events": t.events,
                "table": t.table, "is_compound": t.is_compound,
                "statement_level": t.statement_level, "source": t.source,
            }
            for t in res.triggers
        ],
        "types": [
            {
                "name": t.name, "columns": [{"name": n, "type": ty} for n, ty in t.columns],
                "is_collection": t.is_collection, "methods": t.methods, "source": t.source,
            }
            for t in res.types
        ],
        "routines": [
            {
                "name": r.name, "kind": r.kind,
                "params": [{"name": n, "type": ty} for n, ty in r.params],
                "return_type": r.return_type, "source": r.source,
            }
            for r in res.routines
        ],
    }
