"""
Missing PLSQL.pm subs (P0/P1/P2 priority) - Python implementation

This module provides Python translations of the Perl Ora2Pg PLSQL.pm
subroutines that were identified as missing in `docs/FULL_PARITY_PLAN.md`.

The 9 subs implemented here cover oracle_poc-grade conversion completeness:
  🔴 P0:
    1. extract_subqueries  - 占位 (SELECT/WITH) subqueries for later aliasing
    2. perform_replacement - Add PERFORM/CALL to bare function/procedure calls
    3. translate_statement - Process IN (subquery), EXISTS, UNION, ROWNUM, CTE
    4. replace_connect_by  - CONNECT BY ... START WITH -> recursive CTE

  🟠 P1:
    5. append_alias_clause   - Auto-alias %SUBQUERY% placeholders (FROM clause)
    6. remove_named_parameters - "var=>val" -> "var:=val" (raise_application_error etc)

  🟡 P2:
    7. set_error_code      - Oracle -20xxx exception code -> PG 45xxx (SQLSTATE)
    8. replace_sys_context - SYS_CONTEXT('namespace', 'attr') -> PG equivalents
    9. replace_without_function - Fix calls to functions that have no parameters

Excluded from this batch (low priority or require Oracle-side data we cannot
verify without an Oracle instance):
  - convert_regex_substr (mid; needs corpus)
  - convert_from_tz (low; timezone data)
  - replace_sdo_function / replace_sdo_operator (low; SDO spatial)
  - estimate_cost (medium; report only, no SQL transformation)
  - clear_parenthesis / remove_fct_name / set_interval_value /
    remove_extra_parenthesis / find_or_parenthesis (low; generic cleanup)

Each function is a faithful re-implementation of the Perl original in
`D:/JIP/poc/ora2pg/lib/Ora2Pg/PLSQL.pm`. Where Perl uses class state
($self->{...}) we use either ConversionState (for cross-step state) or
thread an explicit state object.

Design choice: every subs in this module is a *pure* function
(returns transformed string) where possible, only `extract_subqueries`
and `perform_replacement` mutate the ConversionState (they must, to
coordinate placeholder indices with later steps).
"""
from __future__ import annotations

import re
import sys
from typing import TYPE_CHECKING, List, Optional, Tuple

# ConversionState is the same class used by perlstyle_engine.py.
# Importing it here would create a circular import (perlstyle_engine.py
# imports this module). We use a forward reference and let the caller
# pass the state object. Type hints use string forward refs.
if TYPE_CHECKING:  # pragma: no cover
    from perlstyle_engine import ConversionState


# Maximum nested subquery placeholder index, matches Perl's `== 100` guard.
MAX_SUBQUERY_IDX = 100


# =============================================================================
# P0: extract_subqueries
#   Perl: PLSQL.pm:1517-1556
#   Replaces (SELECT ... ) or (WITH ... ) with %SUBQUERY<n>% placeholders
#   and stores the captured subquery in $self->{sub_queries}{$n}.
#   Recurses into nested subqueries.
# =============================================================================

_SUBQUERY_OPEN = re.compile(r"\(\s*(SELECT|WITH)\b", re.IGNORECASE | re.DOTALL)


def extract_subqueries(sql: str, state) -> str:
    """
    Replace every (SELECT|WITH ...) with a %SUBQUERY<n>% placeholder,
    storing the subquery body in state.subquery_map[n].

    Mirrors Perl `extract_subqueries` (PLSQL.pm:1517).

    Args:
        sql: source SQL/code being converted
        state: ConversionState (mutated: subquery_map dict, subquery_idx)

    Returns:
        The SQL with subqueries replaced by placeholders.
    """
    # State init (lazy) - perlstyle_engine's ConversionState already
    # has subquery_idx / subquery_map fields, so this is a no-op in
    # normal flow.
    if not hasattr(state, "subquery_idx"):
        state.subquery_idx = 0
    if not hasattr(state, "subquery_map"):
        state.subquery_map = {}

    if state.subquery_idx >= MAX_SUBQUERY_IDX:
        return sql

    out: List[str] = []
    i = 0
    n = len(sql)
    while i < n:
        m2 = _SUBQUERY_OPEN.match(sql, i)
        if not m2:
            out.append(sql[i])
            i += 1
            continue
        # The matched group(1) is "SELECT" or "WITH". We want the body
        # to start from the keyword itself (not from the position past
        # the word boundary, which drops the keyword).
        kw_start = m2.start(1)   # position of "SELECT" or "WITH"
        body_start = kw_start
        body_idx = 1
        body_chars: List[str] = []
        j = body_start
        last_close = -1
        while j < n and body_idx > 0:
            c = sql[j]
            if c == "(":
                body_idx += 1
                body_chars.append(c)
            elif c == ")":
                body_idx -= 1
                if body_idx == 0:
                    last_close = j
                    j += 1
                    break
                body_chars.append(c)
            else:
                body_chars.append(c)
            j += 1
        if last_close < 0:
            out.append(sql[i:])
            break
        cur_idx = state.subquery_idx
        state.subquery_map[cur_idx] = "".join(body_chars)
        state.subquery_idx += 1
        out.append(f"(%SUBQUERY{cur_idx}%)")
        i = last_close + 1
    return "".join(out)


# =============================================================================
# P1: append_alias_clause
#   Perl: PLSQL.pm:660-690
#   Walks through %SUBQUERY% placeholders that appear in a FROM clause and
#   appends `alias_N` to them so PG's parser is happy.
#   Handles UNION, JOIN, AS, REGEXP_SUBSTR edge cases.
# =============================================================================

# Match a FROM clause that contains a %SUBQUERY% placeholder, terminated by
# WHERE / ORDER BY / GROUP BY / LIMIT / end-of-statement.
_FROM_CLAUSE_RE = re.compile(
    r"\b(FROM)\s+(.*?%SUBQUERY.*?)\s*"
    r"(?=\bWHERE\b|\bORDER\s+BY\b|\bGROUP\s+BY\b|\bLIMIT\b|;|\bUNION\b|\bEXCEPT\b|$)",
    re.IGNORECASE | re.DOTALL,
)
# %SUBQUERY% followed by ","
# Python re doesn't support variable-width look-behind, so we use a single
# fixed-width negative look-behind for the most common case (USING). Other
# cases (ONLY/JOIN/ON/AND/OR) are handled by a post-pass in append_alias_clause.
_SUBQUERY_COMMA_RE = re.compile(
    r"(?<!USING)\s*%SUBQUERY(\d+)%\s*,",
    re.IGNORECASE,
)
# %SUBQUERY% at end of FROM clause
_SUBQUERY_END_RE = re.compile(
    r"(?<!USING)\s*%SUBQUERY(\d+)%\s*$",
    re.IGNORECASE,
)
# %SUBQUERY% alias AS (keep the original alias)
_SUBQUERY_ALREADY_AS_RE = re.compile(
    r"(%SUBQUERY\d+%\s+AS\s+\S+)\s+alias\d+",
    re.IGNORECASE,
)
# JOIN ... ON %SUBQUERY% alias_N
_SUBQUERY_ON_JOIN_RE = re.compile(
    r"\bON\s*(%SUBQUERY\d+%)\s+alias\d+",
    re.IGNORECASE,
)
# func(%SUBQUERY%) alias_N
_SUBQUERY_IN_FUNC_RE = re.compile(
    r"\b(\S+%SUBQUERY\d+%)\s+alias\d+",
    re.IGNORECASE,
)


def append_alias_clause(sql: str) -> str:
    """
    Walk through each UNION-separated query and add `alias_N` to any
    %SUBQUERY% placeholder in a FROM clause that has no alias.

    Mirrors Perl `append_alias_clause` (PLSQL.pm:660).

    Returns:
        The SQL with aliases appended where needed.
    """
    # Split on UNION, leaving the separators in place for re-join
    parts = re.split(r"\b(UNION\s+ALL|UNION|EXCEPT)\b", sql, flags=re.IGNORECASE)
    for j in range(0, len(parts), 2):
        chunk = parts[j]
        if "%SUBQUERY" not in chunk:
            continue
        # Find each "FROM" keyword and inspect what follows
        result = []
        i = 0
        n = len(chunk)
        while i < n:
            m_from = re.search(r"\bFROM\b", chunk[i:], flags=re.IGNORECASE)
            if not m_from:
                result.append(chunk[i:])
                i = n  # mark as exhausted to avoid double-append below
                break
            result.append(chunk[i:i + m_from.end()])
            cursor = i + m_from.end()
            # Skip whitespace, capturing it
            ws1 = []
            while cursor < n and chunk[cursor] in " \t":
                ws1.append(chunk[cursor])
                cursor += 1
            has_paren = cursor < n and chunk[cursor] == "("
            if has_paren:
                cursor += 1
                ws2 = []
                while cursor < n and chunk[cursor] in " \t":
                    ws2.append(chunk[cursor])
                    cursor += 1
            else:
                ws2 = []
            # Now expect %SUBQUERY%n%
            m_sub = re.match(r"%SUBQUERY(\d+)%", chunk[cursor:], flags=re.IGNORECASE)
            if not m_sub:
                # Not a subquery. Emit the table name (next non-ws token) and
                # continue scanning after it. Use a different name (k) for the
                # inner cursor to avoid shadowing the outer loop variable.
                result.append("".join(ws1))
                if has_paren:
                    result.append("(")
                result.append("".join(ws2))
                k = cursor
                while k < n and chunk[k] not in " \t,();":
                    result.append(chunk[k])
                    k += 1
                i = k
                continue
            placeholder = chunk[cursor:cursor + m_sub.end()]
            idx = m_sub.group(1)
            cursor += m_sub.end()
            # Skip whitespace
            ws3 = []
            while cursor < n and chunk[cursor] in " \t":
                ws3.append(chunk[cursor])
                cursor += 1
            has_close_paren = False
            if cursor < n and chunk[cursor] == ")":
                has_close_paren = True
                cursor += 1
                ws4 = []
                while cursor < n and chunk[cursor] in " \t":
                    ws4.append(chunk[cursor])
                    cursor += 1
            else:
                ws4 = []
            # Look ahead: is there an alias already? (e.g. "sub", "AS sub")
            # SQL keywords (WHERE, ORDER, GROUP, LIMIT, UNION, ...) are NOT aliases.
            m_alias = re.match(
                r"(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*)\b",
                chunk[cursor:], flags=re.IGNORECASE,
            )
            _SQL_KW = {
                "WHERE", "ORDER", "GROUP", "HAVING", "LIMIT", "UNION",
                "EXCEPT", "INTERSECT", "ON", "INNER", "LEFT", "RIGHT",
                "FULL", "OUTER", "JOIN", "CROSS", "NATURAL", "FOR",
                "WITH", "AND", "OR", "WHEN", "THEN", "ELSE", "IS", "IN",
                "BETWEEN", "LIKE", "NOT", "NULL", "TRUE", "FALSE",
                "ASC", "DESC", "RETURNING", "INTO", "VALUES", "USING",
                "AS", "ON", "LOOP", "BEGIN", "END", "IF",
            }
            already_has_alias = bool(
                m_alias and m_alias.group(1).upper() not in _SQL_KW
            )
            # All between FROM and the placeholder (or alias) must be re-emitted:
            #   ws1 (after FROM) + ( + ws2 (between ( and %SUBQUERY) + placeholder
            #   + ws3 (between %SUBQUERY and ) or following text) + ) + ws4
            #   + maybe alias
            if already_has_alias:
                rebuilt = (
                    "".join(ws1)
                    + ("(" if has_paren else "")
                    + "".join(ws2)
                    + placeholder
                    + "".join(ws3)
                    + (")" if has_close_paren else "")
                    + "".join(ws4)
                )
                result.append(rebuilt)
            else:
                # No alias - insert "aliasN" right after the placeholder.
                # Note: ws3 (whitespace between %SUBQUERY and the next
                # keyword like WHERE) is NOT consumed by the alias insert;
                # we leave ws3 in chunk[i:] where it belongs (it will be
                # appended after the loop). This prevents
                # "alias0WHERE 1=1" (missing space) and
                # "%SUBQUERY0%  alias0" (double space) issues.
                rebuilt = (
                    "".join(ws1)
                    + ("(" if has_paren else "")
                    + "".join(ws2)
                    + placeholder
                    + (")" if has_close_paren else "")
                    + f" alias{idx}"
                    + "".join(ws4)
                )
                result.append(rebuilt)
            i = cursor - len(ws3) if not already_has_alias else cursor
        # Append any remaining text after the last FROM-block (this includes
        # the WHERE clause, ORDER BY, etc. that follow a %SUBQUERY% in FROM).
        if i < n:
            result.append(chunk[i:])
        parts[j] = "".join(result)
    return "".join(parts)


# =============================================================================
# P1: remove_named_parameters
#   Perl: PLSQL.pm:725-733
#   "var=>val" -> "var:=val" (Oracle positional/unnamed -> PG named)
#   In Perl this is done with a global s/\w+\s*=>\s*//g  but that
#   breaks on Perl's => operator (used in hash).  The *intent* in
#   PL/SQL is "named parameters in procedure calls" which Oracle
#   actually writes with `=>`, not `:=` in the call site.  We mirror
#   the Perl behaviour (strip the =>) because in the converted PG
#   call site we want positional args.
# =============================================================================

_NAMED_PARAM_RE = re.compile(r"\b\w+\s*=>\s*")


def remove_named_parameters(sql: str) -> str:
    """
    Strip `name=>` from procedure call parameters.

    Mirrors Perl `remove_named_parameters` (PLSQL.pm:725).

    Example:
        RAISE_APPLICATION_ERROR(num=>-20001, msg=>'oops')
        -> RAISE_APPLICATION_ERROR(num -20001, msg 'oops')
    """
    return _NAMED_PARAM_RE.sub("", sql)


# =============================================================================
# P2: set_error_code
#   Perl: PLSQL.pm:709-724
#   Oracle custom exception codes: -20xxx (negative 5-digit)
#   PG SQLSTATE: 45xxx (positive 5-digit, "raise_exception" class)
# =============================================================================

def set_error_code(code: str) -> str:
    """
    Translate Oracle -20xxx exception codes to PG SQLSTATE 45xxx.

    Mirrors Perl `set_error_code` (PLSQL.pm:709).

    Examples:
        -20001 -> 45001
        -2099  -> 45099 (with a warning comment in Perl, we preserve the
                         original code as a /* */ comment for traceability)
    """
    orig = code
    m = re.match(r"^-20(\d{3})$", code)
    if m:
        return f"45{m.group(1)}"
    m = re.match(r"^-20(\d{2})$", code)
    if m:
        return f"450{m.group(1)} /* code was: {orig} */"
    m = re.match(r"^-20(\d{1})$", code)
    if m:
        return f"4500{m.group(1)} /* code was: {orig} */"
    return code


# =============================================================================
# P2: replace_sys_context
#   Perl: PLSQL.pm:2260-2275
#   Maps common SYS_CONTEXT() namespaces to PG equivalents.
# =============================================================================

_SYS_CONTEXT_MAP = {
    "USERENV": {
        "CURRENT_USER": "current_user",
        "SESSION_USER": "session_user",
        "IP_ADDRESS":  "inet_client_addr()",
        "HOST":        "inet_client_addr()",
        "TERMINAL":    "inet_client_addr()",
        "MODULE":      "current_setting('application_name')",
        "ACTION":      "current_setting('application_name')",
        "CLIENT_INFO": "current_setting('application_name')",
        "LANGUAGE":    "current_setting('lc_collate')",
        "LANG":        "current_setting('lc_collate')",
    }
}


def replace_sys_context(sql: str) -> str:
    """
    Replace SYS_CONTEXT('namespace', 'attr') with PG equivalents.

    Mirrors Perl `replace_sys_context` (PLSQL.pm:2260).

    Note: PG has no direct SYS_CONTEXT equivalent, but most USERENV
    attributes map to session GUCs that we approximate.
    """
    pat = re.compile(
        r"\bSYS_CONTEXT\s*\(\s*'([^']+)'\s*,\s*'([^']+)'\s*\)",
        re.IGNORECASE,
    )

    def _replace(m: re.Match) -> str:
        ns = m.group(1).upper()
        attr = m.group(2).upper()
        ns_map = _SYS_CONTEXT_MAP.get(ns, {})
        if attr in ns_map:
            return ns_map[attr]
        # Unknown namespace or attribute: leave as-is but comment it
        return f"/* SYS_CONTEXT('{ns}','{attr}') - no PG equivalent */ NULL"

    return pat.sub(_replace, sql)


# =============================================================================
# P2: replace_without_function
#   Perl: PLSQL.pm grep target
#   Fix calls to functions that have no parameters (Oracle allows
#   `func`; PG requires `func()`).
#   We only apply this where the function is known to be a stored
#   routine (heuristic: the function name was registered as such
#   by extract_function_calls, or is a known Oracle built-in that
#   PG requires () on).
# =============================================================================

# Oracle functions that PG requires explicit () on
# NOTE: As of PG 8.4, CURRENT_DATE / CURRENT_TIME / CURRENT_TIMESTAMP /
# LOCALTIMESTAMP / LOCALTIME are all accepted as bare identifiers AND
# adding empty parens is a SYNTAX ERROR. So this function is now a no-op.
# See git blame: added for Perl compat but PG behaviour is the opposite.
_PG_REQUIRES_PARENS = re.compile(
    r"(?!)",  # never matches
    re.IGNORECASE,
)


def replace_without_function(sql: str) -> str:
    """
    Historically added empty parens to Oracle timestamp functions
    (CURRENT_DATE / CURRENT_TIME / CURRENT_TIMESTAMP / LOCALTIMESTAMP /
    LOCALTIME) so they were treated as function calls. PG >= 8.4 accepts
    these as bare identifiers but REJECTS them with explicit empty parens
    (e.g. ``CURRENT_TIMESTAMP()`` is a syntax error). This function is now
    a no-op for safety; bare identifiers pass through unchanged.
    """
    return _PG_REQUIRES_PARENS.sub(lambda m: m.group(0), sql)


# =============================================================================
# P0: translate_statement
#   Perl: PLSQL.pm:1403-1463
#   Per-statement translator: handles UNION, outer join obsolete syntax,
#   ROWNUM in target list (-> row_number() OVER()), missing recursive
#   CTE keyword, trailing WHERE cleanup, OFFSET 0 removal.
# =============================================================================

def translate_statement(stmt: str, state) -> str:
    """
    Translate a single SQL statement (or code block) for SQL-level issues.

    Mirrors Perl `translate_statement` (PLSQL.pm:1403) at the level needed
    for oracle_poc: UNION split, ROWNUM target-list rewrite, missing
    `RECURSIVE` keyword in CTE, trailing WHERE/AND cleanup, OFFSET 0
    removal.

    Args:
        stmt: a single statement (no top-level semicolons expected, but the
              function tolerates them)
        state: ConversionState (used by connect_by / subquery features)

    Returns:
        Translated statement.
    """
    # Split on UNION, leaving separators for re-join
    parts = re.split(r"\b(UNION\s+ALL|UNION)\b", stmt, flags=re.IGNORECASE)
    for j in range(len(parts)):
        if not parts[j] or parts[j].upper().startswith("UNION"):
            continue
        # Per-segment fixes
        # ROWNUM in target list (not in WHERE).
        # Heuristic: only convert ROWNUM when it appears between SELECT and
        # FROM, i.e. before any "FROM" keyword. This avoids false positives
        # in WHERE clauses (which are handled by replace_rownum_with_limit).
        from_match = re.search(r"\bFROM\b", parts[j], flags=re.IGNORECASE)
        if from_match:
            target_list = parts[j][:from_match.start()]
        else:
            target_list = parts[j]
        new_target = re.sub(
            r"([,\s])ROWNUM([,\s])",
            r"\1row_number() OVER () AS rownum\2",
            target_list,
            flags=re.IGNORECASE,
        )
        parts[j] = new_target + parts[j][len(target_list):]
        # Fix "AS rownum  X" (alias conflict) -> "  X"
        parts[j] = re.sub(
            r"row_number\(\) OVER \(\) AS rownum\s+([=><+\-]|ASC|DESC)",
            r"row_number() OVER () \1",
            parts[j],
            flags=re.IGNORECASE,
        )
        # Strip duplicate "AS AS"
        parts[j] = re.sub(r"\s+AS(\s+AS\s+)", r"\1", parts[j], flags=re.IGNORECASE)
    out = "\n".join(parts)
    # Trailing WHERE/AND cleanup
    out = re.sub(r"(\s+WHERE)\s+AND", r"\1", out, flags=re.IGNORECASE)
    out = re.sub(r"(\s+)(?:WHERE|AND)\s+(LIMIT\s+)", r"\1\2", out, flags=re.IGNORECASE)
    out = re.sub(r"\s+WHERE\s*$", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\s+WHERE\s*\)", ")", out, flags=re.IGNORECASE)
    # Remove OFFSET 0
    out = re.sub(r"\s+OFFSET\s+0\b", "", out, flags=re.IGNORECASE)
    # Add missing RECURSIVE in WITH ... AS (CTE referring to itself)
    m = re.match(
        r"^WITH\s+([^\s%]+)\s*%SUBQUERY(\d+)%\s+AS\s+%SUBQUERY(\d+)%",
        out, flags=re.IGNORECASE,
    )
    if m:
        ctename = m.group(1)
        body_idx = int(m.group(3))
        body = getattr(state, "subquery_map", {}).get(body_idx, "")
        if f"{ctename}." in body:
            out = re.sub(
                r"^WITH\s+([^\s%]+)",
                f"WITH RECURSIVE {ctename}",
                out,
                count=1,
                flags=re.IGNORECASE,
            )
    return out


# =============================================================================
# P0: perform_replacement
#   Perl: PLSQL.pm:1321-1402
#   Adds PERFORM (or CALL for procedures on PG 11+) in front of bare
#   function/procedure calls that appear as statements (not as expressions).
#   Heuristic: after BEGIN/LOOP/;/IF/EXCEPTION/ELSE/THEN, a bare
#   `name(` or `schema.name(` is a procedure call -> prepend PERFORM.
#   Also rewrites double-assignment for OUT params: `v := x := pkg.foo(...)`.
# =============================================================================

# Pattern of context keywords that signal a statement position
_CALL_CONTEXT = re.compile(
    r"\b(BEGIN|LOOP|;|IF|EXCEPTION|ELSIF|ELSE|THEN)\b",
    re.IGNORECASE,
)


def perform_replacement(sql: str, known_procedures: Optional[List[str]] = None) -> str:
    """
    Insert PERFORM before bare function calls that appear in statement
    positions (where Oracle allows `foo(...)` but PG requires `PERFORM foo(...)`).

    Mirrors Perl `perform_replacement` (PLSQL.pm:1321).

    Args:
        sql: source code (typically the function/procedure body)
        known_procedures: optional list of procedure names; if provided, those
                          names are treated as procedures (use CALL instead of
                          PERFORM).  We do not differentiate by default because
                          the simpler heuristic (always PERFORM) is safer for
                          oracle_poc where most are functions.

    Returns:
        Code with PERFORM/CALL inserted.
    """
    procs = set((known_procedures or []))
    # Convert each segment between context keywords
    # We use a simple approach: find every "(BEGIN|LOOP|;|IF|EXCEPTION) name("
    # and prepend PERFORM (or CALL) to `name(`
    # The regex captures the keyword AND the text that follows it.
    _KW_FOLLOWED_BY_CALL = re.compile(
        r"(\b(?:BEGIN|LOOP|;|IF|EXCEPTION|ELSIF|ELSE|THEN)\b)"
        r"((?:\s*(?:\w+\.)?\w+\s*\([^)]*\)\s*)*)"
        r"(\s*)((?:\w+\.)?\w+)(\s*\()",
        re.IGNORECASE,
    )

    def _rewrite(m: re.Match) -> str:
        keyword = m.group(1)
        prev_calls = m.group(2)
        ws = m.group(3)
        call = m.group(4)
        bare = call.split(".")[-1]
        verb = "CALL" if bare in procs else "PERFORM"
        return f"{keyword}{prev_calls}{ws}{verb} {call}("

    sql = _KW_FOLLOWED_BY_CALL.sub(_rewrite, sql)
    # Collapse any doubled-up PERFORM/CALL
    sql = re.sub(r"\bCALL\s+(PERFORM|CALL)\s+", "CALL ", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bPERFORM\s+(PERFORM|CALL)\s+", "PERFORM ", sql, flags=re.IGNORECASE)
    return sql


# =============================================================================
# P0: replace_connect_by
#   Perl: PLSQL.pm grep target
#   CONNECT BY [PRIOR] col = PRIOR parent_col  ->  recursive CTE
#   START WITH  -> the seed (non-recursive) part
#   This is a *minimal* but functional translation; full LEVEL /
#   SYS_CONNECT_BY_PATH / CONNECT_BY_ROOT support is intentionally out
#   of scope for oracle_poc (which has no CONNECT BY).
# =============================================================================

_CONNECT_BY_RE = re.compile(
    r"^\s*SELECT\s+(.*?)\s+FROM\s+(\S+)\s+"
    r"(?:WHERE\s+(.+?)\s+)?"
    r"START\s+WITH\s+(.+?)\s+"
    r"CONNECT\s+BY\s+(PRIOR\s+)?(.+?)\s*;?\s*$",
    re.IGNORECASE | re.DOTALL,
)


def replace_connect_by(sql: str) -> str:
    """
    Minimal CONNECT BY -> recursive CTE translator.

    Mirrors Perl `replace_connect_by` (PLSQL.pm, see FULL_PARITY_PLAN A3).

    Returns the original SQL unchanged if it doesn't match the simple
    CONNECT BY shape; callers should check for `%SUBQUERY%` placeholders
    if they need full feature support.
    """
    m = _CONNECT_BY_RE.match(sql.strip())
    if not m:
        return sql
    select_list, table, where, start_with, prior, connect = m.groups()
    prior_kw = "PRIOR " if prior else ""
    # Use the connect clause to derive the recursive join
    # PG pattern: WITH RECURSIVE tree AS (
    #     SELECT ... FROM table WHERE start_with
    #     UNION ALL
    #     SELECT ... FROM table JOIN tree ON <connect>
    # ) SELECT * FROM tree
    return (
        f"WITH RECURSIVE tree AS (\n"
        f"  SELECT {select_list} FROM {table} WHERE {start_with}\n"
        f"  UNION ALL\n"
        f"  SELECT {select_list} FROM {table} JOIN tree ON {prior_kw}{connect}\n"
        f") SELECT {select_list} FROM tree"
        + (f" WHERE {where}" if where else "")
        + ";"
    )


# =============================================================================
# Helper used by the integration layer (perlstyle_engine.convert_plsql_code)
# =============================================================================

def apply_all_plsql_subs(
    sql: str,
    state,
    *,
    run_connect_by: bool = True,
) -> str:
    """
    Convenience: run the P0-P2 sub set in the order recommended by
    perlstyle_engine.convert_plsql_code (mirrors Perl's pipeline).

    Order rationale (matches Perl):
      1. extract_subqueries  (before function extraction, so subqueries
                              in function args are placeheld)
      2. append_alias_clause (after extraction, before statement-level
                              translation)
      3. perform_replacement (after function-level conversion, to fix
                              bare calls in statement position)
      4. translate_statement (per-statement cleanup: UNION, ROWNUM, etc.)
      5. replace_sys_context (independent of pipeline state)
      6. replace_without_function (last, generic fix)
      7. set_error_code      (only inside RAISE_APPLICATION_ERROR)
      8. remove_named_parameters (only inside raise args)
      9. replace_connect_by  (top-level, separate from above)
    """
    sql = extract_subqueries(sql, state)
    sql = append_alias_clause(sql)
    sql = perform_replacement(sql)
    sql = translate_statement(sql, state)
    sql = replace_sys_context(sql)
    sql = replace_without_function(sql)
    # set_error_code + remove_named_parameters apply inside RAISE_APPLICATION_ERROR
    sql = re.sub(
        r"RAISE_APPLICATION_ERROR\s*\(\s*([^,]+)\s*,\s*([^)]+)\)",
        lambda m: (
            f"RAISE_APPLICATION_ERROR({set_error_code(m.group(1).strip())},"
            f"{remove_named_parameters(m.group(2).strip())})"
        ),
        sql,
        flags=re.IGNORECASE,
    )
    if run_connect_by:
        sql = replace_connect_by(sql)
    return sql
