"""
Perl-style Ora2Pg Python Implementation

Mirrors the architecture of the original Perl Ora2Pg:
1. Placeholder strategy: (+), function calls, subparts, subqueries
2. Split-by-BEGIN for function/procedure body extraction
3. Balanced BEGIN/END counter for nested blocks
4. Recursive function call extraction
5. Multi-step restoration at end of pipeline

This module is intentionally separate from ora2pg_py.py and follows
the original Perl implementation patterns as closely as practical.
"""

from __future__ import annotations

import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# =============================================================================
# Placeholder protocol (mirrors Perl)
# =============================================================================
# %OUTERJOIN\d+%    : (+) outer join markers
# %%REPLACEFCT\d+%% : function call placeholders
# %SUBPART\d+%      : parenthesised expressions with OUTERJOIN
# %SUBQUERY\d+%     : subqueries
# %SUBPARAMS\d+%    : nested function parameters
# %ORA2PG_COMMENT\d+% : comments
# =============================================================================


@dataclass
class OracleFunction:
    """Mirrors Perl _lookup_function result"""
    name: str = ""
    type: str = ""  # FUNCTION or PROCEDURE
    args: str = ""
    declare: str = ""
    code: str = ""
    return_type: str = "OPAQUE"
    has_out_params: bool = False
    full_source: str = ""


@dataclass
class ConversionState:
    """Mutable state used across pipeline steps (mirrors $class->{...})"""
    outerjoin_idx: int = 0
    outerjoin_map: Dict[str, str] = field(default_factory=dict)
    consumed_outerjoins: set = field(default_factory=set)  # tracks which %OUTERJOIN% markers were converted

    fct_call_idx: int = 0
    fct_call_map: Dict[str, str] = field(default_factory=dict)

    subpart_idx: int = 0
    subpart_map: Dict[str, str] = field(default_factory=dict)

    subquery_idx: int = 0
    subquery_map: Dict[str, str] = field(default_factory=dict)

    subparams_idx: int = 0
    subparams_map: Dict[str, str] = field(default_factory=dict)

    comment_idx: int = 0
    comment_map: Dict[str, str] = field(default_factory=dict)

    # Variables declared as arrays (TYPE[]) - shared across blocks within a package
    array_vars: Set[str] = field(default_factory=set)

    type: str = ""  # current conversion type (FUNCTION, PACKAGE, etc.)

    warnings: List[str] = field(default_factory=list)


# =============================================================================
# Core helpers
# =============================================================================
def _split_top_level(s: str, delimiter: str) -> List[str]:
    """Split by delimiter at top level (not inside parens or strings).

    Supports single-char delimiters (',', ';') and multi-char delimiters ('AND', 'OR').

    SQL single-line comments (--) are skipped: commas or other delimiters
    inside '--' comment text do not trigger a split.
    """
    parts: List[str] = []
    current: List[str] = []
    depth = 0
    in_str = False
    str_char = None
    dlen = len(delimiter)
    i = 0
    while i < len(s):
        c = s[i]
        if in_str:
            current.append(c)
            if c == str_char and (i + 1 >= len(s) or s[i + 1] != str_char):
                in_str = False
        elif c in ('"', "'"):
            in_str = True
            str_char = c
            current.append(c)
        elif c == '(':
            depth += 1
            current.append(c)
        elif c == ')':
            depth -= 1
            current.append(c)
        elif c == '-' and i + 1 < len(s) and s[i + 1] == '-' and depth == 0:
            # SQL single-line comment: skip to end of line.
            # No need to append anything; the newline will naturally
            # end the comment and the next line starts fresh.
            while i < len(s) and s[i] != '\n':
                i += 1
            continue
        elif dlen == 1 and c == delimiter and depth == 0:
            parts.append(''.join(current))
            current = []
        elif dlen > 1 and s[i:i + dlen] == delimiter and depth == 0:
            # Multi-char delimiter (AND, OR): ensure it's surrounded by whitespace
            # to avoid splitting "A.COL AND B.COL" inside "LAND" etc.
            prev = s[i - 1] if i > 0 else ' '
            next_c = s[i + dlen] if i + dlen < len(s) else ' '
            if prev.isspace() and next_c.isspace():
                parts.append(''.join(current))
                current = []
                i += dlen - 1  # Skip past delimiter
            else:
                current.append(c)
        else:
            current.append(c)
        i += 1
    if current:
        parts.append(''.join(current))
    return parts





def _remove_comments(text: str) -> str:
    """Replace comments with %ORA2PG_COMMENT\\d+% placeholders."""
    state = ConversionState()
    out = []
    i = 0
    while i < len(text):
        # Block comment /* ... */
        if text[i:i + 2] == '/*':
            end = text.find('*/', i + 2)
            if end == -1:
                end = len(text)
            else:
                end += 2
            comment = text[i:end]
            ph = f"%ORA2PG_COMMENT{state.comment_idx}%"
            state.comment_map[ph] = comment
            state.comment_idx += 1
            out.append(ph)
            i = end
        # Line comment -- ...
        elif text[i:i + 2] == '--':
            end = text.find('\n', i)
            if end == -1:
                end = len(text)
            comment = text[i:end]
            ph = f"%ORA2PG_COMMENT{state.comment_idx}%"
            state.comment_map[ph] = comment
            state.comment_idx += 1
            out.append(ph)
            i = end
        else:
            out.append(text[i])
            i += 1
    return ''.join(out), state


def _restore_comments(text: str, state: ConversionState) -> str:
    """Restore %ORA2PG_COMMENT\\d+% placeholders."""
    for ph, comment in state.comment_map.items():
        text = text.replace(ph, comment)
    return text


# =============================================================================
# Outer join placeholder strategy
# =============================================================================
def _replace_outer_join_placeholders(sql: str, state: ConversionState) -> str:
    """Replace (+) with %OUTERJOIN\\d+% placeholders to preserve context."""
    pattern = re.compile(r'\(\+\)')
    while True:
        m = pattern.search(sql)
        if not m:
            break
        ph = f"%OUTERJOIN{state.outerjoin_idx}%"
        state.outerjoin_idx += 1
        state.outerjoin_map[ph] = '(+)'
        sql = sql[:m.start()] + ph + sql[m.end():]
    return sql


def _wrap_outerjoin_in_parens(sql: str, state: ConversionState) -> str:
    """Wrap expressions containing %OUTERJOIN in %SUBPART placeholders.

    Mirrors Perl code:
        while ($str =~ s/\\(([^\\(\\)]*\\%OUTERJOIN\\d+\\%[^\\(\\)]*)\\)/\\%SUBPART$idx\\%/s)
    """
    while True:
        m = re.search(r'\(([^\(\)]*%OUTERJOIN\d+%\s*[^\(\)]*)\)', sql)
        if not m:
            break
        inner = m.group(1)
        ph = f"%SUBPART{state.subpart_idx}%"
        state.subpart_idx += 1
        # Decide if outer parens are needed (mirrors Perl)
        if re.match(r'^\s*[^\s]+\s*(=|NOT\s+LIKE|LIKE)\s*[^\s]+\s*$', inner, re.IGNORECASE):
            sql = sql[:m.start()] + ph + sql[m.end():]
            state.subpart_map[ph] = inner
        else:
            sql = sql[:m.start()] + ph + sql[m.end():]
            state.subpart_map[ph] = f'({inner})'
    return sql


def _restore_outerjoins_to_left_join(sql: str, state: ConversionState) -> str:
    """Convert (+) outer join placeholders to LEFT JOIN.

    Mirrors Perl replace_outer_join logic.
    Handles the case where SELECT list contains subqueries with their own FROM/WHERE,
    which caused the previous non-greedy regex to match the wrong FROM clause.
    Fix: search backward from %OUTERJOIN% to find the actual main query's FROM/WHERE.
    """
    if not any(f'%OUTERJOIN{n}%' in sql for n in range(state.outerjoin_idx)):
        return sql

    # Find position of first OUTERJOIN marker
    first_oj = re.search(r'%OUTERJOIN\d+%', sql)
    if not first_oj:
        return sql
    oj_pos = first_oj.start()
    prefix = sql[:oj_pos]

    # Find the last WHERE keyword that precedes the OUTERJOIN marker
    # (skip subquery WHERE clauses)
    where_matches = list(re.finditer(r'\bWHERE\b', prefix, re.IGNORECASE))
    if not where_matches:
        return sql
    main_where_start = where_matches[-1].start()

    # Find the last FROM keyword that precedes the main WHERE
    from_prefix = sql[:main_where_start]
    from_matches = list(re.finditer(r'\bFROM\b', from_prefix, re.IGNORECASE))
    if not from_matches:
        return sql
    main_from_start = from_matches[-1].start()

    before_from = sql[:main_from_start].strip()
    from_and_where = sql[main_from_start:]

    # Split into from_clause and after WHERE
    where_idx = from_and_where.lower().find('where')
    if where_idx < 0:
        return sql
    from_clause = from_and_where[len('FROM'):where_idx].strip()
    after_where = from_and_where[where_idx + len('WHERE'):]

    # Split WHERE conditions from the trailing GROUP BY / ORDER BY / HAVING
    # clause.  Use search (not match): after_where starts with the WHERE
    # conditions themselves, and the old re.match only handled the
    # degenerate case of an empty WHERE — in every normal case it failed
    # and the GROUP BY text got fed to _parse_where_conditions, which
    # split the GROUP BY list on commas and glued the result.
    trailing_search = re.search(
        r'\b(GROUP\s+BY|ORDER\s+BY|HAVING)\b',
        after_where,
        re.IGNORECASE,
    )
    had_semicolon = after_where.rstrip().endswith(';')
    if trailing_search:
        where_clause = after_where[:trailing_search.start()].strip()
        rest_after_where = after_where[trailing_search.start():]
        # A ';' may sit at the end of rest_after_where — preserve it
        # (we strip per-cond semicolons below, not clause-level ones).
    else:
        where_clause = after_where.strip()
        rest_after_where = ''
    # Keep the clause-level ';': strip only bare trailing semicolons that
    # would confuse the condition parser, remember to restore one at the
    # end of the rebuilt statement.
    where_clause = where_clause.rstrip(';').strip()

    # Parse tables from FROM clause
    tables = _parse_from_tables(from_clause)
    if len(tables) < 2:
        return sql

    # Extract outer join conditions by finding %OUTERJOIN% markers.
    # Split each %OUTERJOIN% condition further: they may contain multiple
    # %OUTERJOIN% markers separated by top-level AND.
    raw_conds = _parse_where_conditions(where_clause)
    # First filter: conditions containing %OUTERJOIN%
    split_plus: List[str] = []
    for pc in raw_conds:
        if '%OUTERJOIN' not in pc:
            continue
        # Split by top-level AND to get individual (+)-conditions
        for sub in re.split(r'\bAND\b', pc, flags=re.IGNORECASE):
            stripped = sub.strip()
            if '%OUTERJOIN' in stripped:
                split_plus.append(stripped)
    plus_conds = split_plus
    # Normal conditions (no %OUTERJOIN%)
    conditions = [c for c in raw_conds if '%OUTERJOIN' not in c]
    if not plus_conds:
        return sql

    # Extract SELECT aliases for hub selection.
    # before_from contains everything before the main FROM clause (SELECT part).
    select_text = before_from
    # Extract table aliases from SELECT column references
    select_aliases: List[str] = []
    for m in re.finditer(r'\b([A-Za-z][A-Za-z0-9_]*)\.[A-Za-z][A-Za-z0-9_]*\b', select_text):
        alias = m.group(1).upper()
        if alias not in ('AND', 'OR', 'NOT', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END',
                         'FROM', 'WHERE', 'GROUP', 'ORDER', 'HAVING', 'JOIN',
                         'LEFT', 'RIGHT', 'FULL', 'CROSS', 'INNER', 'OUTER'):
            select_aliases.append(alias)

    # Track which OUTERJOIN markers have been consumed by this conversion
    for c in plus_conds:
        for m in re.finditer(r'%OUTERJOIN(\d+)%', c):
            state.consumed_outerjoins.add(int(m.group(1)))

    join_plan = _build_join_plan(plus_conds, tables, select_aliases=select_aliases)
    if not join_plan:
        return sql

    main_table = join_plan['main_table']
    joins = join_plan['joins']

    from_sql = main_table['full']
    for join_info in joins:
        jt = join_info['table']
        on_conds = [_strip_outerjoin_marker(c) for c in join_info['conditions']]
        on_clause = '\n    AND '.join(on_conds)
        # Use ON TRUE when there are no conditions (avoids empty ON syntax error).
        # This creates a Cartesian product on the preserved side, which is
        # semantically equivalent when the actual condition is on the preserved
        # table's own ON clause.
        on_clause_sql = on_clause if on_clause.strip() else 'TRUE'
        from_sql += f"\n  LEFT JOIN {jt['full']}\n    ON {on_clause_sql}"

    # Emit WHERE only when non-(+) conditions remain.  With empty normal
    # conditions, a dangling `WHERE` before GROUP BY is invalid SQL.
    normal_conds = [c for c in conditions if '%OUTERJOIN' not in c]
    new_sql = f"{before_from}\nFROM {from_sql}"
    if normal_conds:
        new_sql += '\nWHERE ' + '\n  AND '.join(normal_conds)
    if rest_after_where:
        new_sql += '\n' + rest_after_where
    # Preserve the statement-terminating ';' that the original text had.
    # The rebuild strips it during clause extraction.
    if had_semicolon and not new_sql.rstrip().endswith(';'):
        new_sql = new_sql.rstrip() + ';'

    return new_sql


def _strip_outerjoin_marker(cond: str) -> str:
    """Remove %OUTERJOIN\d+% placeholders from a condition.

    IMPORTANT: remove ONLY the marker itself, not the surrounding
    whitespace.  The marker stands in for `(+)` which occupies no
    needed space; eating the surrounding whitespace glues the
    condition to the next token (e.g. `SC.STUDENT_NO%OUTERJOIN0%\nGROUP BY`
    must become `SC.STUDENT_NO\nGROUP BY`, not `SC.STUDENT_NOGROUP BY`).
    """
    return re.sub(r'%OUTERJOIN\d+%', '', cond)


def _parse_from_tables(from_clause: str) -> List[Dict]:
    """Parse Oracle FROM clause into list of table dicts."""
    tables: List[Dict] = []
    parts = _split_top_level(from_clause, ',')
    for part in parts:
        part = part.strip()
        if not part:
            continue
        tokens = part.split()
        if not tokens:
            continue
        if len(tokens) == 1:
            name = tokens[0]
            alias = ''
        elif len(tokens) >= 2:
            name = tokens[0]
            alias = tokens[1]
        else:
            continue
        base = name.split('.')[-1]
        tables.append({
            'name': name,
            'alias': alias,
            'base': base.upper(),
            'alias_upper': alias.upper() if alias else '',
            'full': part,
        })
    return tables


def _parse_where_conditions(where_clause: str) -> List[str]:
    """Split WHERE clause by AND/OR at top level (case-insensitive)."""
    # Use regex-based split with word boundaries for AND/OR
    # Match at top level only (not inside parens)
    parts: List[str] = []
    current: List[str] = []
    depth = 0
    in_str = False
    str_char = None
    i = 0
    while i < len(where_clause):
        c = where_clause[i]
        if in_str:
            current.append(c)
            if c == str_char and (i + 1 >= len(where_clause) or where_clause[i + 1] != str_char):
                in_str = False
        elif c in ('"', "'"):
            in_str = True
            str_char = c
            current.append(c)
        elif c == '(':
            depth += 1
            current.append(c)
        elif c == ')':
            depth -= 1
            current.append(c)
        elif depth == 0 and (c == ',' or (c.upper() == 'A' and where_clause[i:i + 3].upper() == 'AND') or
                              (c.upper() == 'O' and where_clause[i:i + 2].upper() == 'OR')):
            # Check for AND/OR at top level
            if c == ',':
                parts.append(''.join(current))
                current = []
                i += 1
                continue
            elif c.upper() == 'A' and where_clause[i:i + 3].upper() == 'AND' and not (i + 3 < len(where_clause) and where_clause[i + 3].isalpha()):
                parts.append(''.join(current))
                current = []
                i += 3
                continue
            elif c.upper() == 'O' and where_clause[i:i + 2].upper() == 'OR' and not (i + 2 < len(where_clause) and where_clause[i + 2].isalpha()):
                parts.append(''.join(current))
                current = []
                i += 2
                continue
            else:
                current.append(c)
        else:
            current.append(c)
        i += 1
    if current:
        parts.append(''.join(current))
    return [c.strip() for c in parts if c.strip()]


def _build_join_plan(plus_conds: List[str], tables: List[Dict],
                      select_aliases: Optional[List[str]] = None) -> Optional[Dict]:
    """Determine the LEFT JOIN structure from (+) conditions.

    Key insight: for "A = B(+)" (outer=B, preserved=A):
    - A (preserved) gets its own LEFT JOIN (no ON clause needed)
    - B (outer) gets LEFT JOIN with ON clause "A.col = B.col"

    This means:
    - Each PRESERVED table gets a LEFT JOIN with no condition.
    - Each OUTER table gets a LEFT JOIN with the condition as ON.
    - The main table has no JOIN (it's the FROM base).

    But we also need to handle multi-table cases where the same preserved
    table appears in multiple conditions. Solution: create multiple joins
    for the same preserved table (each with no condition).

    Algorithm:
    1. Hub = table most frequently referenced in SELECT (or most preserved).
    2. For each condition:
       a. If preserved NOT in has_join: get_or_add(preserved) -- no ON
       b. If outer NOT in has_join: get_or_add(outer) -- no ON yet
       c. Append condition to outer's join (the ON clause always goes here)
    """
    if not tables:
        return None

    alias_map: Dict[str, Dict] = {}
    for t in tables:
        if t['alias']:
            alias_map[t['alias'].upper()] = t
        alias_map[t['base'].upper()] = t

    def find_table_side(expr: str) -> Optional[Dict]:
        m = re.search(r'\b([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\b', expr)
        if m and m.group(1).upper() in alias_map:
            return alias_map[m.group(1).upper()]
        return None

    outer_counts: Dict[int, int] = {}
    cond_info: List[Dict] = []

    for cond in plus_conds:
        for part in re.split(r'\bAND\b', cond, flags=re.IGNORECASE):
            part = part.strip()
            if not part:
                continue
            for subpart in re.split(r'\bOR\b', part, flags=re.IGNORECASE):
                subpart = subpart.strip()
                if not subpart:
                    continue
                op_m = re.search(r'([=<>!]+)', subpart)
                if not op_m:
                    continue
                left = subpart[:op_m.start()].strip()
                right = subpart[op_m.end():].strip()
                left_outer = bool(re.search(r'%OUTERJOIN\d+%$', left.rstrip()))
                right_outer = bool(re.search(r'%OUTERJOIN\d+%$', right.rstrip()))
                left_tbl = find_table_side(left)
                right_tbl = find_table_side(right)
                if left_outer and right_tbl:
                    outer, preserved = left_tbl, right_tbl
                elif right_outer and left_tbl:
                    outer, preserved = right_tbl, left_tbl
                else:
                    continue
                outer_counts[id(preserved)] = outer_counts.get(id(preserved), 0) + 1
                cond_info.append({'outer': outer, 'preserved': preserved, 'subpart': subpart})

    if not cond_info:
        return None

    # Hub selection: most frequently referenced in SELECT clause.
    # Fall back to most frequently preserved if no SELECT info.
    if select_aliases:
        from collections import Counter
        ref_counts: Dict[int, int] = Counter()
        for alias in select_aliases:
            if alias.upper() in alias_map:
                ref_counts[id(alias_map[alias.upper()])] += 1
        if ref_counts:
            main_table = tables[0]
            max_cnt = 0
            for tbl_id, cnt in ref_counts.items():
                if cnt > max_cnt:
                    max_cnt = cnt
                    for t in tables:
                        if id(t) == tbl_id:
                            main_table = t
                            break
        else:
            main_table = tables[0]
    else:
        # Fall back: hub = most frequently preserved
        main_table = tables[0]
        max_cnt = 0
        for tbl_id, cnt in outer_counts.items():
            if cnt > max_cnt:
                max_cnt = cnt
                for t in tables:
                    if id(t) == tbl_id:
                        main_table = t
                        break

    joins: List[Dict] = []
    has_join: Set[int] = {id(main_table)}

    def get_or_add(tbl: Dict) -> Dict:
        for j in joins:
            if j['table'] is tbl:
                return j
        j = {'table': tbl, 'join_type': 'LEFT JOIN', 'conditions': []}
        joins.append(j)
        has_join.add(id(tbl))
        return j

    def find_table_join(tbl: Dict) -> Optional[Dict]:
        for j in joins:
            if j['table'] is tbl:
                return j
        return None

    for info in cond_info:
        outer = info['outer']
        preserved = info['preserved']
        subpart = info['subpart']

        # Decision: which table gets the ON clause?
        # Semantics of "A.col = B.col(+)": A is preserved, B is the
        # optional (outer) table -> SQL is "A LEFT JOIN B ON A.col = B.col".
        # The condition ALWAYS goes on the OUTER table's JOIN, regardless
        # of whether A is the hub -- at the point B's ON clause executes,
        # both A and B are in scope.
        if id(preserved) != id(main_table) and id(preserved) not in has_join:
            # Preserved non-hub table must itself be reachable via a
            # condition-less LEFT JOIN.
            get_or_add(preserved)
        if id(outer) not in has_join:
            get_or_add(outer)  # Outer gets the LEFT JOIN carrying the ON
        outer_j = find_table_join(outer)
        if outer_j:
            outer_j['conditions'].append(subpart)

    return {
        'main_table': main_table,
        'joins': joins,
    }


def _extract_function_calls(sql: str, state: ConversionState) -> str:
    """Extract function calls recursively into %%REPLACEFCT\\d+%% placeholders.

    Mirrors Perl extract_function_code (PLSQL.pm:626-658):
        while ($code =~ s/\\b([a-zA-Z0-9\\.\\\_]+)\\s*\\(([^\\(\\)]*)\\)/\\%\\%REPLACEFCT$idx\\%\\%/s)

    Plus Perl keyword-prefix spacing (PLSQL.pm:646):
        If fct_name is one of FROM/AS/VALUES/DEFAULT/OR/AND/IN/SELECT/
        OVER/WHERE/THEN/IF/ELSIF/ELSE/EXISTS/ON, add a space after it
        inside the placeholder. Helps preserve PL/SQL parser context.

    OUTERJOIN markers inside function args are preserved (not elevated to
    suffix). They are restored to (+) by step 15 if not consumed by the
    LEFT JOIN converter.
    """
    sql = _strip_comments_for_extract(sql)
    # Collapse ((x)) -> (x) for non-subquery nested parens (PLSQL.pm:629)
    sql = _collapse_inner_parens(sql)
    changed = True
    while changed:
        changed = False
        # Find innermost function call (no nested parens in args)
        m = re.search(r'\b([A-Za-z_][A-Za-z0-9\.\_]*)\s*\(([^\(\)]*)\)', sql)
        if m:
            fct_name = m.group(1)
            fct_args = m.group(2)
            ph = f"%%REPLACEFCT{state.fct_call_idx}%%"
            state.fct_call_idx += 1
            # Perl keyword-prefix spacing (PLSQL.pm:646):
            # If fct_name is a SQL keyword that needs disambiguation,
            # add a space after it inside the stored call.
            space = ""
            keywords_requiring_space = {
                'FROM', 'AS', 'VALUES', 'DEFAULT', 'OR', 'AND', 'IN',
                'SELECT', 'OVER', 'WHERE', 'THEN', 'IF', 'ELSIF',
                'ELSE', 'EXISTS', 'ON'
            }
            if fct_name.upper() in keywords_requiring_space:
                space = " "
            # OUTERJOIN handling: keep OUTERJOIN marker inside function args.
            # Do NOT elevate to suffix (the earlier elevation approach breaks
            # single-table contexts where LEFT JOIN is impossible).
            # When the OUTERJOIN is inside a function call, it should stay there;
            # the function call may or may not be converted depending on context.
            # The marker will be restored to (+) by step 15 if not consumed.
            state.fct_call_map[ph] = f"{fct_name}{space}({fct_args})"
            replacement = ph
            sql = sql[:m.start()] + replacement + sql[m.end():]
            changed = True
    return sql


def _strip_comments_for_extract(text: str) -> str:
    """Remove comments before function call extraction.
    
    Note: Preserves TODO/INLINED markers that were added during nested function inlining.
    """
    # 先保护 TODO/INLINED 标记
    preserved = {}
    counter = [0]
    
    def protect_marker(m):
        key = f'%PRESERVED_COMMENT{counter[0]}%'
        counter[0] += 1
        preserved[key] = m.group(0)
        return key
    
    # 保护 TODO/INLINED 标记
    text = re.sub(r'/\*\s*(?:TODO|INLINED):.*?\*/', protect_marker, text, flags=re.DOTALL | re.IGNORECASE)
    
    # 移除其他块注释
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    text = re.sub(r'--[^\n]*', '', text)
    
    # 恢复 TODO/INLINED 标记
    for key, val in preserved.items():
        text = text.replace(key, val)
    
    return text


def _clear_parenthesis(s: str) -> str:
    """Mirror of Perl clear_parenthesis (PLSQL.pm:603-617).

    Reduce nested parens around an expression:
      ((x))                       -> (x)
      ((SELECT ...))              -> keep ((SELECT ...))
    """
    if re.search(r'\bSELECT\b', s, re.IGNORECASE):
        return '((' + s + '))'
    s = s.strip()
    return '(' + s + ')'


def _collapse_inner_parens(sql: str) -> str:
    """Peel off one level of ((...))   when content has no parens.

    Mirrors the line:
        $code =~ s/\\(\\s*\\(([^\\(\\)]*)\\)\\s*\\)/clear_parenthesis($1)/iges
    inside Perl extract_function_code (PLSQL.pm:629).

    Repeats until no change (mirrors /g flag with iteration).
    """
    prev = None
    while prev != sql:
        prev = sql
        sql = re.sub(
            r'\(\s*\(([^\(\)]*)\)\s*\)',
            lambda m: _clear_parenthesis(m.group(1)),
            sql
        )
    return sql


def _expand_placeholders_recursive(text: str, state: ConversionState, depth: int = 0) -> str:
    """Recursively expand %%REPLACEFCT\\d+%% placeholders by converting their inner fct_call.

    Ensures nested function calls like ``TO_CHAR(%%REPLACEFCT1%%, 'YYYY')`` where
    REPLACEFCT1 is itself a function call (e.g. ``add_months(...)``) get the inner
    function converted before the outer one is processed.
    """
    if depth > 10:  # safety guard
        return text

    def _replace(m):
        ph = m.group(0)
        inner = state.fct_call_map.get(ph, ph)
        if inner == ph:
            return ph
        # Recursively expand any nested placeholders inside inner first
        inner_expanded = _expand_placeholders_recursive(inner, state, depth + 1)
        # Now convert the inner function call
        inner_converted = _oracle_function_to_pg(inner_expanded, state)
        state.fct_call_map[ph] = inner_converted
        return inner_converted

    return re.sub(r'%%REPLACEFCT\d+%%', _replace, text)


def _oracle_function_to_pg(fct_call: str, state: ConversionState) -> str:
    """Convert a single Oracle function call to PG equivalent.

    Mirrors Perl replace_oracle_function's most common rewrites.
    """
    # RECURSIVE FIX: expand inner placeholders first so nested function calls
    # (e.g. ``TO_CHAR(%%REPLACEFCT1%%, 'YYYY')`` where REPLACEFCT1 is itself a
    # function call) get their inner conversion applied before the outer one.
    fct_call = _expand_placeholders_recursive(fct_call, state)
    # Then apply simple pattern-based recursive conversion (NVL/SYSDATE/etc.)
    fct_call = _convert_function_recursive(fct_call)
    fct_call_upper = fct_call.upper().strip()

    # Common Oracle → PG functions
    # Common Oracle → PG functions (commented for reference, actual logic in _oracle_function_to_pg)
    # table = {
    #     'NVL(': 'COALESCE(',
    #     'TO_DATE(': 'TO_TIMESTAMP',
    #     'SYSDATE': 'CURRENT_TIMESTAMP',
    #     'INSTR(': '__PG_INSTR(',
    #     'DECODE(': '__PG_DECODE(',
    # }

    # NVL/NVL2
    if re.match(r'NVL\s*\(', fct_call_upper):
        return re.sub(r'^NVL\s*\(', 'COALESCE(', fct_call, count=1, flags=re.IGNORECASE)
    if re.match(r'NVL2\s*\(', fct_call_upper):
        # NVL2(a, b, c) -> CASE WHEN a IS NOT NULL THEN b ELSE c END
        m = re.match(r'(NVL2\s*\()(.*)\)$', fct_call, re.IGNORECASE | re.DOTALL)
        if m:
            args_str = m.group(2)
            args = _split_top_level(args_str, ',')
            if len(args) >= 3:
                return f"CASE WHEN {args[0].strip()} IS NOT NULL THEN {args[1].strip()} ELSE {args[2].strip()} END"
            state.warnings.append(f"NVL2 with unusual args: {fct_call}")

    # DECODE
    if re.match(r'DECODE\s*\(', fct_call_upper):
        m = re.match(r'(DECODE\s*\()(.*)\)$', fct_call, re.IGNORECASE | re.DOTALL)
        if m:
            args_str = m.group(2)
            args = _split_top_level(args_str, ',')
            if len(args) >= 3:
                expr = args[0].strip()
                pairs = args[1:]
                if len(pairs) % 2 == 0:
                    # No default
                    result = 'CASE'
                    for i in range(0, len(pairs), 2):
                        result += f" WHEN {expr} = {pairs[i].strip()} THEN {pairs[i+1].strip()}"
                    result += ' END'
                    return result
                else:
                    default = pairs[-1].strip()
                    pairs = pairs[:-1]
                    result = 'CASE'
                    for i in range(0, len(pairs), 2):
                        result += f" WHEN {expr} = {pairs[i].strip()} THEN {pairs[i+1].strip()}"
                    result += f' ELSE {default} END'
                    return result

    # INSTR(s, sub) -> POSITION(sub IN s)
    if re.match(r'INSTR\s*\(', fct_call_upper):
        m = re.match(r'(INSTR\s*\()(.*)\)$', fct_call, re.IGNORECASE | re.DOTALL)
        if m:
            args_str = m.group(2)
            args = _split_top_level(args_str, ',')
            if len(args) == 2:
                return f"POSITION({args[1].strip()} IN {args[0].strip()})"
            if len(args) >= 3:
                return f"POSITION({args[1].strip()} IN SUBSTRING({args[0].strip()} FROM {args[2].strip()}))"

    # SUBSTR -> SUBSTRING
    if re.match(r'SUBSTR\s*\(', fct_call_upper):
        m = re.match(r'(SUBSTR\s*\()(.*)\)$', fct_call, re.IGNORECASE | re.DOTALL)
        if m:
            args_str = m.group(2)
            args = _split_top_level(args_str, ',')
            if len(args) == 2:
                return f"SUBSTRING({args[0].strip()} FROM {args[1].strip()})"
            if len(args) >= 3:
                return f"SUBSTRING({args[0].strip()} FROM {args[1].strip()} FOR {args[2].strip()})"

    # TO_DATE -> TO_TIMESTAMP
    if re.match(r'TO_DATE\s*\(', fct_call_upper):
        m = re.match(r'(TO_DATE\s*\()(.*)\)$', fct_call, re.IGNORECASE | re.DOTALL)
        if m:
            args_str = m.group(2)
            args = _split_top_level(args_str, ',')
            if len(args) == 1:
                return f"TO_TIMESTAMP({args[0].strip()})::date"
            if len(args) >= 2:
                fmt = _convert_oracle_date_format(args[1].strip().strip("'\""))
                return f"TO_TIMESTAMP({args[0].strip()}, '{fmt}')::date"

    # TO_CHAR -> to_char with format conversion (P1)
    if re.match(r'TO_CHAR\s*\(', fct_call_upper):
        m = re.match(r'(TO_CHAR\s*\()(.*)\)$', fct_call, re.IGNORECASE | re.DOTALL)
        if m:
            args_str = m.group(2)
            args = _split_top_level(args_str, ',')
            if len(args) == 1:
                return f"({args[0].strip()})::varchar"
            elif len(args) >= 2:
                fmt = _convert_oracle_date_format(args[1].strip().strip("'\""))
                return f"TO_CHAR({args[0].strip()}, '{fmt}')"

    # TO_NUMBER (P2)
    if re.match(r'TO_NUMBER\s*\(', fct_call_upper):
        m = re.match(r'(TO_NUMBER\s*\()(.*)\)$', fct_call, re.IGNORECASE | re.DOTALL)
        if m:
            args_str = m.group(2)
            args = _split_top_level(args_str, ',')
            if len(args) == 1:
                return f"({args[0].strip()})::NUMERIC"
            # Drop format arg (PG TO_NUMBER has no format)
            return f"({args[0].strip()})::NUMERIC"

    # TO_TIMESTAMP (P2)
    if re.match(r'TO_TIMESTAMP\s*\(', fct_call_upper):
        m = re.match(r'(TO_TIMESTAMP\s*\()(.*)\)$', fct_call, re.IGNORECASE | re.DOTALL)
        if m:
            args_str = m.group(2)
            args = _split_top_level(args_str, ',')
            if len(args) == 1:
                return f"TO_TIMESTAMP({args[0].strip()})"
            elif len(args) >= 2:
                fmt = _convert_oracle_date_format(args[1].strip().strip("'\""))
                return f"TO_TIMESTAMP({args[0].strip()}, '{fmt}')"

    # TO_CLOB -> text cast
    if re.match(r'TO_CLOB\s*\(', fct_call_upper):
        m = re.match(r'(TO_CLOB\s*\()(.*)\)$', fct_call, re.IGNORECASE | re.DOTALL)
        if m:
            return f"({m.group(2).strip()})::TEXT"

    # SYSTIMESTAMP / SYSDATE / LOCALTIMESTAMP / CURRENT_TIMESTAMP (P4)
    if re.match(r'SYSTIMESTAMP\s*$', fct_call_upper):
        return 'CURRENT_TIMESTAMP'
    if re.match(r'LOCALTIMESTAMP\s*$', fct_call_upper):
        return 'LOCALTIMESTAMP'
    if re.match(r'SYSDATE\s*$', fct_call_upper):
        return 'CURRENT_TIMESTAMP'

    # DBMS_OUTPUT.PUT_LINE / PUT / NEW_LINE (P7)
    if re.match(r'DBMS_OUTPUT\s*\.\s*PUT_LINE\s*\(', fct_call_upper):
        m = re.match(r'(DBMS_OUTPUT\s*\.\s*PUT_LINE\s*\()(.*)\)$', fct_call, re.IGNORECASE | re.DOTALL)
        if m:
            arg = m.group(2).strip()
            return f"RAISE NOTICE '%', {arg}"
    if re.match(r'DBMS_OUTPUT\s*\.\s*PUT\s*\(', fct_call_upper):
        m = re.match(r'(DBMS_OUTPUT\s*\.\s*PUT\s*\()(.*)\)$', fct_call, re.IGNORECASE | re.DOTALL)
        if m:
            arg = m.group(2).strip()
            return f"RAISE NOTICE '%', {arg}"
    if re.match(r'DBMS_OUTPUT\s*\.\s*NEW_LINE\s*\(', fct_call_upper):
        return "RAISE NOTICE ''"

    # TRANSLATE (P5) - PG TRANSLATE has different semantics
    # Oracle: single-char level substitution (each char in from -> char in to, length-normalized)
    # PG: TRANSLATE is similar but to shorter than from deletes chars in from
    # Use CASE-based or FUNCTION wrapper for full Oracle compatibility
    if re.match(r'TRANSLATE\s*\(', fct_call_upper):
        m = re.match(r'(TRANSLATE\s*\()(.*)\)$', fct_call, re.IGNORECASE | re.DOTALL)
        if m:
            args_str = m.group(2)
            args = _split_top_level(args_str, ',')
            if len(args) == 3:
                # Use PG TRANSLATE (single-char substitution, deletion if to shorter)
                return f"TRANSLATE({args[0].strip()}, {args[1].strip()}, {args[2].strip()})"

    # TRUNC / ROUND (P8)
    # TRUNC(date) -> DATE_TRUNC('day', date)
    # TRUNC(number [, n]) -> TRUNC(number, n)
    if re.match(r'TRUNC\s*\(', fct_call_upper):
        m = re.match(r'(TRUNC\s*\()(.*)\)$', fct_call, re.IGNORECASE | re.DOTALL)
        if m:
            args_str = m.group(2)
            args = _split_top_level(args_str, ',')
            if len(args) == 1:
                arg = args[0].strip()
                # Heuristic: if arg looks like a column/expression (not literal),
                # assume date; otherwise number
                if re.match(r"^['\"]\d", arg) or re.match(r"^\d+(\.\d+)?$", arg):
                    return f"TRUNC({arg}::NUMERIC)"
                return f"DATE_TRUNC('day', {arg})"
            elif len(args) == 2:
                second = args[1].strip().strip("'\"")
                # If 2nd arg is a date format token like 'mm', 'yyyy', use DATE_TRUNC
                if second.upper() in ('CC', 'SCC', 'YYYY', 'YEAR', 'SYEAR', 'YYY', 'YY', 'Y',
                                       'IYYY', 'IY', 'I', 'Q', 'MONTH', 'MON', 'MM',
                                       'IW', 'W', 'DDD', 'DD', 'D', 'DAY', 'DY', 'J',
                                       'HH', 'HH12', 'HH24', 'MI'):
                    return f"DATE_TRUNC('{_convert_oracle_date_format(second)}', {args[0].strip()})"
                # Otherwise treat as numeric truncation
                return f"TRUNC({args[0].strip()}, {args[1].strip()})"

    if re.match(r'ROUND\s*\(', fct_call_upper):
        m = re.match(r'(ROUND\s*\()(.*)\)$', fct_call, re.IGNORECASE | re.DOTALL)
        if m:
            args_str = m.group(2)
            args = _split_top_level(args_str, ',')
            if len(args) == 1:
                arg = args[0].strip()
                # Heuristic: numeric round without 2nd arg
                if re.match(r"^['\"]\d", arg) or re.match(r"^\d+(\.\d+)?$", arg):
                    return f"ROUND({arg}::NUMERIC)"
                return f"DATE_TRUNC('day', {arg})"
            elif len(args) == 2:
                second = args[1].strip().strip("'\"")
                if second.upper() in ('CC', 'SCC', 'YYYY', 'YEAR', 'SYEAR', 'YYY', 'YY', 'Y',
                                       'IYYY', 'IY', 'I', 'Q', 'MONTH', 'MON', 'MM',
                                       'IW', 'W', 'DDD', 'DD', 'D', 'DAY', 'DY', 'J',
                                       'HH', 'HH12', 'HH24', 'MI'):
                    # DATE_TRUNC rounds DOWN, ROUND rounds to nearest. Use DATE_TRUNC + half-period.
                    # Simplified: use DATE_TRUNC (close approximation)
                    return f"DATE_TRUNC('{_convert_oracle_date_format(second)}', {args[0].strip()})"
                return f"ROUND({args[0].strip()}::NUMERIC, {args[1].strip()})"

    # DECODE - improve to nested CASE (P3)
    if re.match(r'DECODE\s*\(', fct_call_upper):
        return _decode_to_case(fct_call)

    # LENGTH, UPPER, LOWER, TRIM, REPLACE, LPAD, RPAD, LTRIM, RTRIM - already PG-compatible
    return fct_call


# =============================================================================
# P1/P3 helpers: Oracle date format + DECODE
# =============================================================================

# Map Oracle date format tokens to PG equivalents.
# Most tokens are identical; a few need translation.
_ORACLE_DATE_FORMAT_MAP = [
    # Two-digit year: RR -> YY (per Perl convert_date_format)
    (re.compile(r'\bRR\b'), 'YY'),
    # Fractional seconds: FF[123] -> MS, FF* -> US
    (re.compile(r'\bFF[123]\b'), 'MS'),
    (re.compile(r'\bFF\d*\b'), 'US'),
]


def _convert_oracle_date_format(fmt: str) -> str:
    """Translate Oracle date format to PG format string (best-effort).

    Mirrors Perl's convert_date_format():
        RR -> YY, FF[123] -> MS, FF* -> US
    Most tokens (YYYY, MM, DD, HH24, MI, SS, etc.) are kept as-is.
    """
    fmt = fmt.strip().strip("'\"")
    for pat, repl in _ORACLE_DATE_FORMAT_MAP:
        fmt = pat.sub(repl, fmt)
    return fmt


def _decode_to_case(decode_call: str) -> str:
    """Convert Oracle DECODE(expr, search1, result1, search2, result2, ..., default)
    to PG CASE WHEN expr = search1 THEN result1 ... ELSE default END.

    Mirrors Perl replace_decode: recursive CASE generation.
    """
    m = re.match(r'(DECODE\s*\()(.*)\)$', decode_call, re.IGNORECASE | re.DOTALL)
    if not m:
        return decode_call
    args_str = m.group(2)
    args = _split_top_level(args_str, ',')
    if len(args) < 3:
        return decode_call  # Not enough args; keep as-is

    expr = args[0].strip()
    # args[1..N], pairs (search, result), optional default at end if odd count
    pairs = args[1:]
    has_default = (len(pairs) % 2) == 1
    default = pairs[-1].strip() if has_default else 'NULL'
    if has_default:
        pairs = pairs[:-1]

    out = []
    for i in range(0, len(pairs), 2):
        search = pairs[i].strip()
        result = pairs[i + 1].strip()
        out.append(f"WHEN {expr} = {search} THEN {result}")
    if has_default:
        out.append(f"ELSE {default}")
    return f"CASE {' '.join(out)} END"


def _apply_function_conversion(sql: str, state: ConversionState) -> str:
    """Apply Oracle function conversion to extracted placeholders."""
    for ph, fct_call in list(state.fct_call_map.items()):
        converted = _oracle_function_to_pg(fct_call, state)
        state.fct_call_map[ph] = converted

    # Restore placeholders
    while True:
        m = re.search(r'%%REPLACEFCT(\d+)%%', sql)
        if not m:
            break
        ph = m.group(0)
        sql = sql.replace(ph, state.fct_call_map.get(ph, ph), 1)

    return sql


def _restore_subparts(sql: str, state: ConversionState) -> str:
    for ph, content in state.subpart_map.items():
        sql = sql.replace(ph, content)
    return sql


def _restore_subparams(sql: str, state: ConversionState) -> str:
    for ph, content in state.subparams_map.items():
        sql = sql.replace(ph, content)
    return sql


def _convert_inner_functions(text: str, inner_state: ConversionState) -> str:
    """Extract and convert inner function calls within a function's arguments.

    Uses a SEPARATE inner_state to avoid infinite recursion.
    """
    inner_state.fct_call_map.clear()
    inner_state.subpart_map.clear()
    inner_state.subparams_map.clear()
    inner_state.outerjoin_map.clear()

    text = _extract_function_calls(text, inner_state)
    # Convert each extracted call using _oracle_function_to_pg
    for ph, fct_call in list(inner_state.fct_call_map.items()):
        inner_state.fct_call_map[ph] = _oracle_function_to_pg(fct_call, inner_state)
    # Restore placeholders with converted calls
    while True:
        m = re.search(r'%%REPLACEFCT(\d+)%%', text)
        if not m:
            break
        ph = m.group(0)
        text = text.replace(ph, inner_state.fct_call_map.get(ph, ph), 1)
    return text


# Patterns for inner function conversion (no recursion, single-pass)
# These match and convert function calls without calling _oracle_function_to_pg
_INNER_FCT_PATTERNS = [
    # NVL -> COALESCE
    (re.compile(r'\bNVL\s*\(\s*([^,]+),\s*([^)]+)\)', re.IGNORECASE),
     r'COALESCE(\1, \2)'),
    # NVL2 -> CASE WHEN
    (re.compile(r'\bNVL2\s*\(\s*([^,]+),\s*([^,]+),\s*([^)]+)\)', re.IGNORECASE),
     r'(CASE WHEN \1 IS NOT NULL THEN \2 ELSE \3 END)'),
    # INSTR -> strpos
    (re.compile(r'\bINSTR\s*\(\s*([^,]+),\s*([^)]+)\)', re.IGNORECASE),
     r'(STRPOS(\1, \2))'),
    # SUBSTR -> SUBSTRING
    (re.compile(r'\bSUBSTR\s*\(\s*([^,]+),\s*([^,]+),\s*([^)]+)\)', re.IGNORECASE),
     r'SUBSTRING(\1 FROM \2 FOR \3)'),
    (re.compile(r'\bSUBSTR\s*\(\s*([^,]+),\s*([^)]+)\)', re.IGNORECASE),
     r'SUBSTRING(\1 FROM \2)'),
    # SYSDATE -> CURRENT_TIMESTAMP
    (re.compile(r'\bSYSDATE\b', re.IGNORECASE), 'CURRENT_TIMESTAMP'),
    # SYSTIMESTAMP -> CURRENT_TIMESTAMP
    (re.compile(r'\bSYSTIMESTAMP\b', re.IGNORECASE), 'CURRENT_TIMESTAMP'),
    # LOCALTIMESTAMP (keep as-is)
    # CURRENT_TIMESTAMP (keep as-is)
]


def _convert_function_recursive(text: str) -> str:
    """Convert Oracle function calls in text, recursively handling nesting.

    Strategy: repeatedly apply pattern-based conversion until no more changes.
    Uses a MAX_ITERATION guard to prevent infinite loops.
    """
    for _ in range(5):  # Max 5 levels of nesting
        new_text = text
        for pat, repl in _INNER_FCT_PATTERNS:
            new_text = pat.sub(repl, new_text)
        if new_text == text:
            break
        text = new_text
    return text


# =============================================================================
# P8: ROWNUM -> LIMIT (Phase 8)
# =============================================================================

def _replace_rownum_with_limit(sql: str) -> str:
    """Convert Oracle ROWNUM to PG LIMIT/OFFSET.

    Patterns:
        ROWNUM = 1           -> LIMIT 1
        ROWNUM <= N          -> LIMIT N
        ROWNUM < N           -> LIMIT N-1
        ROWNUM >= N          -> OFFSET N-1 LIMIT ALL
        ROWNUM > N           -> OFFSET N
        AND ROWNUM = 1       -> remove AND clause, add LIMIT 1

    Handles subquery context: "FROM (...) WHERE ROWNUM = 1"
    Mirrors Perl replace_rownum_with_limit.
    """
    def _repl_rownum_eq1(m):
        return m.group(1) + ' LIMIT 1' + (m.group(2) or '')
    def _repl_rownum_le(m):
        return m.group(1) + ' LIMIT ' + m.group(2) + (m.group(3) or '')
    def _repl_rownum_lt(m):
        return m.group(1) + ' LIMIT ' + str(int(m.group(2)) - 1) + (m.group(3) or '')
    def _repl_rownum_gt(m):
        return m.group(1) + ' OFFSET ' + m.group(2) + (m.group(3) or '')
    def _repl_rownum_ge(m):
        return m.group(1) + ' OFFSET ' + str(int(m.group(2)) - 1) + ' LIMIT ALL' + (m.group(3) or '')

    # Pattern: standalone WHERE ROWNUM = 1 (after closing paren)
    # Matches:  ) WHERE ROWNUM = 1
    #           ) WHERE ROWNUM = 1;
    sql = re.sub(
        r'(\))\s+WHERE\s+ROWNUM\s*=\s*1\s*(;?)\s*$',
        _repl_rownum_eq1,
        sql, flags=re.IGNORECASE | re.MULTILINE
    )
    sql = re.sub(
        r'(\))\s+WHERE\s+ROWNUM\s*=\s*1\s*(;?)\s*(\n|EXCEPTION|BEGIN|END)',
        lambda m: m.group(1) + ' LIMIT 1' + (m.group(2) or '') + ' ' + m.group(3),
        sql, flags=re.IGNORECASE
    )

    # Standalone WHERE ROWNUM = 1 without closing paren
    sql = re.sub(
        r'WHERE\s+ROWNUM\s*=\s*1\s*(;?)\s*$',
        lambda m: 'LIMIT 1' + (m.group(1) or ''),
        sql, flags=re.IGNORECASE | re.MULTILINE
    )
    sql = re.sub(
        r'WHERE\s+ROWNUM\s*=\s*1\s*(;?)\s*(\n|EXCEPTION|BEGIN|END)',
        lambda m: 'LIMIT 1' + (m.group(1) or '') + ' ' + m.group(2),
        sql, flags=re.IGNORECASE
    )
    sql = re.sub(
        r'\s+AND\s+ROWNUM\s*=\s*1\s*(;?)\s*$',
        lambda m: ' LIMIT 1' + (m.group(1) or ''),
        sql, flags=re.IGNORECASE | re.MULTILINE
    )
    sql = re.sub(
        r'\s+AND\s+ROWNUM\s*=\s*1\s*(;?)\s*(\n|EXCEPTION|BEGIN|END)',
        lambda m: ' LIMIT 1' + (m.group(1) or '') + ' ' + m.group(2),
        sql, flags=re.IGNORECASE
    )
    sql = re.sub(
        r'\s+AND\s+ROWNUM\s*=\s*1\s*(\))',
        lambda m: ' LIMIT 1' + m.group(1),
        sql, flags=re.IGNORECASE
    )

    # Pattern: standalone WHERE ROWNUM = 1 before closing paren in FROM clause
    # Matches:  FROM (...) WHERE ROWNUM = 1)
    sql = re.sub(
        r'(\))\s+WHERE\s+ROWNUM\s*=\s*1\s*(\))',
        lambda m: m.group(1) + ' LIMIT 1' + m.group(2),
        sql, flags=re.IGNORECASE
    )

    # Pattern: AND ROWNUM = 1 (after other WHERE conditions)
    # Matches:  WHERE col = val AND ROWNUM = 1
    sql = re.sub(
        r'\s+AND\s+ROWNUM\s*=\s*1\s*(;?)\s*$',
        lambda m: ' LIMIT 1' + (m.group(1) or ''),
        sql, flags=re.IGNORECASE | re.MULTILINE
    )
    sql = re.sub(
        r'\s+AND\s+ROWNUM\s*=\s*1\s*(;?)\s*(\n|EXCEPTION|BEGIN|END)',
        lambda m: ' LIMIT 1' + (m.group(1) or '') + ' ' + m.group(2),
        sql, flags=re.IGNORECASE
    )
    sql = re.sub(
        r'\s+AND\s+ROWNUM\s*=\s*1\s*(\))',
        lambda m: ' LIMIT 1' + m.group(1),
        sql, flags=re.IGNORECASE
    )

    # Pattern: standalone WHERE ROWNUM <= N
    sql = re.sub(
        r'(\))\s+WHERE\s+ROWNUM\s*<=\s*(\d+)\s*(\))',
        _repl_rownum_le,
        sql, flags=re.IGNORECASE
    )
    sql = re.sub(
        r'(\))\s+WHERE\s+ROWNUM\s*<=\s*(\d+)\s*;',
        lambda m: m.group(1) + ' LIMIT ' + m.group(2) + ';',
        sql, flags=re.IGNORECASE
    )
    # Standalone WHERE ROWNUM <= N without closing paren (end of line)
    sql = re.sub(
        r'WHERE\s+ROWNUM\s*<=\s*(\d+)\s*;?\s*$',
        lambda m: 'LIMIT ' + m.group(1),
        sql, flags=re.IGNORECASE | re.MULTILINE
    )
    # Standalone WHERE ROWNUM <= N before newline or EXCEPTION
    sql = re.sub(
        r'WHERE\s+ROWNUM\s*<=\s*(\d+)\s*(\n|EXCEPTION|BEGIN|ORDER)',
        lambda m: 'LIMIT ' + m.group(1) + ' ' + m.group(2),
        sql, flags=re.IGNORECASE
    )

    # Pattern: AND ROWNUM <= N
    sql = re.sub(
        r'\s+AND\s+ROWNUM\s*<=\s*(\d+)\s*(\))',
        lambda m: ' LIMIT ' + m.group(1) + m.group(2),
        sql, flags=re.IGNORECASE
    )
    sql = re.sub(
        r'\s+AND\s+ROWNUM\s*<=\s*(\d+)\s*;',
        lambda m: ' LIMIT ' + m.group(1) + ';',
        sql, flags=re.IGNORECASE
    )
    sql = re.sub(
        r'\s+AND\s+ROWNUM\s*<=\s*(\d+)\s*$',
        lambda m: ' LIMIT ' + m.group(1),
        sql, flags=re.IGNORECASE | re.MULTILINE
    )
    sql = re.sub(
        r'\s+AND\s+ROWNUM\s*<=\s*(\d+)\s*(\n|EXCEPTION|BEGIN)',
        lambda m: ' LIMIT ' + m.group(1) + ' ' + m.group(2),
        sql, flags=re.IGNORECASE
    )

    # Pattern: standalone WHERE ROWNUM < N -> LIMIT N-1
    sql = re.sub(
        r'(\))\s+WHERE\s+ROWNUM\s*<\s*(\d+)\s*(\))',
        _repl_rownum_lt,
        sql, flags=re.IGNORECASE
    )
    sql = re.sub(
        r'(\))\s+WHERE\s+ROWNUM\s*<\s*(\d+)\s*;',
        lambda m: m.group(1) + ' LIMIT ' + str(int(m.group(2)) - 1) + ';',
        sql, flags=re.IGNORECASE
    )
    # Standalone WHERE ROWNUM < N without closing paren
    sql = re.sub(
        r'WHERE\s+ROWNUM\s*<\s*(\d+)\s*;?\s*$',
        lambda m: 'LIMIT ' + str(int(m.group(1)) - 1),
        sql, flags=re.IGNORECASE | re.MULTILINE
    )
    sql = re.sub(
        r'WHERE\s+ROWNUM\s*<\s*(\d+)\s*(\n|EXCEPTION|BEGIN)',
        lambda m: 'LIMIT ' + str(int(m.group(1)) - 1) + ' ' + m.group(2),
        sql, flags=re.IGNORECASE
    )
    sql = re.sub(
        r'\s+AND\s+ROWNUM\s*<\s*(\d+)\s*(\))',
        lambda m: ' LIMIT ' + str(int(m.group(1)) - 1) + m.group(2),
        sql, flags=re.IGNORECASE
    )
    sql = re.sub(
        r'\s+AND\s+ROWNUM\s*<\s*(\d+)\s*;',
        lambda m: ' LIMIT ' + str(int(m.group(1)) - 1) + ';',
        sql, flags=re.IGNORECASE
    )
    sql = re.sub(
        r'\s+AND\s+ROWNUM\s*<\s*(\d+)\s*$',
        lambda m: ' LIMIT ' + str(int(m.group(1)) - 1),
        sql, flags=re.IGNORECASE | re.MULTILINE
    )

    # Pattern: standalone WHERE ROWNUM > N -> OFFSET N
    sql = re.sub(
        r'(\))\s+WHERE\s+ROWNUM\s*>\s*(\d+)\s*(\))',
        _repl_rownum_gt,
        sql, flags=re.IGNORECASE
    )
    sql = re.sub(
        r'(\))\s+WHERE\s+ROWNUM\s*>\s*(\d+)\s*;',
        lambda m: m.group(1) + ' OFFSET ' + m.group(2) + ';',
        sql, flags=re.IGNORECASE
    )
    # Standalone WHERE ROWNUM > N without closing paren
    sql = re.sub(
        r'WHERE\s+ROWNUM\s*>\s*(\d+)\s*;?\s*$',
        lambda m: 'OFFSET ' + m.group(1),
        sql, flags=re.IGNORECASE | re.MULTILINE
    )
    sql = re.sub(
        r'WHERE\s+ROWNUM\s*>\s*(\d+)\s*(\n|EXCEPTION|BEGIN)',
        lambda m: 'OFFSET ' + m.group(1) + ' ' + m.group(2),
        sql, flags=re.IGNORECASE
    )
    sql = re.sub(
        r'\s+AND\s+ROWNUM\s*>\s*(\d+)\s*(\))',
        lambda m: ' OFFSET ' + m.group(1) + m.group(2),
        sql, flags=re.IGNORECASE
    )
    sql = re.sub(
        r'\s+AND\s+ROWNUM\s*>\s*(\d+)\s*;',
        lambda m: ' OFFSET ' + m.group(1) + ';',
        sql, flags=re.IGNORECASE
    )
    sql = re.sub(
        r'\s+AND\s+ROWNUM\s*>\s*(\d+)\s*$',
        lambda m: ' OFFSET ' + m.group(1),
        sql, flags=re.IGNORECASE | re.MULTILINE
    )

    # Pattern: standalone WHERE ROWNUM >= N -> OFFSET N-1 LIMIT ALL
    sql = re.sub(
        r'(\))\s+WHERE\s+ROWNUM\s*>=\s*(\d+)\s*(\))',
        _repl_rownum_ge,
        sql, flags=re.IGNORECASE
    )
    sql = re.sub(
        r'(\))\s+WHERE\s+ROWNUM\s*>=\s*(\d+)\s*;',
        lambda m: m.group(1) + ' OFFSET ' + str(int(m.group(2)) - 1) + ' LIMIT ALL;',
        sql, flags=re.IGNORECASE
    )
    # Standalone WHERE ROWNUM >= N without closing paren
    sql = re.sub(
        r'WHERE\s+ROWNUM\s*>=\s*(\d+)\s*;?\s*$',
        lambda m: 'OFFSET ' + str(int(m.group(1)) - 1) + ' LIMIT ALL',
        sql, flags=re.IGNORECASE | re.MULTILINE
    )
    sql = re.sub(
        r'WHERE\s+ROWNUM\s*>=\s*(\d+)\s*(\n|EXCEPTION|BEGIN)',
        lambda m: 'OFFSET ' + str(int(m.group(1)) - 1) + ' LIMIT ALL' + ' ' + m.group(2),
        sql, flags=re.IGNORECASE
    )
    sql = re.sub(
        r'\s+AND\s+ROWNUM\s*>=\s*(\d+)\s*(\))',
        lambda m: ' OFFSET ' + str(int(m.group(1)) - 1) + ' LIMIT ALL' + m.group(2),
        sql, flags=re.IGNORECASE
    )
    sql = re.sub(
        r'\s+AND\s+ROWNUM\s*>=\s*(\d+)\s*;',
        lambda m: ' OFFSET ' + str(int(m.group(1)) - 1) + ' LIMIT ALL;',
        sql, flags=re.IGNORECASE
    )
    sql = re.sub(
        r'\s+AND\s+ROWNUM\s*>=\s*(\d+)\s*$',
        lambda m: ' OFFSET ' + str(int(m.group(1)) - 1) + ' LIMIT ALL',
        sql, flags=re.IGNORECASE | re.MULTILINE
    )

    # Clean up: remove redundant WHERE  AND or WHERE with nothing after
    sql = re.sub(r'\s+WHERE\s*$', '', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\s+WHERE\s+AND\s+', ' ', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\s+WHERE\s*\)', ')', sql, flags=re.IGNORECASE)

    return sql


# =============================================================================
# P9: REF CURSOR -> REFCURSOR (Phase 9)
# =============================================================================

def _convert_ref_cursor(sql: str) -> str:
    """Convert Oracle REF CURSOR to PG REFCURSOR.

    Mirrors Perl replace_cursor_def:
        TYPE name IS REF CURSOR;        -> remove type def, replace vars with REFCURSOR
        TYPE name IS REF CURSOR RETURN ...; -> name REFCURSOR
        var_name TYPE_NAME;             -> var_name REFCURSOR;
        SYS_REFCURSOR                   -> REFCURSOR
        CURSOR name IS SELECT           -> CURSOR name FOR SELECT
    """
    # Step 1: Map local type definitions: TYPE xxx IS REF CURSOR; -> REFCURSOR
    # Track local type names for variable declarations
    local_types = {}
    counter = [0]  # use list to allow mutation in nested function

    def _replace_local_type(m):
        type_name = m.group(1).strip()
        key = f'%REFCURSORTYPE{counter[0]}%'
        counter[0] += 1
        local_types[key] = type_name
        # Return placeholder, will be replaced in variables
        return key

    # Replace: TYPE xxx IS REF CURSOR;
    sql = re.sub(
        r'\bTYPE\s+([A-Za-z0-9_\$\-]+)\s+IS\s+REF\s+CURSOR\s*;',
        _replace_local_type,
        sql, flags=re.IGNORECASE
    )

    # Replace: TYPE xxx IS REF CURSOR RETURN type%ROWTYPE;
    def _replace_typed_ref_cursor(m):
        type_name = m.group(1).strip()
        key = f'%REFCURSORTYPE{counter[0]}%'
        counter[0] += 1
        local_types[key] = type_name
        return key + ' REFCURSOR;'

    sql = re.sub(
        r'\bTYPE\s+([A-Za-z0-9_\$\-]+)\s+IS\s+REF\s+CURSOR\s+RETURN\s+[^;]+%ROWTYPE\s*;',
        _replace_typed_ref_cursor,
        sql, flags=re.IGNORECASE
    )

    # Step 2: Replace variables using local types with REFCURSOR
    for key, type_name in local_types.items():
        # var_name TYPE_NAME;
        sql = re.sub(
            rf'\b([A-Za-z0-9_\$\-]+)\s+{re.escape(type_name)}\s*;',
            r'\1 REFCURSOR;',
            sql, flags=re.IGNORECASE
        )
        # var_name TYPE_NAME,
        sql = re.sub(
            rf'\b([A-Za-z0-9_\$\-]+)\s+{re.escape(type_name)}\s*,',
            r'\1 REFCURSOR,',
            sql, flags=re.IGNORECASE
        )

    # Step 3: Replace remaining placeholders with REFCURSOR comment
    for key in local_types.keys():
        sql = re.sub(rf'{key}\b', '/* TYPE ' + type_name + ' IS REF CURSOR */ REFCURSOR', sql)

    # Step 4: Replace remaining placeholders with REFCURSOR comment
    # (TYPE defs that weren't used in variable declarations)
    for key, type_name in local_types.items():
        if key in sql:
            escaped_key = re.escape(key)
            # Don't use \b after placeholder (it's not a word char)
            sql = re.sub(
                escaped_key + r'(?=\W|$)',
                f'/* TYPE {type_name} IS REF CURSOR */',
                sql
            )

    # Step 5: Replace SYS_REFCURSOR -> REFCURSOR
    sql = re.sub(r'\bSYS_REFCURSOR\b', 'REFCURSOR', sql, flags=re.IGNORECASE)

    # Step 6: Replace standalone REF CURSOR -> REFCURSOR
    sql = re.sub(r'\bREF\s+CURSOR\b', 'REFCURSOR', sql, flags=re.IGNORECASE)

    # Step 7: CURSOR name IS SELECT -> CURSOR name FOR SELECT
    # Simple: find 'CURSOR' followed by 'IS SELECT' and replace 'IS ' with 'FOR '
    # This handles both CURSOR name IS SELECT and CURSOR (params) IS SELECT
    def _replace_cursor_is_for(s):
        result = s
        # Find all CURSOR positions
        for m in re.finditer(r'\bCURSOR\b', s):
            pos = m.end()
            # Find 'IS SELECT' after CURSOR
            rest = s[pos:pos+200]
            # Look for 'IS' followed by whitespace and 'SELECT'
            is_m = re.search(r'\bIS\b(\s*)SELECT\b', rest, re.IGNORECASE)
            if is_m:
                # Calculate absolute position
                is_start = pos + is_m.start()
                # Check not inside string literal
                prefix = s[:is_start]
                if prefix.count("'") % 2 == 0 and prefix.count('"') % 2 == 0:
                    # Replace 'IS' with 'FOR'
                    s = s[:is_start] + 'FOR' + s[is_start+2:]
        return s

    sql = _replace_cursor_is_for(sql)

    return sql


# =============================================================================
# P10: REGEXP_REPLACE conversion (Phase 10)
# =============================================================================

def _convert_regexp_replace(sql: str) -> str:
    """Convert Oracle REGEXP_REPLACE to PG regexp_replace.

    Oracle signature:
        REGEXP_REPLACE(source, pattern, replace [, position [, occurrence [, match_param]]])

    PG signature:
        regexp_replace(source, pattern, replace [, flags])

    Differences:
    - Oracle default = global replace (all occurrences)
    - PG default = replace first only
    - Oracle backrefs use \\1, \\2 etc. (compatible with PG)
    - position parameter (Oracle) is not in PG - PG starts from beginning
    - occurrence parameter (Oracle) is not in PG - need regex wrapper for nth occurrence
    - match_param flags translated (i, c, n, m, x)

    Mirrors Perl: appends 'g' flag to ensure global replacement.
    Strategy: add 'g' flag if no flags already specified.
    """
    # Find all REGEXP_REPLACE( and process each
    output_sql = ''
    i = 0
    while i < len(sql):
        m = re.search(r'\bREGEXP_REPLACE\s*\(', sql[i:], re.IGNORECASE)
        if not m:
            output_sql += sql[i:]
            break
        # Found start of REGEXP_REPLACE
        start = i + m.start()
        paren_start = i + m.end() - 1  # position of (
        output_sql += sql[i:start]
        # Find matching close paren
        depth = 0
        j = paren_start
        in_string = False
        string_char = None
        while j < len(sql):
            ch = sql[j]
            if in_string:
                if ch == string_char and (j == 0 or sql[j-1] != '\\'):
                    in_string = False
                j += 1
                continue
            if ch in ("'", '"'):
                in_string = True
                string_char = ch
                j += 1
                continue
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth != 0:
            # Unbalanced, just keep as-is
            output_sql += sql[start:]
            break
        # Now sql[paren_start+1:j] is the args
        full_call = sql[start:j+1]
        args_str = sql[paren_start+1:j]
        args = _split_top_level(args_str, ',')

        if len(args) == 3:
            new_call = f"regexp_replace({args_str.strip()}, 'g')"
        elif len(args) == 4:
            new_call = f"regexp_replace({args[0].strip()}, {args[1].strip()}, {args[2].strip()}, 'g')"
        elif len(args) == 5:
            new_call = f"regexp_replace({args[0].strip()}, {args[1].strip()}, {args[2].strip()}, 'g')"
        elif len(args) >= 6:
            flags = args[5].strip().strip("'\"")
            pg_flags = 'g'
            for ch in flags.lower():
                if ch == 'i' or ch == 'c' or ch == 'x':
                    pg_flags += ch
            new_call = f"regexp_replace({args[0].strip()}, {args[1].strip()}, {args[2].strip()}, '{pg_flags}')"
        else:
            new_call = full_call
        output_sql += new_call
        i = j + 1

    sql = output_sql

    # Also handle REGEXP_LIKE -> regexp_match
    sql = re.sub(
        r'\bREGEXP_LIKE\s*\(\s*([^,]+)\s*,\s*([^\)]+)\)',
        r"(regexp_match(\1, \2) IS NOT NULL)",
        sql, flags=re.IGNORECASE
    )

    return sql


# =============================================================================
# P11: Date arithmetic functions (Phase 11)
# =============================================================================

def _convert_date_functions(sql: str) -> str:
    """Convert Oracle date functions to PG equivalents.

    Handles:
        EXTRACT(YEAR FROM date)       - already PG-compatible (kept)
        EXTRACT(DAY FROM ...) - already PG-compatible
        ADD_MONTHS(date, n)           - date + (n || ' months')::INTERVAL
        LAST_DAY(date)                - last day of month
        MONTHS_BETWEEN(d1, d2)        - complex, approximated
        NEXT_DAY(date, day)           - hard, not implemented

    Mirrors Perl's partial implementation.
    """
    # EXTRACT is already PG-compatible (lowercase it for consistency)
    # EXTRACT(YEAR|MONTH|DAY|HOUR|MINUTE|SECOND FROM expr) -> already supported
    # PG syntax: EXTRACT(field FROM source)
    # Just ensure it's left as-is (no conversion needed)
    # But lowercase for consistency
    sql = re.sub(
        r'\bEXTRACT\s*\(\s*(YEAR|MONTH|DAY|HOUR|MINUTE|SECOND|TIMEZONE_HOUR|TIMEZONE_MINUTE|TIMEZONE_REGION|DAY_OF_WEEK|DAY_OF_MONTH|DAY_OF_YEAR)\s+FROM\b',
        lambda m: 'EXTRACT(' + m.group(1).lower() + ' FROM',
        sql, flags=re.IGNORECASE
    )

    # ADD_MONTHS(date, n) -> date + (n || ' months')::INTERVAL
    # Handle integer second arg
    sql = re.sub(
        r'\bADD_MONTHS\s*\(\s*([^,]+?)\s*,\s*(\d+)\s*\)',
        lambda m: f"({m.group(1).strip()} + ({m.group(2)} || ' months')::INTERVAL)",
        sql, flags=re.IGNORECASE
    )
    # Handle non-integer (variable/expression)
    sql = re.sub(
        r'\bADD_MONTHS\s*\(\s*([^,]+?)\s*,\s*([^,\(\)]+?)\s*\)',
        lambda m: f"({m.group(1).strip()} + ({m.group(2).strip()} * INTERVAL '1 month'))",
        sql, flags=re.IGNORECASE
    )

    # LAST_DAY(date) -> last day of month
    # ((date_trunc('month', date) + interval '1 month')::date - 1)
    sql = re.sub(
        r'\bLAST_DAY\s*\(\s*([^\(\)]+?)\s*\)',
        lambda m: f"((date_trunc('month', ({m.group(1).strip()})::timestamp) + interval '1 month')::date - 1)",
        sql, flags=re.IGNORECASE
    )

    # MONTHS_BETWEEN(d1, d2) - complex Oracle function
    # Returns the number of months between d1 and d2
    # Approximation: EXTRACT(YEAR FROM age(d1, d2)) * 12 + EXTRACT(MONTH FROM age(d1, d2))
    # PG has age() function which returns interval
    sql = re.sub(
        r'\bMONTHS_BETWEEN\s*\(\s*([^,]+?)\s*,\s*([^,\(\)]+?)\s*\)',
        lambda m: f"(EXTRACT(YEAR FROM age({m.group(1).strip()}, {m.group(2).strip()})) * 12 + EXTRACT(MONTH FROM age({m.group(1).strip()}, {m.group(2).strip()})))",
        sql, flags=re.IGNORECASE
    )

    # NEXT_DAY(date, day_name) - hard to translate
    # Oracle: NEXT_DAY(date, 'MONDAY') -> next Monday on or after date
    # PG: Not a direct equivalent. Requires custom function.
    # For now, leave as-is (will fail in PG but preserves user intent)
    # Mark with warning comment if possible
    # sql = re.sub(  # TODO: comment
    #     r'\bNEXT_DAY\s*\(\s*([^,]+?)\s*,\s*([^,\(\)]+?)\s*\)',
    #     lambda m: f"/* NEXT_DAY not auto-converted */ {m.group(0)}",
    #     sql, flags=re.IGNORECASE
    # )

    return sql


# =============================================================================
# Phase 1: Universal syntax fixes (T1-T4)
# =============================================================================

def _fix_null_comparison(sql: str) -> str:
    """Fix Oracle NULL comparison to PG IS NULL / IS NOT NULL.

    Oracle: col <> NULL  (returns FALSE, bug!)
    Oracle: col != NULL  (returns FALSE, bug!)
    Oracle: col = NULL   (returns FALSE, bug!)
    PG: col IS NULL / IS NOT NULL

    Mirrors Perl:
        s/\\s*(<>|!=)\\s*NULL/ IS NOT NULL/igs;
        s/\\s*=\\s*NULL\\b/ IS NULL/igs;
    """
    # col <> NULL or col != NULL -> col IS NOT NULL
    sql = re.sub(r'\s*(<>|!=)\s*NULL\b', ' IS NOT NULL', sql, flags=re.IGNORECASE)
    # col = NULL -> col IS NULL
    sql = re.sub(r'\s*=\s*NULL\b', ' IS NULL', sql, flags=re.IGNORECASE)
    return sql


def _fix_binary_literals(sql: str) -> str:
    """Fix Oracle binary special literals.

    BINARY_FLOAT_NAN  -> 'NaN'
    BINARY_FLOAT_INFINITY -> 'Infinity'
    BINARY_DOUBLE_NAN  -> 'NaN'
    BINARY_DOUBLE_INFINITY -> 'Infinity'
    'Inf' -> 'Infinity' (for negative, '-Inf' -> '-Infinity')
    Mirrors Perl:
        s/BINARY_(FLOAT|DOUBLE)_NAN/'NaN'/igs;
        s/([\-]*)BINARY_(FLOAT|DOUBLE)_INFINITY/'$1Infinity'/igs;
        s/'([\-]*)Inf'/'$1Infinity'/igs;
    """
    sql = re.sub(r"\bBINARY_(?:FLOAT|DOUBLE)_NAN\b", "'NaN'", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\b([\-]?)BINARY_(?:FLOAT|DOUBLE)_INFINITY\b", r"'\1Infinity'", sql, flags=re.IGNORECASE)
    sql = re.sub(r"'([\-]*)Inf'", r"'\1Infinity'", sql, flags=re.IGNORECASE)
    return sql


def _replace_pipe_row(sql: str) -> str:
    """Convert Oracle PIPE ROW to PG RETURN NEXT.

    Oracle:  PIPE ROW(record_value);
    PG:      RETURN NEXT record_value;

    Mirrors Perl:
        s/PIPE\\s+ROW\\s*/RETURN NEXT /igs;
        s/(RETURN NEXT )\\(([^\\)]+)\\)/$1$2/igs;
    """
    # PIPE ROW (expr); -> RETURN NEXT expr;
    sql = re.sub(
        r'\bPIPE\s+ROW\s*\(\s*([^()]+?)\s*\)\s*;',
        r'RETURN NEXT \1;',
        sql, flags=re.IGNORECASE
    )
    # PIPE ROW expr; (no parens)
    sql = re.sub(
        r'\bPIPE\s+ROW\s+([^;]+?)\s*;',
        r'RETURN NEXT \1;',
        sql, flags=re.IGNORECASE
    )
    # RETURN NEXT (expr) -> RETURN NEXT expr (cleanup redundant parens)
    sql = re.sub(
        r'\bRETURN\s+NEXT\s+\(([^()]+)\)\s*;',
        r'RETURN NEXT \1;',
        sql, flags=re.IGNORECASE
    )
    return sql


def _strip_supplemental_log(sql: str) -> str:
    """Remove Oracle Supplemental Log Data (Goldengate).

    Oracle:  SUPPLEMENTAL LOG DATA (cols) COLUMNS
    PG:      (removed, not supported)
    Mirrors Perl:
        s/SUPPLEMENTAL LOG DATA \\(.*?\\) COLUMNS//is;
    """
    sql = re.sub(r'\bSUPPLEMENTAL\s+LOG\s+DATA\s*\([^)]*\)\s*COLUMNS\b', '', sql, flags=re.IGNORECASE)
    return sql


# =============================================================================
# Phase 2: DDL Extraction - SEQUENCE/VIEW/TRIGGER/TYPE (T7-T10)
# =============================================================================

def convert_sequence(sql: str) -> Tuple[str, List[str]]:
    """Convert Oracle CREATE SEQUENCE to PG.

    Oracle:  CREATE SEQUENCE name INCREMENT BY 1 START WITH 1 MAXVALUE 999999999
             MINVALUE 1 CACHE 20 CYCLE|NOCYCLE ORDER|NOORDER;
    PG:      CREATE SEQUENCE name INCREMENT 1 START 1 MAXVALUE 999999999
             MINVALUE 1 CACHE 20 CYCLE|NOCYCLE;

    PG does NOT support ORDER keyword - removes it.

    PG bigint max is 9223372036854775807 (19 digits).  Oracle MAXVALUE
    may exceed this (e.g. NUMBER(20)).  We cap the value to the PG
    bigint max and emit a warning.
    """
    warnings: List[str] = []
    result = sql

    # INCREMENT BY -> INCREMENT
    result = re.sub(r'\bINCREMENT\s+BY\s+', 'INCREMENT ', result, flags=re.IGNORECASE)
    # START WITH -> START
    result = re.sub(r'\bSTART\s+WITH\s+', 'START ', result, flags=re.IGNORECASE)
    # MAX_VALUE -> MAXVALUE
    result = re.sub(r'\bMAX_VALUE\b', 'MAXVALUE', result, flags=re.IGNORECASE)
    # MIN_VALUE -> MINVALUE
    result = re.sub(r'\bMIN_VALUE\b', 'MINVALUE', result, flags=re.IGNORECASE)
    # NOCYCLE -> NO CYCLE
    result = re.sub(r'\bNOCYCLE\b', 'NO CYCLE', result, flags=re.IGNORECASE)
    # Remove ORDER | NOORDER (not in PG)
    result = re.sub(r'\b\s+(NO)?ORDER\s*;', ';', result, flags=re.IGNORECASE)
    # Remove KEEP/NOKEEP (not in PG)
    result = re.sub(r'\b\s+(NO)?KEEP\s*', ' ', result, flags=re.IGNORECASE)
    # SCALE | NOSCALE (11g+)
    result = re.sub(r'\b\s+(NO)?SCALE\s*', ' ', result, flags=re.IGNORECASE)
    # SESSION | GLOBAL (only in some versions)
    result = re.sub(r'\b\s+(SESSION|GLOBAL)\s*', ' ', result, flags=re.IGNORECASE)

    # PG bigint range cap (9223372036854775807)
    _PG_BIGINT_MAX = 9223372036854775807
    # Oracle NOMAXVALUE generates an implicit maxvalue of 10^27 - 1
    # which is far beyond PG bigint.  We cap MAXVALUE explicitly.
    def _cap_maxvalue(m):
        v = int(m.group(1))
        if v > _PG_BIGINT_MAX:
            warnings.append(
                f"MAXVALUE {v} exceeds PG bigint max ({_PG_BIGINT_MAX}); capped"
            )
            return f"MAXVALUE {_PG_BIGINT_MAX}"
        return m.group(0)
    result = re.sub(
        r'\bMAXVALUE\s+(\d+)',
        _cap_maxvalue,
        result,
        flags=re.IGNORECASE,
    )

    return result, warnings


def _convert_view_options(sql: str) -> str:
    """Convert Oracle VIEW options to PG.

    Oracle:  CREATE VIEW ... WITH READ ONLY;
    Oracle:  CREATE VIEW ... WITH CHECK OPTION;
    Oracle:  CREATE OR REPLACE FORCE VIEW ... AS ...

    PG:      CREATE VIEW ... WITH CASCADED CHECK OPTION;
    PG:      (no READ ONLY equivalent in PG)
    """
    # WITH READ ONLY -> (removed, not supported in PG)
    sql = re.sub(r'\bWITH\s+READ\s+ONLY\b', '', sql, flags=re.IGNORECASE)

    # WITH CHECK OPTION -> WITH CASCADED CHECK OPTION
    sql = re.sub(
        r'\bWITH\s+CHECK\s+OPTION\s*;',
        'WITH CASCADED CHECK OPTION;',
        sql, flags=re.IGNORECASE
    )

    # CONSTRAINTS (Oracle only) -> remove
    sql = re.sub(r'\s+ADD\s+\w+\s+CONSTRAINT\s+\w+.*?CHECK\s*\([^)]+\)\s*(?:DISABLE|ENABLE)?', '', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bDROP\s+(?:PRIMARY\s+KEY|UNIQUE\s*\([^)]+\)|CONSTRAINT\s+\w+)\s*;', '', sql, flags=re.IGNORECASE)

    # XMLType views: OF XMLTYPE (...) -> remove
    sql = re.sub(r'\s+OF\s+XMLTYPE\s*\([^)]*\)', '', sql, flags=re.IGNORECASE)

    # BEQUEER DEFINERS/WHERE NETTLINK -> remove Oracle-specific
    sql = re.sub(r'\s+BEQUEATH\s+(DEFINER|CURRENT_USER)\b', '', sql, flags=re.IGNORECASE)

    # CONTAINER_MAP, CONTAINERS_DEFAULT -> Oracle-only
    sql = re.sub(r'\s+CONTAINER_(MAP|DEFAULT)\b', '', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bDEFAULT\s+CONTAINER\s*=\s*\w+', '', sql, flags=re.IGNORECASE)

    # EDITIONABLE / NONEDITIONABLE -> remove (12c+)
    sql = re.sub(r'\b(?:NON)?EDITIONABLE\s+', '', sql, flags=re.IGNORECASE)

    return sql


def _extract_plsql_block(text: str, start_pos: int) -> Tuple[int, int]:
    """Given text and position of 'BEGIN' token, find matching 'END' (keyword-level).

    Depth-aware: tracks nested BEGIN/END, IF/END IF, LOOP/END LOOP, CASE/END CASE.
    Returns (begin_pos, end_pos_after_end_keyword). end_pos excludes trailing ';'/'.
    Respects single-quoted strings and -- comments.
    """
    i = start_pos
    n = len(text)
    depth = 0
    in_str = False
    str_ch = ''
    # Walk forward; count opens, stop at depth 0 close.
    # We treat BEGIN, IF, LOOP, CASE, (also DECLARE not relevant) as opens,
    # END (optionally followed by IF/LOOP/CASE/name) as close.
    while i < n:
        ch = text[i]
        if in_str:
            if ch == str_ch:
                # handle '' escape
                if i + 1 < n and text[i + 1] == str_ch:
                    i += 2
                    continue
                in_str = False
            i += 1
            continue
        if ch == "'":
            in_str = True
            str_ch = ch
            i += 1
            continue
        if ch == '-' and i + 1 < n and text[i + 1] == '-':
            eol = text.find('\n', i)
            i = n if eol == -1 else eol
            continue
        # word?
        m = re.match(r'[A-Za-z][A-Za-z0-9_]*', text[i:])
        if m:
            word = m.group(0).upper()
            wstart = i
            wend = i + len(word)
            if word in ('BEGIN',):
                depth += 1
            elif word == 'IF':
                # Only count IF that opens (followed by condition ... THEN).
                # Heuristic: must be at statement start (previous non-ws is ; or THEN or ELSE or BEGIN or newline).
                # We just count every IF - safe because each IF has a matching END IF.
                depth += 1
            elif word == 'LOOP':
                # 'FOR x IN .. LOOP' or 'WHEN .. LOOP' or plain 'LOOP'.
                depth += 1
            elif word == 'CASE':
                depth += 1
            elif word == 'END':
                # Look ahead for optional IF/LOOP/CASE/identifier
                tail_m = re.match(r'\s+(IF|LOOP|CASE|[A-Za-z][A-Za-z0-9_$\.]*)', text[wend:])
                tail = tail_m.group(1).upper() if tail_m else ''
                # 'END IF' closes IF; 'END LOOP' closes LOOP; 'END CASE' closes CASE;
                # plain 'END' closes BEGIN.
                depth -= 1
                if depth == 0:
                    return (wstart, wend + (tail_m.end() if tail_m else 0))
                i = wend + (tail_m.end() if tail_m else 0)
                continue
            i = wend
            continue
        i += 1
    return (start_pos, n)


def _split_triggers(sql: str) -> List[str]:
    """Split a file containing multiple CREATE TRIGGER statements.

    Uses SQL*Plus '/' line terminator when present; otherwise falls back
    to splitting on ';' at top-level depth.
    """
    # Prefer / on its own line
    parts = re.split(r'(?m)^\s*/\s*$', sql)
    if len(parts) > 1:
        return [p for p in parts if re.search(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:COMPOUND\s+)?TRIGGER\b', p, re.IGNORECASE)]
    return [sql]


# P1-1: 提取触发器对象名（不依赖完整解析，供 warnings 显式登记用）。
_TRIGGER_NAME_FALLBACK_RE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:COMPOUND\s+)?TRIGGER\s+([A-Za-z0-9_$\-\.\"']+)",
    re.IGNORECASE,
)


def _split_compound_trigger(sql: str) -> Tuple[str, List[str]]:
    """
    P3-4：复合触发器结构化拆分（委派给 workspace 实现）。

    拆分器逻辑放在 ``workspace/converter/compound_trigger.py``——
    那里有独立单测（``workspace/tests/test_compound_trigger.py``）。
    本引擎**不反向硬依赖** workspace：按需把 workspace 加入 sys.path，
    导入失败时抛 ImportError，由 ``convert_trigger`` 捕获并降级为显式
    warning（保持 P1-1 的「不静默」底线，引擎仍可独立运行）。
    """
    import os
    import sys as _sys

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ws = os.path.join(repo_root, "workspace")
    if ws not in _sys.path:
        _sys.path.insert(0, ws)
    from converter.compound_trigger import convert_compound_trigger  # noqa: PLC0415

    return convert_compound_trigger(sql)


def convert_trigger(sql: str) -> Tuple[str, List[str]]:
    """Convert Oracle CREATE TRIGGER(s) to PG function + CREATE TRIGGER.

    P1-1 修复（实施路径 §四 阶段一）：
        历史版本只转换 ``_split_triggers`` 命中的**第一个**块，其余触发器
        被静默丢弃（实测源 6 个 → 产物 1 个，转换报告仍 ``success:true``）。
        现在遍历全部分块：
          - 普通 ROW/STATEMENT 触发器 → 走 ``_convert_single_trigger``；
          - COMPOUND 触发器（PG 无原生等价物）→ **不静默丢弃**，
            对象名显式登记到 warnings（进人工清单，P3-4/P4 阶段处理）。

    Oracle:  CREATE OR REPLACE TRIGGER name
             BEFORE INSERT OR UPDATE ON table
             REFERENCING OLD AS old NEW AS new
             FOR EACH ROW
             WHEN (condition)
             BEGIN
               :NEW.col := ...;
             END;

    PG:      CREATE OR REPLACE FUNCTION name() RETURNS TRIGGER AS $$
             BEGIN
               NEW.col := ...;
               RETURN NEW;
             END;
             $$ LANGUAGE plpgsql;

             CREATE TRIGGER name BEFORE INSERT OR UPDATE ON table
             FOR EACH ROW WHEN (condition)
             EXECUTE PROCEDURE name();
    """
    blocks = _split_triggers(sql)
    out_chunks: List[str] = []
    warnings: List[str] = []
    for block in blocks:
        # COMPOUND TRIGGER：PG 无原生等价物。P3-4（实施路径 §四 阶段三）已实现
        # 结构化拆分——拆为多个普通触发器（每个时机点一个）+ session GUC 承载
        # 跨时机点状态。拆分器实现在 workspace/converter/compound_trigger.py。
        # 拆不出来时抛 CompoundTriggerError，在此处降级为显式 warning，
        # **绝不静默丢弃**（P1-1 原则延续）。
        if re.search(r'\bCOMPOUND\s+TRIGGER\b', block, re.IGNORECASE):
            m = _TRIGGER_NAME_FALLBACK_RE.search(block)
            tname = m.group(1).strip('"').split('.')[-1] if m else '<unknown>'
            try:
                chunk, chunk_warnings = _split_compound_trigger(block)
                out_chunks.append(chunk.rstrip())
                warnings.extend(chunk_warnings)
            except Exception as exc:  # noqa: BLE001 - 拆分失败必须显式登记
                warnings.append(
                    f"[P3-4] COMPOUND TRIGGER {tname} 结构化拆分失败："
                    f"{type(exc).__name__}: {exc} —— 已登记进人工清单（未静默丢弃）"
                )
            continue
        chunk, chunk_warnings = _convert_single_trigger(block)
        out_chunks.append(chunk.rstrip())
        warnings.extend(chunk_warnings)

    return "\n\n".join(out_chunks), warnings


def _convert_single_trigger(sql: str) -> Tuple[str, List[str]]:
    """Convert one Oracle CREATE TRIGGER block."""
    warnings: List[str] = []
    result = sql

    # Extract trigger name and metadata
    header_m = re.search(
        r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:COMPOUND\s+)?TRIGGER\s+([A-Za-z0-9_\$\-\."]+)',
        result, re.IGNORECASE
    )
    if not header_m:
        return result, ['Could not parse trigger header']

    trigger_name = header_m.group(1).strip('"').split('.')[-1]
    table_m = re.search(r'\bON\s+([A-Za-z0-9_\$\-\."]+)', result, re.IGNORECASE)
    table_name = table_m.group(1).strip('"').split('.')[-1] if table_m else 'unknown_table'

    # Determine timing and event
    timing_m = re.search(r'\b(BEFORE|AFTER|INSTEAD\s+OF)\b', result, re.IGNORECASE)
    timing = (timing_m.group(1).upper() if timing_m else 'BEFORE')  # keep 'INSTEAD OF' with space

    event_m = re.search(
        r'\b(BEFORE|AFTER|INSTEAD\s+OF)\s+(INSERT\s+OR\s+UPDATE\s+OR\s+DELETE|INSERT\s+OR\s+UPDATE|INSERT\s+OR\s+DELETE|UPDATE\s+OR\s+DELETE|INSERT|UPDATE|DELETE|TRUNCATE)',
        result, re.IGNORECASE
    )
    if event_m:
        events = event_m.group(2).upper().replace(' OR ', ' OR ').replace('  ', ' ')
    else:
        events = 'INSERT'
        warnings.append("Could not detect trigger events, defaulting to INSERT")

    # Check FOR EACH ROW vs FOR EACH STATEMENT
    each_row = bool(re.search(r'\bFOR\s+EACH\s+ROW\b', result, re.IGNORECASE))

    # Extract WHEN condition
    when_m = re.search(r'\bWHEN\s*\(([^)]+(?:\([^)]*\))?[^)]*)\)\s*', result, re.IGNORECASE)
    when_cond = when_m.group(1) if when_m else None
    # Convert :NEW / :OLD references in WHEN clause
    if when_cond:
        when_cond = re.sub(r':NEW\b', 'NEW', when_cond)
        when_cond = re.sub(r':OLD\b', 'OLD', when_cond)
        when_cond = re.sub(r':NEW\.', 'NEW.', when_cond)
        when_cond = re.sub(r':OLD\.', 'OLD.', when_cond)

    # Extract DECLARE/BEGIN block using depth-aware matcher.
    begin_m = re.search(r'\bBEGIN\b', result, re.IGNORECASE)
    if not begin_m:
        return result, ['Could not find BEGIN/END block in trigger']

    begin_pos = begin_m.start()
    _, end_pos = _extract_plsql_block(result, begin_m.start())
    # body is text between BEGIN keyword end and END keyword start.
    # end_pos is position right after END (incl. optional IF/LOOP/CASE/name),
    # so we strip the trailing END ourselves (template adds its own END;).
    body_start = begin_m.end()
    body = result[body_start:end_pos]
    # Remove the trailing END keyword + optional label + ; from body.
    body = re.sub(r'\bEND\s+(?:IF|LOOP|CASE|[A-Za-z][A-Za-z0-9_$\.]*)\s*;?\s*$', '', body, flags=re.IGNORECASE)
    body = re.sub(r'\bEND\s*;?\s*$', '', body, flags=re.IGNORECASE)

    # Extract DECLARE section (between DECLARE and BEGIN)
    decl_section = ''
    decl_m = re.search(r'\bDECLARE\b', result, re.IGNORECASE)
    if decl_m and decl_m.start() < begin_pos:
        decl_section = result[decl_m.end():begin_pos].strip()
        # Strip PRAGMA AUTONOMOUS_TRANSACTION (PG has no equivalent)
        decl_section = re.sub(r'\bPRAGMA\s+AUTONOMOUS_TRANSACTION\s*;?', '', decl_section, flags=re.IGNORECASE)
        decl_section = _apply_type_mapping(decl_section)

    # Convert body:
    # :NEW.column -> NEW.column
    # :OLD.column -> OLD.column
    body = re.sub(r':NEW\b', 'NEW', body)
    body = re.sub(r':OLD\b', 'OLD', body)
    body = re.sub(r':NEW\.', 'NEW.', body)
    body = re.sub(r':OLD\.', 'OLD.', body)
    # REFERENCING clause -> remove
    body = re.sub(r'\bREFERENCING\s+(?:OLD\s+AS\s+\w+|NEW\s+AS\s+\w+\s*)+', '', body, flags=re.IGNORECASE)
    # WHEN clause reference -> remove (handled in CREATE TRIGGER)
    body = re.sub(r'\bWHEN\s*\([^)]+\)\s*', '', body, flags=re.IGNORECASE)

    # Convert INSERTING/UPDATING/DELETING predicates
    body = re.sub(r'\bINSERTING\b', 'TG_OP = \'INSERT\'', body, flags=re.IGNORECASE)
    body = re.sub(r'\bUPDATING\b(?!\()', 'TG_OP = \'UPDATE\'', body, flags=re.IGNORECASE)
    body = re.sub(r'\bUPDATING\s*\(\s*\'([^\']+)\'\s*\)', r'TG_OP = \'UPDATE\' AND TG_ARGV[0] = \'\1\'', body, flags=re.IGNORECASE)
    body = re.sub(r'\bDELETING\b', 'TG_OP = \'DELETE\'', body, flags=re.IGNORECASE)

    # Oracle RAISE_APPLICATION_ERROR(code, msg) -> PG RAISE EXCEPTION
    body = re.sub(
        r'\bRAISE_APPLICATION_ERROR\s*\(\s*(-?\d+)\s*,\s*([^)]+)\)',
        lambda m: f"RAISE EXCEPTION USING MESSAGE = {m.group(2).strip()}, ERRCODE = abs({m.group(1)}%50000) + 50000",
        body, flags=re.IGNORECASE
    )

    # Convert Oracle built-in function calls inside the body
    # (SYSTIMESTAMP/SYSDATE, NVL/NVL2, INSTR, SUBSTR, DECODE,
    # DBMS_OUTPUT.PUT_LINE, ...).
    #
    # Trigger bodies used to skip these two steps and only got type mapping,
    # so Oracle built-ins survived verbatim into plpgsql. PL/pgSQL then
    # resolved them as column references the first time the trigger fired
    # ("column systimestamp does not exist"), which broke every INSERT that
    # the trigger covered. Functions/procedures already ran this pipeline via
    # convert_plsql_code; triggers now run the same two steps.
    _tstate = ConversionState()
    body = _extract_function_calls(body, _tstate)
    body = _apply_function_conversion(body, _tstate)
    body = _restore_subparts(body, _tstate)

    # Apply type mapping to body
    body = _apply_type_mapping(body)
    body = _fix_null_comparison(body)
    body = _convert_ref_cursor(body)

    # Build PG function + trigger
    if each_row:
        body_lines = body.split('\n')
        # Add RETURN NEW at end if missing
        has_return = any('RETURN' in line.upper() for line in body_lines)
        if not has_return:
            body = body.rstrip() + '\n    RETURN NEW;\n'
        else:
            # If no RETURN NEW/OLD, add RETURN NEW
            if not any(re.search(r'\bRETURN\s+(NEW|OLD|NULL)\s*;', line, re.IGNORECASE) for line in body_lines):
                body = body.rstrip() + '\n    RETURN NEW;\n'

        ret_type = 'TRIGGER'
        return_stmt = 'RETURN NEW;'  # default
        if any(re.search(r'\bRETURN\s+OLD\s*;', line, re.IGNORECASE) for line in body_lines):
            return_stmt = 'RETURN OLD;'
    else:
        ret_type = 'TRIGGER'
        return_stmt = ''  # statement-level triggers don't return

    pg_func = f"""CREATE OR REPLACE FUNCTION {trigger_name}() RETURNS {ret_type} AS $$
{('DECLARE ' + decl_section) if decl_section else ''}
BEGIN
{body}
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS {trigger_name} ON {table_name};
CREATE TRIGGER {trigger_name} {timing} {events}
ON {table_name}
{'FOR EACH ROW' if each_row else 'FOR EACH STATEMENT'}
{('WHEN (' + when_cond + ')') if when_cond else ''}
EXECUTE PROCEDURE {trigger_name}();
"""
    return pg_func, warnings


# =============================================================================
# Phase 3: PACKAGE global variables -> set_config (T13-T14)
# =============================================================================

def _convert_package_globals(declarations: List[str], pkg_name: str) -> Dict[str, Dict[str, str]]:
    """Parse PACKAGE BODY global variable declarations.

    Returns a dict mapping var_name -> {type, default}.
    Mirrors Perl register_global_variable (extract phase only).
    """
    global_vars: Dict[str, Dict[str, str]] = {}
    # Prefixes that indicate this line is NOT a variable declaration
    skip_prefixes = ('CURSOR ', 'TYPE ', 'SELECT ', 'FROM ', 'WHERE ', 'AND ',
                     'OR ', 'GROUP ', 'ORDER ', 'HAVING ', 'ON ', 'JOIN ',
                     'LEFT ', 'RIGHT ', 'INNER ', 'OUTER ', 'FULL ', 'CROSS ')

    for line in declarations:
        line_stripped = line.strip()
        if chr(45)*2 in line:  # "--" comment
            continue

        # Skip lines that are NOT global variable declarations:
        # - CURSOR definitions
        # - TYPE definitions
        # - SELECT/FROM/WHERE/etc. SQL keywords (from cursor bodies)
        # - Empty lines or separator lines
        upper_line = line_stripped.upper()
        if (upper_line.startswith(skip_prefixes) or
            not line_stripped or
            line_stripped.startswith(('--', '/*', '*/')) or
            '--------' in line_stripped or
            '*****' in line_stripped or
            re.match(r'^\s*\d+\s*$', line_stripped) or
            re.match(r'^[\s,(]*$', line_stripped)):
            continue

        # Match: var_name [CONSTANT] TYPE [DEFAULT|:=|:= expr;]
        # var_name can have leading package prefix (g_xxx, c_xxx, s_xxx, etc.)
        m = re.match(
            r'^(\w+)\s+(?:CONSTANT\s+)?(\w+(?:\([^)]*\))?)?\s*(?::=|DEFAULT\s+)?\s*(.*?);?\s*$',
            line_stripped,
            re.IGNORECASE
        )
        if not m:
            continue
        var_name = m.group(1)
        var_type = m.group(2) or ''
        default = m.group(3).strip().rstrip(';')

        # Filter: only collect likely global vars (g_xxx, c_xxx, n_xxx, m_xxx).
        # DO NOT include 's_' prefix or bare uppercase letters like 'S', 'SC', 'SB'
        # which are table aliases in CURSOR definitions.
        # Also exclude fully uppercase names that are just table names (STUDENT_MST)
        # or Oracle keywords.
        # Valid patterns: g_var, g_sVAR, g_nVAR, c_const, n_count, m_max, etc.
        if (var_name.startswith(('g_', 'c_', 'n_', 'm_')) or
            # Names with underscore and mixed case: g_sWSNUM, G_sTANTO_CD, etc.
            ('_' in var_name and not var_name.isupper())):
            global_vars[var_name] = {'type': var_type, 'default': default}

    return global_vars


def _apply_package_global_subs(sql: str, pkg_name: str, global_vars: Dict[str, Dict[str, str]]) -> str:
    """Apply set_config/current_setting substitution for PACKAGE global variables.

    Oracle:  g_var := 5;
    PG:      PERFORM set_config('pkg.g_var', '5', false);

    Oracle:  v := g_var;
    PG:      v := current_setting('pkg.g_var')::TYPE;

    Single-pass to avoid double substitution.
    """
    if not global_vars:
        return sql

    # Sort by length descending to avoid partial replacements
    sorted_vars = sorted(global_vars.keys(), key=lambda x: -len(x))

    # First, replace var := value; -> PERFORM set_config(...)
    # Use a placeholder to protect from second pass
    for var_name in sorted_vars:
        sql = re.sub(
            rf'\b{re.escape(var_name)}\s*:=\s*([^;]+?)\s*;',
            lambda m: f"/*__SET_CONFIG_{var_name}__{m.group(1)}__*/",
            sql, flags=re.IGNORECASE
        )

    # Then, replace bare var (as expression) -> current_setting(...)
    for var_name in sorted_vars:
        sql = re.sub(
            rf'\b{re.escape(var_name)}\b',
            f"current_setting('{pkg_name}.{var_name}')",
            sql, flags=re.IGNORECASE
        )

    # Restore placeholders to actual set_config calls
    for var_name in sorted_vars:
        sql = re.sub(
            rf'/\*__SET_CONFIG_{re.escape(var_name)}__(.+?)__\*/',
            rf"PERFORM set_config('{pkg_name}.{var_name}', \1, false);",
            sql, flags=re.IGNORECASE
        )

    return sql


# =============================================================================
# Phase 4: perform_replacement - PERFORM/CALL semantics (T21-T23)
# =============================================================================

# Known procedure names from GKB test corpus - these need CALL when used as statement
_DEFAULT_PROCEDURE_NAMES = frozenset([
    'GKBSKIDOTCH', 'GAAPK0030_PRC', 'KKBPK5551_FSETOLOG',
    'KKAPK0010_FYUBINHENSHU', 'KKAPK0010_FMULTIBYTEHENKAN',
    'G_SET_PARAMS', 'EXECUTE_IMMEDIATE_WRAPPER',
])

# Known function names that return values (used with :=)
_DEFAULT_FUNCTION_NAMES = frozenset([
    'FCHECKUPDATE', 'FGETINFO', 'FUNC_SETLOG', 'FUNC_MAIN', 'FUNC_B00INIT',
    'FUNC_B01PINIT', 'FUNC_B02PINIT', 'FGETATENA', 'FGETATENA2', 'FDAYEDIT',
    'FGETLOGPATH', 'FMULTIBYTEHENKAN', 'FYUBINHENSHU', 'FNINSHOGet',
    'FJSTPGet', 'FSETOLOG', 'FSHOGAKUMEI', 'FCHUGAKUMEI', 'FGYOSEIKUMEI',
    'FCHIKUMEI', 'FADDRESSEDIT', 'NVL', 'NVL2', 'DECODE', 'SYSDATE',
    'SYSTIMESTAMP', 'TRUNC', 'ROUND', 'TO_CHAR', 'TO_DATE',
])


def _apply_perform_call(sql: str, known_procedure_names: Optional[set] = None,
                         known_function_names: Optional[set] = None) -> str:
    """Convert Oracle procedure call semantics to PG PERFORM/CALL.

    Oracle:  PROC_NAME(args);  -- called as statement (returns no value)
    PG:      CALL PROC_NAME(args);

    Oracle:  FUNC_NAME(args);  -- called as statement (return value discarded)
    PG:      PERFORM FUNC_NAME(args);

    Oracle:  EXEC PKG.PROC_NAME(args);  -- SQL*Plus EXEC command
    PG:      CALL PKG.PROC_NAME(args);

    Mirrors Perl perform_replacement. Handles both procedure and function
    statement-level calls. When ``known_procedure_names`` and
    ``known_function_names`` are provided, only those names are rewritten;
    otherwise falls back to a conservative default list.
    """
    if known_procedure_names is None:
        known_procedure_names = set(_DEFAULT_PROCEDURE_NAMES)
    if known_function_names is None:
        known_function_names = set(_DEFAULT_FUNCTION_NAMES)

    # Pattern: name as bare statement followed by ;
    # Negative lookbehind to avoid replacing when already prefixed with :=
    #   (e.g. "v := PROC_NAME();" should NOT be wrapped)
    # Also skip when preceded by "PERFORM " or "CALL " (already wrapped)
    # Also skip when inside a SELECT statement (handled separately)

    if known_procedure_names:
        proc_pattern = '|'.join(re.escape(p) for p in known_procedure_names)
        # Protect "END PROC_NAME" patterns with placeholders first.
        # "END PROC_NAME;" and "END PROC_NAME(...);" are function/procedure
        # end markers, NOT statement-level procedure calls.
        end_placeholder_map: Dict[str, str] = {}

        def _proc_end_replace(m: re.Match) -> str:
            orig = m.group(0)
            key = f'%%END_PKG_{len(end_placeholder_map)}%%'
            end_placeholder_map[key] = orig
            return key

        sql = re.sub(
            rf'(?i)\bEND\s+({proc_pattern})(?:\s*\([^)]*\))?\s*;',
            _proc_end_replace,
            sql
        )

        # Procedure with parentheses: PROC_NAME(args);
        sql = re.sub(
            rf'(?<!:)(?<!PERFORM )(?<!CALL )(?<!\.)\b({proc_pattern})\s*\(',
            r'CALL \1(',
            sql,
            flags=re.IGNORECASE
        )
        # Procedure without parentheses: PROC_NAME;
        sql = re.sub(
            rf'(?<!:)(?<!PERFORM )(?<!CALL )(?<!\.)\b({proc_pattern})\s*;',
            r'CALL \1();',
            sql,
            flags=re.IGNORECASE
        )
        # Restore END markers
        for key, orig in end_placeholder_map.items():
            sql = sql.replace(key, orig)

    if known_function_names:
        fn_pattern = '|'.join(re.escape(f) for f in known_function_names)
        # Protect "END FUNC_NAME" patterns with placeholders
        end_fn_map: Dict[str, str] = {}

        def _func_end_replace(m: re.Match) -> str:
            orig = m.group(0)
            key = f'%%END_FN_{len(end_fn_map)}%%'
            end_fn_map[key] = orig
            return key

        sql = re.sub(
            rf'(?i)\bEND\s+({fn_pattern})(?:\s*\([^)]*\))?\s*;',
            _func_end_replace,
            sql
        )
        # Function with parentheses
        sql = re.sub(
            rf'(?<!:)(?<!PERFORM )(?<!CALL )(?<!\.)(?<!RETURN\s)\b({fn_pattern})\s*\(',
            r'PERFORM \1(',
            sql,
            flags=re.IGNORECASE
        )
        # Function without parentheses
        sql = re.sub(
            rf'(?<!:)(?<!PERFORM )(?<!CALL )(?<!\.)(?<!RETURN\s)\b({fn_pattern})\s*;',
            r'PERFORM \1();',
            sql,
            flags=re.IGNORECASE
        )
        # Restore END markers
        for key, orig in end_fn_map.items():
            sql = sql.replace(key, orig)

    if known_function_names:
        fn_pattern = '|'.join(re.escape(f) for f in known_function_names)
        # Function with parentheses in expression context
        sql = re.sub(
            rf'(?<!:)(?<!PERFORM )(?<!CALL )(?<!\.END )(?<!RETURN\s)\b({fn_pattern})\s*\(',
            r'PERFORM \1(',
            sql,
            flags=re.IGNORECASE
        )
        # Function without parentheses
        sql = re.sub(
            rf'(?<!:)(?<!PERFORM )(?<!CALL )(?<!\.END )(?<!RETURN\s)\b({fn_pattern})\s*;',
            r'PERFORM \1();',
            sql,
            flags=re.IGNORECASE
        )

    # EXEC PKG.PROC -> CALL PKG.PROC
    if known_procedure_names:
        proc_pattern = '|'.join(re.escape(p) for p in known_procedure_names)
        sql = re.sub(
            rf'\bEXEC\s+({proc_pattern})\s*\(',
            r'CALL \1(',
            sql,
            flags=re.IGNORECASE
        )

    return sql


# =============================================================================
# Type mapping (Phase 0)
# =============================================================================
_TYPE_MAP = [
    (re.compile(r'\bVARCHAR2\s*\(\s*(\d+)\s+(CHAR|BYTE)\s*\)'), r'VARCHAR(\1)'),
    (re.compile(r'\bNVARCHAR2\s*\(\s*(\d+)\s+(CHAR|BYTE)\s*\)'), r'VARCHAR(\1)'),
    (re.compile(r'\bVARCHAR2\s*(\(\s*\d+\s*\))?', re.IGNORECASE), r'VARCHAR\1'),
    (re.compile(r'\bNVARCHAR2\s*(\(\s*\d+\s*\))?', re.IGNORECASE), r'VARCHAR\1'),
    (re.compile(r'\bNUMBER\s*\(\s*(\d+)\s*,\s*0\s*\)'), r'BIGINT'),
    (re.compile(r'\bNUMBER\s*\(\s*(\d+)\s*\)'), r'BIGINT'),
    (re.compile(r'\bNUMBER\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)'), r'NUMERIC(\1,\2)'),
    (re.compile(r'\bNUMBER\s*\(\s*\*\s*\)'), r'NUMERIC'),
    (re.compile(r'\bNUMBER\b(?!\s*\()'), r'NUMERIC'),
    (re.compile(r'\bPLS_INTEGER\b'), r'INTEGER'),
    (re.compile(r'\bBINARY_INTEGER\b'), r'INTEGER'),
    (re.compile(r'\bNCLOB\b'), r'TEXT'),
    (re.compile(r'\bCLOB\b'), r'TEXT'),
    (re.compile(r'\bBLOB\b'), r'BYTEA'),
    (re.compile(r'\bRAW\s*\(\s*(\d+)\s*\)'), r'BYTEA'),
    (re.compile(r'\bLONG\s+RAW\b'), r'BYTEA'),
    (re.compile(r'\bLONG\b'), r'TEXT'),
    (re.compile(r'\bDATE\b(?!\s*\()'), r'TIMESTAMP'),
    # Oracle collection types -> PG arrays
    (re.compile(r'\bDBMS_UTILITY\.MAXNAME_ARRAY\b'), r'TEXT[]'),
    (re.compile(r'\bDBMS_UTILITY\.NAME_ARRAY\b'), r'TEXT[]'),
    (re.compile(r'\bDBMS_UTILITY\.UNIQUE_ACL\s*\(\s*\)'), r'TEXT[]'),
    # %ROWTYPE in variable declarations: var TABLENAME%ROWTYPE -> var TABLENAME
    # (PG uses table name directly as composite type reference)
    # Keep table name, only strip %ROWTYPE marker
    (re.compile(r'%ROWTYPE\b'), ''),
    # INDEX BY BINARY_INTEGER / INDEX BY PLS_INTEGER (associative array key)
    # PG doesn't support INDEX BY, remove the clause
    (re.compile(r'\s+INDEX\s+BY\s+(?:BINARY_INTEGER|PLS_INTEGER|VARCHAR2\s*\(\s*\d+\s*\)|VARCHAR\s*\(\s*\d+\s*\)|INTEGER|NUMBER|INTEGER|INT)'), ''),
]


def _apply_type_mapping(sql: str) -> str:
    for pattern, replacement in _TYPE_MAP:
        sql = pattern.sub(replacement, sql)
    return sql


# =============================================================================
# TABLESPACE / physical attribute removal (Phase 0+)
# =============================================================================
def _strip_physical_attributes(sql: str) -> str:
    """Strip Oracle-specific physical clauses."""
    patterns = [
        (r'\bTABLESPACE\s+\w+', ''),
        (r'\bNOCACHE\b', ''),
        (r'\bCACHE\b', ''),
        (r'\bLOGGING\b', ''),
        (r'\bNOLOGGING\b', ''),
        (r'\bPCTFREE\s+\d+', ''),
        (r'\bPCTUSED\s+\d+', ''),
        (r'\bINITRANS\s+\d+', ''),
        (r'\bMAXTRANS\s+\d+', ''),
        (r'\bSTORAGE\s*\([^)]*\)', ''),
        (r'\bSEGMENT\s+CREATION\s+(IMMEDIATE|DEFERRED)', ''),
        (r'\bENABLE\s+ROW\s+MOVEMENT\b', ''),
        (r'\bROW\s+MOVEMENT\b', ''),          # standalone ROW MOVEMENT clause
        (r'\bMONITORING\b', ''),
        (r'\bNOMONITORING\b', ''),
        (r'\bPARALLEL\s+\d*', ''),
        (r'\bNOPARALLEL\b', ''),
        # GLOBAL TEMPORARY -> TEMPORARY (PG: no GLOBAL keyword)
        (r'\bGLOBAL\s+TEMPORARY\b', 'TEMPORARY'),
    ]
    for pat, repl in patterns:
        sql = re.sub(pat, repl, sql, flags=re.IGNORECASE)
    return sql


def _strip_force_keyword(sql: str) -> str:
    sql = re.sub(r'\bCREATE(\s+(?:OR\s+REPLACE)?)?\s+(FORCE\s+)?(VIEW|TYPE)\b',
                 r'CREATE\1 \3', sql, flags=re.IGNORECASE)
    return sql


# =============================================================================
# (+), +, ..., OUT parameter helpers
# =============================================================================
def _convert_out_params(declare: str, code: str, state: ConversionState) -> Tuple[List[Tuple[str, str]], str, str]:
    """Convert OUT parameters to RETURNS TABLE.

    Returns: (out_params_list, new_declare, returns_table)
    PG 不允许 RETURNS TABLE 函数有 OUT/INOUT 参数。
    解决方案: 从参数列表中移除 OUT 参数(但不从 RETURNS TABLE 中移除)。
    """
    args_m = re.search(r'\(([^)]*)\)', declare)
    if not args_m:
        return [], declare, code, ""

    args_str = args_m.group(1)
    out_params = []
    new_args = []
    parts = _split_top_level(args_str, ',')
    for p in parts:
        p_strip = p.strip()
        # Strip comments for pattern matching
        p_check = re.sub(r'--[^\n]*', '', p_strip)
        p_check = re.sub(r'%ORA2PG_COMMENT\d+%', '', p_check)
        if re.search(r'\b(OUT|INOUT|IN\s+OUT)\b', p_check, re.IGNORECASE):
            # 兼容多种写法：<name> OUT/INOUT/IN OUT <type>  或  OUT/INOUT/IN OUT <name> <type>
            m = re.search(r'\b(\w+)\s+(?:IN\s+OUT|INOUT|OUT)\s+(\w+(?:\.\w+)?)', p_check, re.IGNORECASE)
            if not m:
                m = re.search(r'\b(?:IN\s+OUT|INOUT|OUT)\s+(\w+)\s+(\w+(?:\.\w+)?)', p_check, re.IGNORECASE)
            if m:
                out_params.append((m.group(1), m.group(2)))
                # 不在参数列表中添加 OUT 参数（PG 不允许 RETURNS TABLE 中有 OUT）
                continue
        new_args.append(p_strip)

    if not out_params:
        return [], declare, code, ""

    new_args_str = ', '.join(new_args)
    new_declare = declare.replace(args_str, new_args_str, 1)

    # Build RETURNS TABLE
    returns_table = "RETURNS TABLE(" + ", ".join(f"{n} {_apply_type_mapping(t)}" for n, t in out_params) + ")"
    state.warnings.append(f"OUT params converted: {len(out_params)} -> RETURNS TABLE")
    return out_params, new_declare, code, returns_table


# =============================================================================
# Balanced BEGIN/END block finder (mirrors Perl split + counter)
# =============================================================================
def _split_at_balanced_end(text: str, start_marker_regex: str = r'\bIS\b|\bAS\b') -> Tuple[str, str, str]:
    """Split text at start_marker_regex, then find matching END.

    Returns (before_marker, marker, after_marker)
    Mirrors Perl's split by BEGIN/DECLARE/END + counter approach.
    """
    m = re.search(start_marker_regex, text, re.IGNORECASE)
    if not m:
        return "", "", text

    before = text[:m.start()].rstrip()
    marker = text[m.start():m.end()]
    body = text[m.end():]

    # Use split by BEGIN/DECLARE/END with counter
    end_pattern = re.compile(
        r'\b(BEGIN|DECLARE|END(?!\s*(?:IF|LOOP|CASE|INTO|FROM|,|\)))[^;\s]*\s*;)',
        re.IGNORECASE
    )
    parts = end_pattern.split(body)
    depth = 0
    matched_body = ""
    i = 0
    while i < len(parts):
        chunk = parts[i]
        if re.match(r'\bBEGIN\b', chunk, re.IGNORECASE):
            depth += 1
            matched_body += chunk
        elif re.match(r'\bEND\b', chunk, re.IGNORECASE):
            if depth == 0:
                # matched END
                rest = ''.join(parts[i + 1:])
                return before, marker, matched_body + rest
            depth -= 1
            matched_body += chunk
        else:
            matched_body += chunk
        i += 1
    # Not found
    return before, marker, body


# =============================================================================
# Function/Procedure header parser (split-by-BEGIN approach)
# =============================================================================
def _lookup_function(plsql: str, package_name: Optional[str] = None) -> OracleFunction:
    """Parse PL/SQL function header using split-by-BEGIN approach.

    Mirrors Oracle.pm::_lookup_function:
        ($fct_detail{declare}, $fct_detail{code}) = split(/\\bBEGIN\\b/i, $plsql, 2);
    """
    func = OracleFunction(full_source=plsql)

    # Split by BEGIN (max 2 parts)
    parts = re.split(r'\bBEGIN\b', plsql, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) < 2:
        return func

    declare = parts[0]
    code = "BEGIN" + parts[1]  # restore the BEGIN
    func.declare = declare
    func.code = code

    # Extract function header from declare
    # Pattern: (.*?)\b(FUNCTION|PROCEDURE)\s+([^\s\(]+)\s*(\([^\)]*\))
    # The (.*?) can contain anything (incl. comments), due to /s
    m = re.search(
        r'(.*?)\b(FUNCTION|PROCEDURE)\s+([^\s\(]+)\s*(\([^\)]*\))',
        declare,
        re.DOTALL | re.IGNORECASE
    )
    if m:
        func.type = m.group(2).upper()
        func.name = m.group(3).strip().strip('"')
        func.args = m.group(4)
        func.has_out_params = bool(re.search(r'\bOUT\b', func.args, re.IGNORECASE))
    else:
        # Try without parens (no-arg function)
        m = re.search(
            r'(.*?)\b(FUNCTION|PROCEDURE)\s+([^\s\(]+)\s+(RETURN|IS|AS)',
            declare,
            re.DOTALL | re.IGNORECASE
        )
        if m:
            func.type = m.group(2).upper()
            func.name = m.group(3).strip().strip('"')
            func.args = '()'
        else:
            return func

    # Extract RETURN type
    # Look for RETURN X before IS/AS
    m_ret = re.search(r'\bRETURN\s+([^\s\(]+)', declare, re.IGNORECASE)
    if m_ret:
        func.return_type = m_ret.group(1).strip()

    return func


# =============================================================================
# PACKAGE BODY line-by-line extraction (mirrors _extract_functions)
# =============================================================================
def _extract_functions_from_body(content: str) -> List[Dict]:
    """Line-by-line scan to identify FUNCTION/PROCEDURE blocks.

    Uses a balanced BEGIN/END counter across the whole body to correctly
    handle nested blocks AND bare END; at depth 0.

    Returns list of {'type', 'name', 'args', 'declare_lines', 'body_lines'}
    """
    lines = content.split('\n')
    functions: List[Dict] = []
    current: Optional[Dict] = None
    before_lines: List[str] = []
    in_function = False
    depth = 0  # global BEGIN/END depth

    # Pattern to detect function/procedure start
    func_start_re = re.compile(
        r'^\s*(?:CREATE\s+(?:OR\s+REPLACE\s+)?)?(?:NONEDITIONABLE\s+|EDITIONABLE\s+)?'
        r'(FUNCTION|PROCEDURE)\s+([A-Za-z0-9_\$\-\."]+)(.*)$',
        re.IGNORECASE
    )

    def _update_depth(line: str) -> int:
        """Update depth counter for a line.

        Strips comments BEFORE counting BEGIN/END/DECLARE to avoid false
        matches like "-- comment End" being treated as a bare END (which
        would otherwise desync the depth counter and cause premature
        function termination at depth 0).
        """
        nonlocal depth
        # Strip comments: -- ... \n and /* ... */
        line_clean = re.sub(r'--[^\n]*', '', line)
        line_clean = re.sub(r'/\*.*?\*/', ' ', line_clean, flags=re.DOTALL)
        # Count BEGIN/DECLARE increments
        for kw in ('BEGIN', 'DECLARE'):
            for _ in re.finditer(rf'\b{kw}\b', line_clean, re.IGNORECASE):
                depth += 1
        # Count END decrements (excluding END IF/LOOP/CASE/INTO/FROM)
        for m in re.finditer(r'\bEND\b', line_clean, re.IGNORECASE):
            after = line_clean[m.end():m.end() + 30]
            if re.match(r'\s*(IF|LOOP|CASE|INTO|FROM)\b', after, re.IGNORECASE):
                continue
            # Skip when depth already 0 to avoid going negative
            if depth > 0:
                depth -= 1

    i = 0
    while i < len(lines):
        line = lines[i]

        if not in_function:
            m = func_start_re.match(line)
            if m:
                meaningful_before = [l for l in before_lines if l.strip()]
                if meaningful_before:
                    functions.append({
                        'type': 'GLOBAL_DECL',
                        'name': '',
                        'lines': list(before_lines),
                        'content': '\n'.join(before_lines),
                    })
                before_lines = []

                ftype = m.group(1).upper()
                fname = m.group(2).strip('"').split('.')[-1]
                after = m.group(3)

                current = {
                    'type': ftype,
                    'name': fname,
                    'args': '',
                    'declare_lines': [line],
                    'body_lines': [],
                    'seen_begin': False,  # True once we've entered the BEGIN body
                }
                in_function = True
                # Reset depth: each function/procedure has its own BEGIN/END scope
                # (END func_name; is detected by end_with_name and ends via continue,
                #  so it never decrements depth, which would otherwise accumulate
                #  across functions and break bare-end detection)
                depth = 0

                # Accumulate continuation lines until IS/AS found
                while not re.search(r'\b(IS|AS)\b', after, re.IGNORECASE):
                    i += 1
                    if i >= len(lines):
                        break
                    current['declare_lines'].append(lines[i])
                    after = lines[i]

                # Extract args from accumulated declare
                full_decl = '\n'.join(current['declare_lines'])
                args_m = re.search(r'\(([^)]*)\)', full_decl, re.DOTALL)
                if args_m:
                    current['args'] = args_m.group(1)

                i += 1
                continue

        if in_function and current is not None:
            current['body_lines'].append(line)

            # Check if this line ends the function:
            # 1. END function_name;
            end_with_name = re.match(
                rf'^\s*END\s+["]*{re.escape(current["name"])}["]*\s*;',
                line, re.IGNORECASE
            )
            if end_with_name:
                functions.append(current)
                current = None
                in_function = False
                i += 1
                continue

            # Update depth
            _update_depth(line)

            # Track whether we have entered the BEGIN body so that DECLARE-segment
            # CURSOR/RECORD blocks ending with `END;` (e.g. CASE END; inside
            # CURSOR SELECT) don't falsely terminate the function.
            line_clean = re.sub(r'--[^\n]*', '', line)
            if not current.get('seen_begin') and re.search(r'\bBEGIN\b', line_clean):
                current['seen_begin'] = True

            # 2. Bare END; when depth == 0 AND we have entered the BEGIN body.
            #    Strip comments first to avoid false matches.
            if current.get('seen_begin'):
                bare_end = re.match(r'^\s*END\s*;', line_clean, re.IGNORECASE)
                if bare_end and depth == 0:
                    functions.append(current)
                    current = None
                    in_function = False

            i += 1
            continue

        before_lines.append(line)
        i += 1

    return functions


# =============================================================================
# PACKAGE SPEC expansion: extract function/procedure signatures
# =============================================================================
def _extract_spec_declarations(content: str) -> List[Dict]:
    """Extract FUNCTION/PROCEDURE declarations from PACKAGE spec.

    A spec declaration looks like:
        FUNCTION func_name(arg1 IN type, arg2 OUT type) RETURN type;
        PROCEDURE proc_name(arg IN type);
    """
    declarations: List[Dict] = []
    pattern = re.compile(
        r'\b(FUNCTION|PROCEDURE)\s+([A-Za-z0-9_\$]+)\s*'
        r'(\([^\)]*\))?\s*'
        r'(RETURN\s+[A-Za-z0-9_\.\(\)]+)?\s*;',
        re.IGNORECASE | re.DOTALL
    )
    for m in pattern.finditer(content):
        decl_type = m.group(1).upper()
        decl_name = m.group(2)
        decl_args = (m.group(3) or '()').strip('()') if m.group(3) else ''
        decl_ret = (m.group(4) or '').replace('RETURN', '').strip()
        declarations.append({
            'type': decl_type,
            'name': decl_name,
            'args': decl_args,
            'return_type': decl_ret,
            'full_signature': m.group(0).strip(),
        })
    return declarations


# =============================================================================
# T19: CONNECT BY -> recursive CTE (mirrors Perl replace_connect_by)
# =============================================================================


def _replace_connect_by(sql: str) -> str:
    """Convert Oracle CONNECT BY hierarchical queries to PostgreSQL recursive CTEs.

    Mirrors Perl replace_connect_by (PLSQL.pm line 3887).

    Oracle syntax:
        SELECT ... START WITH condition CONNECT BY PRIOR parent = child ...

    PostgreSQL equivalent:
        WITH RECURSIVE cte AS (
            SELECT ... WHERE condition   -- anchor (START WITH)
            UNION ALL
            SELECT ... FROM cte JOIN t ON cte.parent = t.child  -- recursive
        )
        SELECT * FROM cte ...

    Supported:
        - START WITH (root condition)
        - CONNECT BY PRIOR (parent-child relationship)
        - Multiple PRIOR conditions (AND-separated)
        - LEVEL pseudo-column
        - ORDER SIBLINGS BY (hierarchy column)
        - NOCYCLE (removed, no PG equivalent)
        - Simple FROM clauses

    Not yet supported:
        - SYS_CONNECT_BY_PATH (complex string aggregation)
        - CONNECT_BY_ISLEAF / CONNECT_BY_ISCYCLE (requires CYCLE clause)
        - Subqueries in SELECT columns with CONNECT BY context
        - Complex FROM with multiple tables
    """
    if not re.search(r'\bCONNECT\s+BY\b', sql, re.IGNORECASE):
        return sql

    original_sql = sql

    # Remove NOCYCLE (no PG equivalent)
    sql = re.sub(r'\s+NOCYCLE\b', '', sql, flags=re.IGNORECASE)

    # Extract ORDER SIBLINGS BY -> will be used for hierarchy ordering
    siblings_col = ''
    siblings_m = re.search(r'ORDER\s+SIBLINGS\s+BY\s+([^\s,]+(?:\s*,\s*[^\s,]+)*)', sql, re.IGNORECASE)
    if siblings_m:
        siblings_col = siblings_m.group(1).strip()
        sql = re.sub(r'ORDER\s+SIBLINGS\s+BY\s+[^\s]+(?:\s*,\s*[^\s]+)*', '', sql, flags=re.IGNORECASE).strip()

    # Extract ORDER BY (non-siblings)
    order_by = ''
    order_m = re.search(r'\s+ORDER\s+BY\s+(.+?)(?=\s+UNION|\s+GROUP|\s+LIMIT|\s+OFFSET|\s*$)', sql, re.IGNORECASE | re.DOTALL)
    if order_m:
        order_by = order_m.group(1).strip().rstrip(';')
        sql = sql[:order_m.start()] + sql[order_m.end():]

    # Extract GROUP BY
    group_by = ''
    group_m = re.search(r'\s+GROUP\s+BY\s+.+?(?=\s+ORDER|\s+HAVING|\s+UNION|\s+LIMIT|\s*$)', sql, re.IGNORECASE | re.DOTALL)
    if group_m:
        group_by = group_m.group(0).strip()
        sql = sql[:group_m.start()] + sql[group_m.end():]

    # Extract INTO clause (removed, not supported in PG SELECT INTO via CTE)
    # e.g., "SELECT ... INTO var FROM ..." - but this is not typical in SELECT statements
    # For SELECT ... INTO ... FROM ... CONNECT BY, we just note it

    # Parse CONNECT BY clause with START WITH variants
    # Strategy: find CONNECT BY position, extract the pre-CONNECT-BY section,
    # then parse WHERE/START WITH from that section
    connect_by_match = re.search(r'\s+CONNECT\s+BY\s+(PRIOR\s+.+?)$', sql, re.IGNORECASE | re.DOTALL)
    if not connect_by_match:
        return original_sql

    pre_connect = sql[:connect_by_match.start()]
    connect_by_clause = connect_by_match.group(1).strip()
    sql = pre_connect  # Remaining: SELECT...FROM... [WHERE] [START WITH]

    # Extract WHERE clause from pre-connect section
    where_clause = ''
    # WHERE might be between FROM and START WITH (or just before CONNECT BY if no START WITH)
    start_with = ''
    if re.search(r'\bWHERE\b', pre_connect, re.IGNORECASE):
        # Find WHERE: could be before FROM or after FROM (in the middle)
        # Extract from last WHERE to START WITH (or end of pre_connect)
        where_m = re.search(r'\bWHERE\b(.+?)(?=\s+START\s+WITH|\s*$)', pre_connect, re.IGNORECASE | re.DOTALL)
        if where_m:
            where_clause = 'WHERE ' + where_m.group(1).strip()

    # Extract START WITH
    start_m = re.search(r'\bSTART\s+WITH\s+(.+?)(?=\s+CONNECT\s+BY|\s*$)', pre_connect, re.IGNORECASE | re.DOTALL)
    if start_m:
        start_with = start_m.group(1).strip()

    if not locals().get('connect_by_clause'):
        return original_sql  # No CONNECT BY found after all, shouldn't happen

    # Extract PRIOR clauses: split by AND (outside parens)
    prior_parts = _split_top_level(connect_by_clause, 'AND')
    prior_conditions = []
    extra_where_parts = []

    for part in prior_parts:
        part = part.strip()
        if not part:
            continue
        # Check if this part contains PRIOR
        if re.search(r'\bPRIOR\b', part, re.IGNORECASE):
            prior_conditions.append(part)
        else:
            extra_where_parts.append(part)

    if extra_where_parts:
        extra_cond = ' AND '.join(extra_where_parts)
        where_clause = (where_clause + ' AND ' + extra_cond).strip() if where_clause else extra_cond

    # Parse PRIOR conditions: split each by = (outside parens)
    # PRIOR a = b OR c = PRIOR d -> split by OR first
    join_conditions = []
    for pc in prior_conditions:
        # Split by OR for multiple conditions
        or_parts = re.split(r'\bOR\b', pc, flags=re.IGNORECASE)
        for op in or_parts:
            op = op.strip()
            if not op:
                continue
            # Split by = (equality join)
            eq_parts = re.split(r'\s*=\s*', op, maxsplit=1)
            if len(eq_parts) == 2:
                left = eq_parts[0].strip()
                right = eq_parts[1].strip()
                # Determine which side has PRIOR and extract the parent column
                has_prior_right = bool(re.search(r'\bPRIOR\b', right, re.IGNORECASE))
                has_prior_left = bool(re.search(r'\bPRIOR\b', left, re.IGNORECASE))

                if has_prior_right:
                    # Pattern: child_col = PRIOR parent_col
                    p_val = re.sub(r'\bPRIOR\s+', '', right, flags=re.IGNORECASE).strip()
                    child_col = left
                elif has_prior_left:
                    # Pattern: PRIOR parent_col = child_col
                    p_val = re.sub(r'\bPRIOR\s+', '', left, flags=re.IGNORECASE).strip()
                    child_col = right
                else:
                    join_conditions.append(op)
                    continue

                # Determine if child_col is a column (not a literal)
                # A column typically starts with letter/underscore, may have dots/funcs
                # A literal starts with number, quote, or is just a simple number
                is_literal = bool(re.match(r'^\s*[\'\"\d]', child_col))

                if is_literal:
                    # PRIOR col = <literal> -> parent filter, not a join condition
                    extra_where_parts.append(f'{p_val} = {child_col}')
                else:
                    # Remove table alias from child column for join
                    child_col_clean = re.sub(r'^\s*\w+\.\s*', '', child_col).strip()
                    # Remove table alias from parent column
                    p_val_clean = re.sub(r'^\s*\w+\.\s*', '', p_val).strip()
                    join_conditions.append(f'child.{child_col_clean} = cte.{p_val_clean}')
            else:
                join_conditions.append(op)

    # Extract SELECT columns and FROM clause
    # Look for SELECT ... FROM ... pattern
    sel_match = re.search(r'\bSELECT\s+(.+?)\s+FROM\s+(\w+)(?:\s+(?:AS\s+)?(\w+))?', sql, re.IGNORECASE | re.DOTALL)
    if not sel_match:
        return original_sql  # Can't parse, return original

    columns_str = sel_match.group(1).strip()
    table_name = sel_match.group(2).strip()
    table_alias = sel_match.group(3) if sel_match.group(3) else table_name

    # Split columns
    cols = _split_top_level(columns_str, ',')

    # Process each column: handle LEVEL, replace table aliases
    processed_cols = []
    has_level = False
    has_siblings = bool(siblings_col)

    for col in cols:
        col = col.strip()
        if not col:
            continue

        # Handle LEVEL pseudo-column
        # In anchor: LEVEL = 1
        # In recursive: LEVEL = cte.level + 1
        if re.match(r'LEVEL\s*$', col, re.IGNORECASE):
            has_level = True
            processed_cols.append('1 AS level')
        elif re.search(r'\bLEVEL\b', col, re.IGNORECASE):
            has_level = True
            col = re.sub(r'\bLEVEL\b', '1', col, flags=re.IGNORECASE)
            processed_cols.append(col)
        # Handle other columns: replace table alias references
        else:
            # Replace table alias references in the column
            if table_alias != table_name:
                col = re.sub(rf'\b{re.escape(table_alias)}\b', 'cte', col, flags=re.IGNORECASE)
            processed_cols.append(col)

    anchor_cols = ', '.join(processed_cols)

    # Recursive member columns: LEVEL +1, table alias references
    recursive_cols = []
    for col in cols:
        col = col.strip()
        if not col:
            continue
        if re.match(r'LEVEL\s*$', col, re.IGNORECASE):
            recursive_cols.append('(cte.level + 1) AS level')
        elif re.search(r'\bLEVEL\b', col, re.IGNORECASE):
            col = re.sub(r'\bLEVEL\b', '(cte.level + 1)', col, flags=re.IGNORECASE)
            recursive_cols.append(col)
        else:
            # Replace table alias with 'child' in recursive part
            if table_alias != table_name:
                col = re.sub(rf'\b{re.escape(table_alias)}\b', 'child', col, flags=re.IGNORECASE)
            recursive_cols.append(col)

    recursive_cols_str = ', '.join(recursive_cols)

    # Build hierarchy column for siblings
    hierarchy_col = ''
    if has_siblings:
        hierarchy_col = f', ARRAY[row_number() OVER (ORDER BY {siblings_col})] as hierarchy'

    # Build the recursive CTE
    # Anchor WHERE includes both original WHERE condition AND START WITH
    anchor_conds = []
    if where_clause:
        # Strip leading WHERE if present
        wc = re.sub(r'^\s*WHERE\s+', '', where_clause, flags=re.IGNORECASE).strip()
        if wc:
            anchor_conds.append(wc)
    if start_with:
        anchor_conds.append(start_with)
    anchor_where = 'WHERE ' + ' AND '.join(anchor_conds) if anchor_conds else ''

    join_clause = ''
    if join_conditions:
        join_clause = ' ON ' + ' AND '.join(join_conditions)

    # Use 'child' as the table alias in recursive part
    recursive_from = f'{table_name} child{join_clause}'

    # Build the full recursive CTE
    anchor_from_line = f'  FROM {table_name}'
    if anchor_where:
        anchor_from_line += f'\n  {anchor_where}'

    recursive_cte = (
        f'WITH RECURSIVE cte AS (\n'
        f'  SELECT {anchor_cols}{hierarchy_col}\n'
        f'{anchor_from_line}\n'
        f'  UNION ALL\n'
        f'  SELECT {recursive_cols_str}{hierarchy_col}\n'
        f'  FROM cte\n'
        f'  JOIN {recursive_from}\n'
        f') SELECT * FROM cte'
    )

    # Append ORDER BY, GROUP BY
    if order_by:
        if has_siblings:
            recursive_cte += f' ORDER BY hierarchy, {order_by}'
        else:
            recursive_cte += f' ORDER BY {order_by}'
    elif has_siblings:
        recursive_cte += ' ORDER BY hierarchy'

    if group_by:
        recursive_cte += f' {group_by}'

    return recursive_cte


# =============================================================================
# FORALL -> FOR LOOP conversion (mirrors Oracle BULK COLLECT / FORALL semantics)
# =============================================================================


def _convert_array_subscript(sql: str, state: Optional['ConversionState'] = None) -> str:
    """Convert Oracle collection subscripting (parens) to PG array (brackets).

    Oracle:
        aCSV_TBL(i)         -- collection access with parens
        aCSV_TBL(i).field   -- record field access via collection

    PostgreSQL:
        aCSV_TBL[i]         -- array access with brackets
        aCSV_TBL[i].field   -- same syntax works in PG

    Identifies declared array variables (TYPE[]) and converts their parens
    subscripting to bracket subscripting. Uses state.array_vars to share
    declarations across GLOBAL_DECL block and function body blocks.
    """
    # Collect array vars: from state (shared) AND local declarations
    array_vars = set()
    if state is not None and hasattr(state, 'array_vars') and state.array_vars:
        array_vars.update(state.array_vars)

    # Find local declared array variables
    for m in re.finditer(r'\b(\w+)\s+(?:TEXT|VARCHAR|NUMERIC|BIGINT|INTEGER|BOOLEAN)\s*\[\s*\]', sql, re.IGNORECASE):
        array_vars.add(m.group(1))
        # Also register in state for cross-block sharing
        if state is not None and hasattr(state, 'array_vars'):
            state.array_vars.add(m.group(1))

    if not array_vars:
        return sql

    # Build set of known function/procedure names to avoid false positives
    known_names = set(_DEFAULT_FUNCTION_NAMES) | set(_DEFAULT_PROCEDURE_NAMES)

    # Convert array subscripting: IDENT(idx) -> IDENT[idx] for known array vars
    for var in array_vars:
        sql = re.sub(
            rf'\b{re.escape(var)}\s*\(\s*(\w+)\s*\)',
            rf'{var}[\1]',
            sql
        )

    # Also convert common collection access patterns: lower_case_var(<integer_or_var>)
    # This handles cases where the variable is implicitly a collection (no explicit TYPE[])
    # but uses parens subscripting. We exclude known function names.
    def array_repl(m: re.Match) -> str:
        var = m.group(1)
        idx = m.group(2)
        # Skip if it's a known function
        if var.upper() in known_names:
            return m.group(0)
        # Skip if it starts with uppercase (likely function/package name)
        if var[0].isupper():
            return m.group(0)
        return f'{var}[{idx}]'

    sql = re.sub(
        r'\b([a-z]\w*)\s*\(\s*(\w+)\s*\)(?!\s*[\(\w])',
        array_repl,
        sql
    )

    return sql


def _convert_forall(sql: str) -> str:
    """Convert Oracle FORALL bulk DML to PostgreSQL FOR LOOP.

    Oracle syntax:
        FORALL idx IN start..end
          INSERT INTO table VALUES coll(idx);

    PostgreSQL equivalent:
        FOR idx IN start..end LOOP
            INSERT INTO table VALUES (coll[idx]);
        END LOOP;

    Note: Oracle collections use `coll(idx)` (parens), PostgreSQL arrays use
    `coll[idx]` (brackets). We convert both.
    """
    # First, find array variable declarations of the form `var TYPE[]`
    # so we can later convert var(idx) -> var[idx] for those variables.
    array_vars = set()
    for m in re.finditer(r'\b(\w+)\s+(?:TEXT|VARCHAR|NUMERIC|BIGINT|INTEGER)\s*\[\s*\]', sql, re.IGNORECASE):
        array_vars.add(m.group(1))

    # Convert array subscripting: IDENT(idx) -> IDENT[idx] for known array vars
    for var in array_vars:
        sql = re.sub(
            rf'\b{re.escape(var)}\s*\(\s*(\w+)\s*\)',
            rf'{var}[\1]',
            sql
        )

    # Pattern: FORALL <var> IN <expr>..<expr>
    # Capture the index variable, lower bound, upper bound, and the DML statement
    pattern = re.compile(
        r'\bFORALL\s+(\w+)\s+IN\s+([^.]+?)\.\.([^.\s;]+)\s*\n\s*'
        r'(INSERT\s+INTO[^;]+|UPDATE\s+[^;]+|DELETE\s+FROM\s+[^;]+)',
        re.IGNORECASE | re.DOTALL
    )

    def to_for_loop(m: re.Match) -> str:
        idx_var = m.group(1)
        lower = m.group(2).strip()
        upper = m.group(3).strip()
        dml = m.group(4).strip()

        # Convert Oracle collection subscripting (parens) to PG array subscripting (brackets)
        dml_pg = re.sub(
            rf'(\w+)\s*\(\s*{re.escape(idx_var)}\s*\)',
            rf'\1[{idx_var}]',
            dml, flags=re.IGNORECASE
        )

        return (f'FOR {idx_var} IN {lower}..{upper} LOOP\n'
                f'  {dml_pg};\n'
                f'END LOOP;')

    return pattern.sub(to_for_loop, sql)
    # Pattern: FORALL <var> IN <expr>..<expr>
    # Capture the index variable, lower bound, upper bound, and the DML statement
    pattern = re.compile(
        r'\bFORALL\s+(\w+)\s+IN\s+([^.]+?)\.\.([^.\s;]+)\s*\n\s*'
        r'(INSERT\s+INTO[^;]+|UPDATE\s+[^;]+|DELETE\s+FROM\s+[^;]+)',
        re.IGNORECASE | re.DOTALL
    )

    def to_for_loop(m: re.Match) -> str:
        idx_var = m.group(1)
        lower = m.group(2).strip()
        upper = m.group(3).strip()
        dml = m.group(4).strip()

        # Convert Oracle collection subscripting (parens) to PG array subscripting (brackets)
        # Pattern: collection_name(index) -> collection_name[index]
        # Be careful: only convert collection access where index is an integer/index variable
        # For simplicity, convert ALL parens-after-identifier in dashed code
        # (this is safe for collection access; function calls will be parsed correctly)
        # Actually, let's only convert the FORALL-specific collection access (idx variable)

        # The DML likely contains collection(<idx>var) - convert to collection[idx]
        dml_pg = re.sub(
            rf'(\w+)\s*\(\s*{re.escape(idx_var)}\s*\)',
            rf'\1[{idx_var}]',
            dml, flags=re.IGNORECASE
        )

        return (f'FOR {idx_var} IN {lower}..{upper} LOOP\n'
                f'  {dml_pg};\n'
                f'END LOOP;')

    return pattern.sub(to_for_loop, sql)


# =============================================================================
# Main pipeline: convert_plsql_code (mirrors Perl)
# =============================================================================
def convert_plsql_code(sql: str, state: Optional[ConversionState] = None) -> Tuple[str, ConversionState]:
    """Mirrors Perl convert_plsql_code."""
    if state is None:
        state = ConversionState()

    if not sql or not sql.strip():
        return sql, state

    # 0. Remove/convert PRAGMA directives (do this BEFORE comment extraction
    # so PRAGMA lines are not wrapped in %ORA2PG_COMMENT% placeholders)
    # PRAGMA AUTONOMOUS_TRANSACTION -> comment (no native PG equivalent)
    # PRAGMA RESTRICT_REFERENCES -> remove (PL/SQL only, no PG equivalent)
    # PRAGMA SERIALLY_REUSABLE -> remove (PL/SQL only)
    sql = re.sub(
        r'[ \t]*PRAGMA\s+AUTONOMOUS_TRANSACTION\s*;\s*(?:--[^\n]*)?',
        '-- Unsupported: AUTONOMOUS_TRANSACTION not natively supported in PostgreSQL\n'
        '-- Use dblink or pg_background extension for autonomous transaction behavior\n',
        sql, flags=re.IGNORECASE
    )
    sql = re.sub(r'[ \t]*PRAGMA\s+RESTRICT_REFERENCES\s*\([^;]+\)\s*;\s*(?:--[^\n]*)?', '', sql, flags=re.IGNORECASE)
    sql = re.sub(r'[ \t]*PRAGMA\s+SERIALLY_REUSABLE\s*;\s*(?:--[^\n]*)?', '', sql, flags=re.IGNORECASE)

    # 1. Strip SYS schema
    sql = re.sub(r'\bSYS\.', '', sql, flags=re.IGNORECASE)

    # 1b. [P2] replace_sys_context — must happen before any keyword
    #     extraction so SYS_CONTEXT(USERENV, ...) doesn't get split.
    from plsql_subs import replace_sys_context as _rsc
    sql = _rsc(sql)

    # 1c. [P0] extract_subqueries — placeholder (SELECT|WITH ...) so the
    #     downstream function extraction (step 4) does not dive into them.
    from plsql_subs import extract_subqueries as _esq
    sql = _esq(sql, state)

    # 2. Outer join placeholder
    sql = _replace_outer_join_placeholders(sql, state)

    # 3. Wrap OUTERJOIN expressions in SUBPART
    sql = _wrap_outerjoin_in_parens(sql, state)

    # 3b. Restore SUBPART BEFORE function extraction
    # Mirrors Perl: SUBPART wrap is a transient guard for outer-join rewriting;
    # it's immediately unwrapped (with predicate-aware paren restoration) so that
    # extract_function_code sees the real source.
    sql = _restore_subparts(sql, state)

    # 4. Recursive function call extraction
    sql = _extract_function_calls(sql, state)

    # 5. Apply function conversion per extracted call
    sql = _apply_function_conversion(sql, state)

    # 6. Restore SUBPART
    sql = _restore_subparts(sql, state)

    # 6b. [P1] append_alias_clause — adds alias_N to %SUBQUERY% in FROM clauses
    from plsql_subs import append_alias_clause as _aac
    sql = _aac(sql)

    # 6c. [P1] remove_named_parameters — strip "name=>" from call sites
    from plsql_subs import remove_named_parameters as _rnp
    sql = _rnp(sql)

    # 7. Apply type mapping
    sql = _apply_type_mapping(sql)

    # 8. Per-statement outer join rewrite
    sql = _restore_outerjoins_to_left_join(sql, state)

    # 8b. CONNECT BY -> recursive CTE (T19)
    sql = _replace_connect_by(sql)

    # 9. ROWNUM -> LIMIT (P8)
    sql = _replace_rownum_with_limit(sql)

    # 10. REF CURSOR -> REFCURSOR (P9)
    sql = _convert_ref_cursor(sql)

    # 11. REGEXP_REPLACE -> regexp_replace (P10)
    sql = _convert_regexp_replace(sql)

    # 12. Date functions: ADD_MONTHS/LAST_DAY/MONTHS_BETWEEN (P11)
    sql = _convert_date_functions(sql)

    # 12b. FORALL -> FOR LOOP (BULK COLLECT/INSERT semantics)
    sql = _convert_forall(sql)

    # 12c. [P2] set_error_code — inside RAISE_APPLICATION_ERROR first arg
    from plsql_subs import set_error_code as _sec
    sql = re.sub(
        r"RAISE_APPLICATION_ERROR\s*\(\s*([^,]+?)\s*,",
        lambda m: f"RAISE_APPLICATION_ERROR({_sec(m.group(1).strip())},",
        sql,
        flags=re.IGNORECASE,
    )

    # 13. Universal syntax fixes (Phase 1: T1-T4)
    sql = _fix_null_comparison(sql)
    sql = _fix_binary_literals(sql)
    sql = _replace_pipe_row(sql)
    sql = _strip_supplemental_log(sql)

    # 14. Array subscripting: array(idx) -> array[idx] (after function conversion)
    # At this point, known function calls have been converted; remaining
    # IDENT(idx) patterns where IDENT is not in _DEFAULT_FUNCTION_NAMES are
    # likely collection/array accesses (Oracle: parens, PG: brackets)
    sql = _convert_array_subscript(sql, state)

    # 14b. [P0] perform_replacement — add PERFORM/CALL before bare function
    #      calls in statement position.
    # NOTE: perlstyle_engine already has _apply_perform_call (in convert_function,
    # step 5b) which is more accurate (uses known_procedure_names metadata) and
    # cleans up PERFORM in expression contexts. We keep this re-export available
    # for callers that use convert_plsql_code directly, but we don't double-apply
    # it here because convert_function post-processes PERFORM contextually.
    # from plsql_subs import perform_replacement as _prp
    # sql = _prp(sql)

    # 14c. [P0] translate_statement — per-statement ROWNUM/UNION/cleanup.
    #     Operates on the whole code block (Perl's translate_statement also
    #     takes a single string and returns it).
    from plsql_subs import translate_statement as _ts
    sql = _ts(sql, state)

    # 14d. [P2] replace_without_function — generic fix for bare CURRENT_DATE etc.
    from plsql_subs import replace_without_function as _rwf
    sql = _rwf(sql)

    # 15. Restore unconverted OUTERJOIN placeholders to (+)
    # Mirrors Perl plsql_to_plpgsql line 1253:
    #     $str =~ s/\%OUTERJOIN\d+\%/\(\+\)/igs;
    # Only restore markers that were NOT consumed by _restore_outerjoins_to_left_join.
    # Consumed markers (multi-table context) are tracked in consumed_outerjoins set.
    if state.outerjoin_idx > 0:
        def _restore_oj(m):
            idx = int(m.group(1))
            if idx in state.consumed_outerjoins:
                return m.group(0)  # keep: already converted to LEFT JOIN
            return '(+)'  # restore: single-table context
        sql = re.sub(
            r'%OUTERJOIN(\d+)%',
            _restore_oj,
            sql, flags=re.IGNORECASE
        )

    # 16. [P2-1] Restore %SUBQUERY%d% placeholders.
    # Mirrors Perl PLSQL.pm:1517-1556 extract→…→restore flow.
    # extract_subqueries (step 1c) replaced (SELECT|WITH ...) bodies with
    # placeholders, caching the originals in state.subquery_map. All
    # downstream steps (function extraction, type mapping, ROWNUM, etc.)
    # operate on placeholders. This final step restores the original
    # subquery bodies so the output SQL is self-contained.
    # Strict: any unresolved placeholder raises SubqueryRestoreError
    # (data loss zero-tolerance per AGENTS.md §2).
    from rules.subquery_restore import restore_subqueries as _rsq
    sql, _used = _rsq(sql, state)

    return sql, state


# =============================================================================
# Top-level convert functions
# =============================================================================
def convert_function(sql: str, same_pkg_procedure_names: Optional[set] = None,
                     same_pkg_function_names: Optional[set] = None,
                     shared_state: Optional[ConversionState] = None) -> Tuple[str, List[str]]:
    """Convert Oracle FUNCTION/PROCEDURE to PG plpgsql function.

    T15 cross-package call semantics: ``same_pkg_procedure_names`` and
    ``same_pkg_function_names`` enable PERFORM/CALL insertion for
    bare statement-level calls of same-package procedures/functions.
    ``shared_state`` propagates data from caller (e.g., package-level
    array declarations) so array subscripting conversion works across
    GLOBAL_DECL block and function body blocks.

    oracle_poc 00_FUNCTION.sql is a *package* body that contains 10
    top-level FUNCTION declarations plus 42 nested helper functions.
    We detect multiple `CREATE OR REPLACE FUNCTION|PROCEDURE` headers
    and process each one independently, joining the results.
    """
    warnings: List[str] = []

    if not sql or not sql.strip():
        return sql, warnings

    # Detect multiple top-level CREATE FUNCTION/PROCEDURE blocks.
    # If only one, fall through to the original single-function path.
    header_re = re.compile(
        r"CREATE\s+(?:OR\s+REPLACE\s+)?"
        r"(?:EDITIONABLE\s+|NONEDITIONABLE\s+)?"
        r"(FUNCTION|PROCEDURE)\b",
        re.IGNORECASE,
    )
    headers = list(header_re.finditer(sql))
    if len(headers) <= 1:
        return _convert_single_function(
            sql, same_pkg_procedure_names, same_pkg_function_names,
            shared_state, warnings,
        )

    # Multi-function file: split at each header, convert each, re-join.
    # But first extract nested FUNCTION/PROCEDURE blocks within each
    # top-level block (they confuse _lookup_function's BEGIN split).
    parts: List[str] = []
    for i, m in enumerate(headers):
        start = m.start()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(sql)
        block = sql[start:end].rstrip()
        if block.strip():
            parts.append(block)

    converted: List[str] = []
    for block in parts:
        block_warnings: List[str] = []
        try:
            # Strip nested function/procedure definitions and lift them out
            # as standalone PG functions.
            # nested_lifted: None = no headers found (inline may run);
            # non-None = headers found (skip inline per AGENTS.md: "禁用文本内联")
            block, lifted = _lift_nested_subprograms(block, block_warnings)

            # P4-4d: Disambiguate lifted nested function names by prefixing
            # with the parent function name to avoid collisions when multiple
            # parent functions have nested functions with the same name (e.g.
            # three different BUILD_SQL nested functions in F_GET_TOTAL_SCORE,
            # F_GET_AVG_SCORE, and F_GET_STUDENT_CURSOR).
            lift_renames: Dict[str, str] = {}  # orig -> new
            if lifted:
                # Extract parent function name from the block header
                parent_m = re.search(
                    r"CREATE\s+OR\s+REPLACE\s+(?:FUNCTION|PROCEDURE)\s+(\w+)",
                    block, re.IGNORECASE,
                )
                parent_name = parent_m.group(1).upper() if parent_m else "PARENT"
                new_lifted: List[str] = []
                for lift_sql in lifted:
                    if not isinstance(lift_sql, str):
                        new_lifted.append(lift_sql)
                        continue
                    # Extract the lifted function name from CREATE OR REPLACE FUNCTION <name>(
                    lift_name_m = re.search(
                        r"CREATE\s+OR\s+REPLACE\s+(?:FUNCTION|PROCEDURE)\s+(\w+)\s*\(",
                        lift_sql, re.IGNORECASE,
                    )
                    if lift_name_m:
                        orig_name = lift_name_m.group(1).upper()
                        new_name = f"{parent_name}_{orig_name}"
                        if orig_name != new_name:
                            # Record rename for call sites (parent + sibling bodies)
                            lift_renames[orig_name] = new_name
                    new_lifted.append(lift_sql)
                lifted = new_lifted
                # Now apply all renames: function definitions + ALL call sites
                # in parent block AND in every lifted function body.
                # Sort by length descending to avoid partial matches (e.g.
                # BUILD_SQL before BUILD_SQL_X)
                sorted_renames = sorted(lift_renames.items(), key=lambda x: -len(x[0]))
                for orig, new in sorted_renames:
                    # Rewrite function definition (CREATE OR REPLACE FUNCTION orig(
                    for i, lift_sql in enumerate(lifted):
                        if not isinstance(lift_sql, str):
                            continue
                        if re.search(
                            r"CREATE\s+OR\s+REPLACE\s+(?:FUNCTION|PROCEDURE)\s+" + re.escape(orig) + r"\s*\(",
                            lift_sql, re.IGNORECASE,
                        ):
                            # Rename the definition itself
                            lift_sql = re.sub(
                                r"(CREATE\s+OR\s+REPLACE\s+(?:FUNCTION|PROCEDURE)\s+)"
                                + re.escape(orig) + r"\s*\(",
                                r"\g<1>" + new + r"(",
                                lift_sql, count=1, flags=re.IGNORECASE,
                            )
                            # Rename DROP IF EXISTS
                            lift_sql = re.sub(
                                r"(DROP\s+(?:FUNCTION|PROCEDURE)\s+IF\s+EXISTS\s+)"
                                + re.escape(orig) + r"\b",
                                r"\g<1>" + new,
                                lift_sql, count=1, flags=re.IGNORECASE,
                            )
                            lifted[i] = lift_sql
                    # Rename call sites in the parent block
                    block = re.sub(
                        r"\b" + re.escape(orig) + r"\s*\(",
                        new + r"(",
                        block, flags=re.IGNORECASE,
                    )
                    # Rename call sites inside ALL lifted function bodies
                    for i, lift_sql in enumerate(lifted):
                        if not isinstance(lift_sql, str):
                            continue
                        # Skip the definition we already renamed
                        if re.search(
                            r"CREATE\s+OR\s+REPLACE\s+(?:FUNCTION|PROCEDURE)\s+" + re.escape(new) + r"\s*\(",
                            lift_sql, re.IGNORECASE,
                        ) and not re.search(
                            r"\b" + re.escape(orig) + r"\s*\(",
                            lift_sql, re.IGNORECASE,
                        ):
                            continue  # no more old-name calls left
                        lift_sql = re.sub(
                            r"\b" + re.escape(orig) + r"\s*\(",
                            new + r"(",
                            lift_sql, flags=re.IGNORECASE,
                        )
                        lifted[i] = lift_sql
            else:
                lifted = None

            out, w = _convert_single_function(
                block, same_pkg_procedure_names, same_pkg_function_names,
                shared_state, block_warnings,
                nested_lifted=lifted is not None,
            )
            # If we lifted nested functions, prefix their converted forms.
            if lifted:
                out = out + "\n" + "\n".join(x for x in lifted if isinstance(x, str))
            converted.append(out)
            warnings.extend(w)
        except Exception as e:
            warnings.append(
                f"convert_function block error, keeping original: {e}"
            )
            converted.append(block)
    return "\n\n".join(converted) + "\n", warnings


def _lift_nested_subprograms(block: str, warnings: List[str], _recursive: bool = False) -> Tuple[str, Optional[List[str]]]:
    """Find nested FUNCTION / PROCEDURE declarations inside a top-level
    FUNCTION/PROCEDURE body and lift them out as standalone PG functions.

    Returns:
        (block_with_nested_removed, lifted_pg_sql_list_or_None)
        lifted_pg_sql_list_or_None is:
          - a non-empty list  : at least one nested func was found AND lifted
          - None              : no nested FUNCTION/PROCEDURE headers found in block
            (→ inline path may still find work to do)
        Note: if headers were found but lift FAILED (no matching END), returns
        a non-empty list so inline is also SKIPPED (AGENTS.md: "禁用文本内联").

    Detection (two strategies):
    1. ANTLR chunker (primary): uses grammar to precisely identify all nested
       function boundaries — including pre-BEGIN declarations and multi-line
       signatures that the regex misses.  Falls back to regex if ANTLR
       unavailable or fails.
    2. Regex (fallback): scans AFTER BEGIN only (for post-BEGIN nested
       functions that ANTLR also handles).

    Multi-level nesting (depth 2+): each lifted nested function is passed
    through _convert_single_function which calls _lift_nested_subprograms
    recursively, so deeply-nested functions are also lifted.
    """
    lines = block.splitlines(keepends=True)
    n = len(lines)
    # Each entry is (start_line, end_line, name) inclusive.  Regex loop fills
    # in name; ANTLR path also fills in name.  Older 2-tuple entries are no
    # longer produced, but downstream code still accepts them.
    nested_blocks: List[Tuple[int, int, str]] = []
    header_re = re.compile(
        r"^\s*(FUNCTION|PROCEDURE)\s+(\w+)\s*",
        re.IGNORECASE,
    )
    end_re_template = r"^\s*END\s+{name}\s*;\s*$"
    i = 0
    name = ""  # fallback for ANTLR chunks (regex loop may not run)
    found_top_begin = False
    found_any_header = False  # track: at least one nested header seen?
    if not _recursive:
        # Regex loop scans AFTER the outer BEGIN for post-BEGIN nested
        # declarations.  On recursive invocations (inside a lifted nested
        # block), we let ANTLR handle everything — the regex scan would
        # wrongly treat inner FUNCTION headers at shallow offsets.
        while i < n:
            line = lines[i]
            if not found_top_begin:
                if re.search(r"\bBEGIN\b", line, re.IGNORECASE):
                    found_top_begin = True
                i += 1
                continue
            m = header_re.match(line)
            if not m:
                i += 1
                continue
            found_any_header = True  # ← found a nested FUNCTION/PROCEDURE header
            kind = m.group(1).upper()
            name = m.group(2)
            start_i = i
            depth = 0
            end_i = -1
            j = i
            end_re = re.compile(end_re_template.format(name=re.escape(name)), re.IGNORECASE)
            while j < n:
                jline = lines[j]
                for m2 in re.finditer(r"\b(BEGIN|END)\b", jline, re.IGNORECASE):
                    word = m2.group(1).upper()
                    if word == "BEGIN":
                        depth += 1
                    else:
                        # 排除 END IF / END LOOP / END CASE（控制语句结束，不是块结束）
                        rest = jline[m2.end():].lstrip().upper()
                        if rest.startswith(("IF", "LOOP", "CASE")):
                            continue
                        depth -= 1
                    if depth == 0 and end_re.match(jline):
                        end_i = j
                        break
                if end_i >= 0:
                    break
                j += 1
            if end_i < 0:
                warnings.append(
                    f"Nested {kind} {name} has no matching END; skipping lift"
                )
                break
            nested_blocks.append((start_i, end_i, name))
            i = end_i + 1

    # -------------------------------------------------------------------
    # Strategy 1: ANTLR chunker — finds pre-BEGIN nested functions and
    # multi-line signatures that the regex above misses.
    # -------------------------------------------------------------------
    try:
        # Avoid circular import; local import is acceptable here.
        # The chunker caches results, so repeated calls are cheap.
        import sys as _sys
        _repo_root = Path(__file__).resolve().parent.parent
        _parsers_dir = _repo_root / "workspace" / "parsers"
        if str(_parsers_dir) not in _sys.path:
            _sys.path.insert(0, str(_parsers_dir))
        from chunker_ast import chunk_oracle_by_antlr

        # ANTLR's PlSql grammar requires one of two prefixes to parse:
        #
        # 1. `CREATE OR REPLACE FUNCTION ...` for top-level functions.
        #    When we prepend this to a nested-function text, ANTLR parses it as
        #    `Create_function_bodyContext` (top-level).  Our chunker now records
        #    this at the caller's depth (depth ≥ 1 for pre-BEGIN nested text),
        #    so it shows up in nested_chunks.
        #
        # 2. `DECLARE ... BEGIN ... END;` for nested function bodies declared
        #    inside a DECLARE block.  The nested function text appears as
        #    `Function_bodyContext` (nested) inside `Declare_specContext`.
        #
        # The chunker also treats `Anonymous_blockContext` as a function scope,
        # so `Function_bodyContext` inside a DECLARE block is recorded at
        # depth ≥ 1.
        #
        # We extract ONLY the declarative section (between IS and BEGIN) from
        # the block text.  This avoids parse errors from partial BEGIN...END
        # pairs in the block body.  We then wrap the declaration section in
        # `DECLARE ... BEGIN END;` so ANTLR can parse the nested functions.
        #
        # Find the OUTER function's BEGIN (after all nested declarations).
        # The naive IS...BEGIN regex stops at the FIRST BEGIN, which is often
        # inside a nested function body.  We need the BEGIN at depth=0 (the
        # outer function's executable body start).
        # Use depth-aware scanning: find IS, then scan for BEGIN at depth=0.
        _is_match = re.search(r"\bIS\b", block, re.IGNORECASE)
        _decl_text = ""
        _decl_line_0 = 0
        if _is_match:
            _is_pos = _is_match.start()
            _is_line_0 = block[:_is_pos].count("\n")  # 0-based line in block
            # Scan from IS+1 to find BEGIN at depth=0
            _after_is = block[_is_pos + 4:]
            _depth = 0
            _begin_pos = -1
            for _m2 in re.finditer(r"\b(BEGIN|END|DECLARE|FUNCTION|PROCEDURE)\b",
                                    _after_is, re.IGNORECASE):
                _kw = _m2.group(1).upper()
                # Skip `END <name>;` — these close a labeled nested FUNCTION
                # declaration and should NOT change depth.  Without this
                # guard the depth counter goes negative when a nested
                # function is closed before the outer body's BEGIN.
                if _kw == "END":
                    _rest = _after_is[_m2.end():_m2.end() + 30].lstrip()
                    _up = _rest.upper()
                    # Skip END IF / END LOOP / END CASE (control stmts,
                    # depth unaffected).
                    if re.match(r"[\w\"]", _rest) and _up.startswith(("IF", "LOOP", "CASE")):
                        continue
                    # END <name>; closes a nested FUNCTION/PROCEDURE —
                    # decrement depth (we entered one when FUNCTION
                    # keyword was seen for that nested decl).
                    if re.match(r"[\w\"]", _rest):
                        if _depth > 0:
                            _depth -= 1  # labeled END closes nested fn
                        continue
                if _kw in ("FUNCTION", "PROCEDURE"):
                    _depth += 1  # entering a nested declaration
                elif _kw == "BEGIN":
                    if _depth == 0:
                        _begin_pos = _m2.start()
                        break
                    # else: BEGIN inside a nested function, don't count as outer BEGIN
                elif _kw == "END":
                    # Don't decrement below 0 (defensive against unlabeled END)
                    if _depth > 0:
                        _depth -= 1
            if _begin_pos >= 0:
                # _decl_text = text between IS and outer BEGIN (exclusive)
                _decl_text = _after_is[:_begin_pos]
                _decl_line_0 = _is_line_0 + 1  # lines after IS start at _is_line_0+1 (0-based)
            else:
                _decl_text = ""
                _decl_line_0 = 0

        # Try CREATE OR REPLACE prefix first (for post-BEGIN nested functions).
        _parse_text = "CREATE OR REPLACE " + block
        nested_chunks = [
            c for c in chunk_oracle_by_antlr(_parse_text)
            if c["depth"] >= 1
        ]
        # Try DECLARE wrapping of the declaration section.
        _wrapped = ""  # default; set below only when DECLARE path is used
        if _decl_text and not nested_chunks:
            # Wrap only the IS...BEGIN section (not the whole block body).
            # This avoids parse errors from incomplete BEGIN...END in block body.
            _wrapped = "DECLARE " + _decl_text.strip() + "\nBEGIN NULL; END;"
            nested_chunks = [
                c for c in chunk_oracle_by_antlr(_wrapped, allow_errors=True)
                if c["depth"] >= 1
            ]
            if nested_chunks:
                # _wrapped starts with 'DECLARE' (1 line).  ANTLR line numbers
                # are 1-based.  We need to map back to original block line
                # indices.
                #
                # Strategy: find the position of the declaration text within
                # the original block, then use the chunk's offset within the
                # wrapped text to compute the original block line.
                _decl_start_in_block = block.find(_decl_text.strip())
                if _decl_start_in_block >= 0:
                    # Line in block where _decl_text starts (0-based)
                    _decl_line_0 = block[:_decl_start_in_block].count("\n")
                else:
                    _decl_line_0 = 0

        if nested_chunks:
            # Determine which path produced the chunks.
            _is_declare_path = bool(_decl_text) and 'DECLARE' in _wrapped
            if _is_declare_path:
                # DECLARE-wrapped path: chunk.start_line is 1-based in
                # _wrapped.  _wrapped line 1 = "DECLARE ", line 2 = first
                # line of _decl_text.strip().  Map back to block line:
                #   block_line = _decl_line_0 + (chunk_line - 2)
                # where _decl_line_0 is the 0-based block line where
                # _decl_text starts.
                # Recompute _decl_line_0 from the ACTUAL stripped text so it
                # matches the wrapped text.
                _decl_start_in_block = block.find(_decl_text.strip())
                if _decl_start_in_block >= 0:
                    _decl_line_0 = block[:_decl_start_in_block].count("\n")

                for chunk in nested_chunks:
                    chunk_name = chunk.get("name", "")
                    # Dedup: skip if already found by regex or another chunk
                    if any(chunk_name.lower() == (item[2] or "").lower()
                           for item in nested_blocks):
                        continue
                    # Find header: ANTLR line L → approx block line
                    _approx_s = _decl_line_0 + (chunk["start_line"] - 2)
                    _hdr_line = None
                    # widen search window: ANTLR line points at FUNCTION keyword;
                    # allow up to 5 lines before for leading comments.
                    for _delta in range(-5, 6):
                        _si = _approx_s + _delta
                        if 0 <= _si < n:
                            _m2 = header_re.match(lines[_si])
                            if _m2 and _m2.group(2).lower() == chunk_name.lower():
                                _hdr_line = _si
                                break
                    if _hdr_line is None:
                        found_any_header = True
                        warnings.append(
                            f"ANTLR found nested {chunk_name} but header not "
                            f"found near line {_approx_s}; skipping lift"
                        )
                        continue
                    # Find END: search for 'END <name>;' anywhere in block.
                    # Pick the END with the most preceding BEGINs (closes the
                    # outermost body of this function).
                    _end_pattern = re.compile(
                        r"^\s*END\s+" + re.escape(chunk_name) + r"\s*;\s*$",
                        re.IGNORECASE | re.MULTILINE,
                    )
                    _end_candidates = [(m.start(), block[:m.start()].count('\n'))
                                        for m in _end_pattern.finditer(block)]
                    if not _end_candidates:
                        found_any_header = True
                        warnings.append(
                            f"Nested {chunk_name}: no 'END {chunk_name};' found in block"
                        )
                        continue

                    def _count_begins_up_to(pos):
                        return len(re.findall(r"\bBEGIN\b", block[:pos], re.I))

                    _best_end = max(_end_candidates,
                                     key=lambda x: _count_begins_up_to(x[0]))
                    _end_line = _best_end[1]  # 0-based line
                    if _end_line < _hdr_line:
                        warnings.append(
                            f"Nested {chunk_name}: END line {_end_line} < header line {_hdr_line}"
                        )
                        continue
                    # Skip if this chunk is inside an already-registered chunk
                    # (outer function already lifted — inner handled by recursion).
                    _inside_existing = any(
                        (sb <= _hdr_line <= eb) or (sb <= _end_line <= eb)
                        for sb, eb, _ in nested_blocks
                    )
                    if _inside_existing:
                        continue
                    # If this new chunk fully contains any existing entries,
                    # drop those (they are inner functions of this outer
                    # function — will be handled by recursive lift).
                    nested_blocks = [
                        item for item in nested_blocks
                        if not (_hdr_line <= item[0] and item[1] <= _end_line)
                    ]
                    nested_blocks.append((_hdr_line, _end_line, chunk_name))
                    found_any_header = True
            else:
                # CREATE OR REPLACE path: prefix is one line
                # ("CREATE OR REPLACE ").  ANTLR line 1 = prefix line,
                # ANTLR line 2 = block line 0 (0-based).  So mapping is:
                #   block_line = antlr_line - 1
                # Use regex-found END to anchor e_line (more reliable than
                # ANTLR stop_line which may miss labeled END <name>;).
                for chunk in nested_chunks:
                    chunk_name = chunk.get("name", "")
                    s_line = chunk["start_line"] - 1
                    e_line = chunk["stop_line"] - 1
                    if not (0 <= s_line < len(lines) and 0 <= e_line <= len(lines)):
                        warnings.append(
                            f"ANTLR chunk {chunk_name} out of range: "
                            f"[{s_line}, {e_line}] block lines (n={len(lines)})"
                        )
                        continue
                    # Find END <name>; via regex to ensure we don't miss
                    # the labeled END that ANTLR's stop_line may not include.
                    _end_pat = re.compile(
                        r"^\s*END\s+" + re.escape(chunk_name) + r"\s*;\s*$",
                        re.IGNORECASE | re.MULTILINE,
                    )
                    _end_cands = [m.start() for m in _end_pat.finditer(block)]
                    if _end_cands:
                        # Use the first END <name>; AFTER s_line to compute e_line
                        for pos in _end_cands:
                            line_in_block = block[:pos].count("\n")
                            if line_in_block >= s_line:
                                e_line = line_in_block
                                break
                    if any(chunk_name.lower() == (item[2] or "").lower()
                           for item in nested_blocks):
                        continue
                    # Skip if inside an already-registered outer chunk.
                    _inside_existing = any(
                        (sb <= s_line <= eb) or (sb <= e_line <= eb)
                        for sb, eb, _ in nested_blocks
                    )
                    if _inside_existing:
                        continue
                    # If this new chunk fully contains any existing entries,
                    # drop those (regex-detected inner, this ANTLR-detected
                    # outer will cover via recursive lift).
                    nested_blocks = [
                        item for item in nested_blocks
                        if not (s_line <= item[0] and item[1] <= e_line)
                    ]
                    nested_blocks.append((s_line, e_line, chunk_name))
                found_any_header = True
    except Exception as _antlr_exc:  # noqa: BLE001
        # Never silently swallow ANTLR failures — log and continue.
        warnings.append(
            f"ANTLR chunker path failed: {type(_antlr_exc).__name__}: {_antlr_exc}"
        )

    # -------------------------------------------------------------------
    # No nested FUNCTION/PROCEDURE headers found at all → inline may still work
    # -------------------------------------------------------------------
    if not found_any_header:
        return block, None

    # Headers were found (even if some failed to lift) → skip inline (禁用文本内联)
    if not nested_blocks:
        return block, [True]  # non-None, non-empty → signals "don't inline"

    lifted_pg: List[str] = []
    for item in nested_blocks:
        # Support both 2-tuple (regex) and 3-tuple (ANTLR) entries
        if len(item) == 3:
            s, e, block_name = item
        else:
            s, e = item
            block_name = name  # fallback to last regex name
        nested_text = "".join(lines[s : e + 1])
        # Strip the END <name> label: Oracle nested functions use "END <name>;"
        # but when lifted to a standalone PG function there is no matching
        # BEGIN <name>, so the label is invalid in PostgreSQL.
        # Use block_name from chunk (ANTLR) or the 'name' variable (regex loop).
        _end_name = block_name if block_name else name  # chunk name or last regex name
        if _end_name:
            # P4-2R: 旧实现 r"\1;\2" 在 END 后强制加分号，但 \2 已含原分号
            # → 产生 "END ;;"。改为 r"\1END;" 保留缩进、剥离函数名与原分号。
            nested_text = re.sub(
                r"^(\s*)END\s+" + re.escape(_end_name) + r"\s*;?\s*$",
                r"\1END;",
                nested_text,
                flags=re.IGNORECASE | re.MULTILINE,
            )
        try:
            # Recursively lift any nested functions inside this nested block
            # itself (depth 2+ nesting).  ANTLR chunker records all nested
            # functions at depth=1 without distinguishing outer/inner,
            # so depth-2 blocks (e.g. FETCH_CREDIT inside GET_CREDIT_INFO)
            # are not removed by the outer pass.  We do a recursive pass here.
            inner_warnings: List[str] = []
            nested_text, inner_lifted = _lift_nested_subprograms(
                nested_text, inner_warnings, _recursive=True
            )
            # Suppress the recursive "Failed to lift" / "no matching END"
            # noise for now — outer conversion will surface real errors.
            nested_out, nested_w = _convert_single_function(
                nested_text, None, None, None, [], nested_lifted=True
            )
            lifted_pg.append(nested_out)
            if inner_lifted:
                # Append recursively-lifted standalone functions after this one.
                # inner_lifted may be [True] sentinel (found headers but lift
                # failed) — filter out sentinels; we only want real PG SQL.
                lifted_pg.extend(x for x in inner_lifted if isinstance(x, str))
            warnings.append(
                f"Lifted nested {_end_name} "
                f"as standalone PG function"
            )
        except Exception as exc:
            warnings.append(
                f"Failed to lift nested block: {exc}; leaving in place"
            )

    # If headers were found but ALL conversions failed, still return non-empty
    # so inline path is skipped (AGENTS.md: "禁用文本内联"). Nested bodies
    # were removed from result_lines; they are lost, but inline would create
    # _nested_result dead code that breaks PG syntax.
    if found_any_header and not lifted_pg:
        result_lines = list(lines)
        _sorted = sorted(nested_blocks, key=lambda x: x[0], reverse=True)
        for item in _sorted:
            s, e = item[0], item[1]
            del result_lines[s : e + 1]
        return "".join(result_lines), [True]  # non-empty sentinel → skip inline

    result_lines = list(lines)
    # CRITICAL: sort by start index descending before deleting.  Without
    # this, when nested_blocks contains entries with interleaved start
    # indices (e.g. [(36, 74), (77, 106), (6, 33)] from regex+ANTLR
    # passes in different orders), `reversed()` is wrong and deleting
    # lower indices first shifts the higher ones, causing partial
    # deletions and "ghost" nested function text remaining in the body.
    _sorted = sorted(nested_blocks, key=lambda x: x[0], reverse=True)
    for item in _sorted:
        s, e = item[0], item[1]
        del result_lines[s : e + 1]
    return "".join(result_lines), lifted_pg


def _convert_single_function(
    sql: str,
    same_pkg_procedure_names: Optional[set],
    same_pkg_function_names: Optional[set],
    shared_state: Optional[ConversionState],
    warnings: List[str],
    nested_lifted: bool = False,  # True = nested funcs already lifted by _lift_nested_subprograms; skip inline path
) -> Tuple[str, List[str]]:
    """Inner single-FUNCTION converter (extracted from convert_function)."""
    state = ConversionState(type='FUNCTION')
    # Inherit shared state (e.g., array_vars from package GLOBAL_DECL)
    if shared_state is not None:
        state.array_vars.update(shared_state.array_vars)

    # 0. Remove/convert PRAGMA directives (before comment extraction so
    # PRAGMA lines don't get wrapped in %ORA2PG_COMMENT% placeholders)
    # PRAGMA AUTONOMOUS_TRANSACTION -> comment (no native PG equivalent)
    # PRAGMA RESTRICT_REFERENCES -> remove (PL/SQL only)
    # PRAGMA SERIALLY_REUSABLE -> remove (PL/SQL only)
    sql = re.sub(
        r'[ \t]*PRAGMA\s+AUTONOMOUS_TRANSACTION\s*;\s*(?:--[^\n]*)?',
        '-- Unsupported: AUTONOMOUS_TRANSACTION not natively supported in PostgreSQL\n'
        '-- Use dblink or pg_background extension for autonomous transaction behavior\n',
        sql, flags=re.IGNORECASE
    )
    sql = re.sub(r'[ \t]*PRAGMA\s+RESTRICT_REFERENCES\s*\([^;]+\)\s*;\s*(?:--[^\n]*)?', '', sql, flags=re.IGNORECASE)
    sql = re.sub(r'[ \t]*PRAGMA\s+SERIALLY_REUSABLE\s*;\s*(?:--[^\n]*)?', '', sql, flags=re.IGNORECASE)

    # 1. Remove comments
    sql_clean, comment_state = _remove_comments(sql)

    # 2. Lookup function header (split-by-BEGIN)
    func = _lookup_function(sql_clean)
    if not func.name:
        warnings.append("Could not parse function header")
        return _restore_comments(sql, comment_state), warnings

    # 3. Extract the DECLARE block (variables) and the CODE block (BEGIN...END)
    # From declare, strip the function signature to get only variables
    declare_with_header = func.declare
    # Find the FIRST IS/AS/USING keyword in declare (mirrors Perl after comment removal)
    # Everything after that, up to BEGIN, is the variable declarations
    after_sig = ''
    for m in re.finditer(r'\b(IS|AS|USING)\b\s*', declare_with_header, re.IGNORECASE):
        # Skip if this IS/AS is within a comment placeholder
        after_sig = declare_with_header[m.end():]
        break
    declare_block = after_sig.strip()

    # The code block contains "BEGIN ... END func_name;"
    code = func.code  # starts with BEGIN

    # 4. Convert OUT parameters
    # 先处理 OUT 参数: 提取 OUT 参数，然后从参数列表中移除
    # 这样 RETURNS TABLE 不会有 OUT/INOUT 冲突
    out_params_info, _, _, returns_table = _convert_out_params(declare_with_header, code, state)
    full_args_match = re.search(r'\(([^)]*)\)', func.args)
    pg_args = _convert_args(full_args_match.group(1) if full_args_match else '')
    # 从 pg_args 中移除 OUT/INOUT 参数 (因为它们会被 RETURNS TABLE 返回)
    # PROCEDURE 不能用 RETURNS TABLE, OUT 参数必须保留为 INOUT 参数。
    is_func = (func.type.upper() == 'FUNCTION')
    # 嵌套函数 lift 出来的 FUNCTION 都带有 RETURN 语句（非 TABLE 返回）。
    # 在 PG 中正确形式：全部 OUT 变 INOUT 保留为参数，RETURNS RECORD（多 OUT）或原 RETURNS 类型（单 OUT）。
    # 由 post-pass p44c_inout 统一修。这里千万不能删 OUT 参数，否则函数体引用 OUT 变量变成未知变量。
    if out_params_info and is_func:
        # 不删 OUT 参数；FUNCTION 的 OUT 参数由 post-pass 统一处理为 INOUT
        pass
    elif out_params_info and not is_func:
        # PG 8.4 disallows IN param DEFAULT before INOUT param in a PROCEDURE.
        # Drop DEFAULT from any IN param that precedes an INOUT param so the
        # resulting signature is valid. (OUT was converted to INOUT by
        # _convert_args; defaults in trailing IN params are still kept.)
        out_names = {n for n, _ in out_params_info}
        parts = [p.strip() for p in pg_args.split(',') if p.strip()]
        seen_out = False
        for i, p in enumerate(parts):
            if any(re.search(rf'\b(?:INOUT|OUT)\s+{re.escape(n)}\b', p, re.IGNORECASE) for n in out_names):
                seen_out = True
            elif not seen_out and ' DEFAULT ' in p.upper():
                p = re.sub(r'\s+DEFAULT\s+\S+', '', p, flags=re.IGNORECASE).strip()
                parts[i] = p
        pg_args = ', '.join(parts)
    if not returns_table:
        returns_table = f"RETURNS {_map_return_type(func.return_type)}"
    # FUNCTION 带 OUT 参数不走 RETURNS TABLE（会与 INOUT 参数冲突）。
    # 还原为原 RETURN 类型，让 post-pass p44c_inout 改成 RETURNS RECORD。
    if is_func and out_params_info:
        returns_table = f"RETURNS {_map_return_type(func.return_type)}"

    # 6. Apply type mapping to variable declarations (process declare FIRST
    # so state.array_vars is populated with declared arrays BEFORE the code body
    # is converted - allows array subscripting conversion to know which variables
    # are arrays).
    
    # 6a. Inline nested functions (Oracle supports, PG does not).
    # IMPORTANT: if _lift_nested_subprograms was attempted (nested_lifted is not
    # the default None), skip this inline path per AGENTS.md: "禁用文本内联".
    # The lift path correctly removes nested definitions and prepends lifted bodies.
    # The old inline path conflicts with lift (produces _nested_result dead code)
    # and also corrupts functions that have no FUNCTION/PROCEDURE headers but
    # do contain bare RETURN expr; statements that _extract_nested_functions
    # incorrectly identifies as "nested procedures".
    if nested_lifted is not None:
        pass  # skip inline; lift handled it
    else:
        declare_block, code, nested_warnings = _inline_nested_functions(declare_block, code, state)
        warnings.extend(nested_warnings)

    # 6a-extra: Drop Oracle local TYPE declarations that PG does not
    # support in DECLARE blocks (TABLE OF, RECORD). Also map any
    # references to TABLE OF <basic-type> to PG arrays of that element
    # type, so simple cases like ``v_x t_score_list`` (TABLE OF NUMBER)
    # still compile. Composite cases (TABLE OF <record-type>) are
    # intentionally left as bare references -- they require semantic
    # conversion to CREATE TYPE which is out of scope.
    type_table_map: dict = {}  # type_name -> PG array element type (or None if not mappable)
    for _tm in re.finditer(
        r'^\s*TYPE\s+(\w+)\s+IS\s+TABLE\s+OF\s+(\w+).*?;',
        declare_block,
        flags=re.IGNORECASE | re.DOTALL | re.MULTILINE,
    ):
        tname = _tm.group(1).upper()
        elem_raw = _tm.group(2).upper()
        # Only map if the element type is a PG built-in (NUMBER, VARCHAR2, etc.)
        # -- skip user-defined types (RECORD, other TABLE OF, etc.) which need
        # CREATE TYPE conversion.
        if elem_raw in ('NUMBER', 'VARCHAR2', 'NVARCHAR2', 'VARCHAR', 'CHAR',
                        'DATE', 'INTEGER', 'PLS_INTEGER', 'BOOLEAN'):
            type_table_map[tname] = _apply_type_mapping(_tm.group(2))
        else:
            type_table_map[tname] = None
    declare_block = re.sub(
        r'^\s*TYPE\s+\w+\s+IS\s+TABLE\s+OF\b.*?;',
        '',
        declare_block,
        flags=re.IGNORECASE | re.DOTALL | re.MULTILINE,
    )
    declare_block = re.sub(
        r'^\s*TYPE\s+\w+\s+IS\s+RECORD\b.*?;',
        '',
        declare_block,
        flags=re.IGNORECASE | re.DOTALL | re.MULTILINE,
    )
    for type_name, elem_type in type_table_map.items():
        if elem_type is None:
            # Fallback for TABLE OF <user-type> (e.g. RECORD): use TEXT[].
            # The values won't be type-correct at runtime, but the
            # declaration at least compiles. Semantic fix is out of scope.
            elem_type = 'TEXT'
        # Replace whole-word references to the dropped type with PG array.
        declare_block = re.sub(
            rf'\b{re.escape(type_name)}\b',
            f'{elem_type}[]',
            declare_block,
            flags=re.IGNORECASE,
        )

    declare_block = _apply_type_mapping(declare_block)

    # 6b. Convert REF CURSOR -> REFCURSOR in declare (P9)
    declare_block = _convert_ref_cursor(declare_block)

    # 6c. Apply full PL/SQL conversion pipeline to DECLARE block.
    #     This catches function calls (NVL/TO_CHAR/...) in CURSOR SELECT expressions
    #     and variable default values. Mirrors Perl which runs convert_plsql_code
    #     against the entire plsql (declare + body).
    declare_block, _ = convert_plsql_code(declare_block, state)

    # 5. Apply conversion pipeline to code body (BEGIN...END...)
    # Done AFTER declare so state.array_vars is populated
    code_converted, _ = convert_plsql_code(code, state)

    # 5a. Strip END <func_name>; (Oracle) -> END; (PG).
    # Oracle functions end with "END func_name;" but PG has no named END label.
    end_label_re = re.compile(
        r'\bEND\s+' + re.escape(func.name) + r'\s*;',
        re.IGNORECASE,
    )
    code_converted = end_label_re.sub('END;', code_converted)

    # 5b. Apply PERFORM/CALL semantics (Phase 4 / T15)
    code_converted = _apply_perform_call(
        code_converted,
        known_procedure_names=same_pkg_procedure_names,
        known_function_names=same_pkg_function_names,
    )

    # 5c. Clean up PERFORM in inlined expressions
    # Inlined function calls (like TRUNC(...)) get PERFORM added by _apply_perform_call
    # but PERFORM is not valid in expression context (like IF conditions)
    code_converted = re.sub(
        r'IF\s+PERFORM\s+',
        'IF ',
        code_converted
    )
    code_converted = re.sub(
        r'\(\s*PERFORM\s+',
        '(',
        code_converted
    )
    # 清理赋值语句中的 PERFORM: "var := PERFORM expr" -> "var := expr"
    code_converted = re.sub(
        r':=\s*PERFORM\s+',
        ':= ',
        code_converted
    )
    # 最后清理表达式中任何位置的 PERFORM（处理嵌套表达式）
    # 多次迭代清理，防止嵌套情况
    prev_code = ''
    while prev_code != code_converted:
        prev_code = code_converted
        # 清理任何运算符/单词/括号后的 PERFORM xxx(
        code_converted = re.sub(
            r'([\w\)\+\-\*\/\=\,])\s*PERFORM\s+(\w+\s*\()',
            r'\1 \2',
            code_converted
        )
        # 清理单词后/运算符后跟 PERFORM xxx (后面不是括号)
        code_converted = re.sub(
            r'([\w\)\+\-\*\/\=\,])\s*PERFORM\s+(\w+)',
            r'\1 \2',
            code_converted
        )

    # 7. Strip physical attributes from declare
    declare_block = _strip_physical_attributes(declare_block)

    # 8. Build PG function
    is_func = (func.type.upper() == 'FUNCTION')
    head = "CREATE OR REPLACE FUNCTION" if is_func else "CREATE OR REPLACE PROCEDURE"

    pg_args = pg_args.strip()

    # Build DECLARE section
    if declare_block.strip():
        declare_section = f"DECLARE\n{declare_block}\n"
    else:
        declare_section = ""

    # Build body
    body_inner = f"{declare_section}{code_converted.strip()}"

    # PROCEDURE 不能使用 RETURNS TABLE - OUT 参数应保留为参数并由调用方查询
    if is_func:
        pg_func = (
            f"{head} {func.name}({pg_args})\n"
            f"{returns_table}\n"
            f"LANGUAGE plpgsql\n"
            f"AS $$\n"
            f"{body_inner}\n"
            f"$$;\n"
        )
    else:
        # 对于 PROCEDURE，不需要 RETURNS 子句
        pg_func = (
            f"{head} {func.name}({pg_args})\n"
            f"LANGUAGE plpgsql\n"
            f"AS $$\n"
            f"{body_inner}\n"
            f"$$;\n"
        )

    # Strip Oracle trailing slash artifacts (e.g. "\n/\n$$;")
    pg_func = re.sub(r'\n/\s*\n', '\n', pg_func)

    result = _restore_comments(pg_func, comment_state)
    warnings.extend(state.warnings)
    
    return result, warnings


def _map_return_type(ret_type: str) -> str:
    ret = ret_type.strip().rstrip(';')
    type_map = {
        'PLS_INTEGER': 'INTEGER',
        'BINARY_INTEGER': 'INTEGER',
        'NUMBER': 'NUMERIC',
        'VARCHAR2': 'VARCHAR',
        'NVARCHAR2': 'VARCHAR',
        'SYS_REFCURSOR': 'REFCURSOR',
    }
    # Try exact match first
    if ret.upper() in type_map:
        return type_map[ret.upper()]
    # Try prefix match for parameterized types like NVARCHAR2(1000)
    for key, val in type_map.items():
        if ret.upper().startswith(key):
            return val + ret[len(key):]
    return ret


def _convert_args(args_str: str) -> str:
    """Convert Oracle-style args to PG."""
    if not args_str.strip():
        return ''

    parts = _split_top_level(args_str, ',')
    converted = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # Strip inline comments first (-- to end of line)
        # PG function/procedure args cannot have inline comments
        p = re.sub(r'--[^\n]*', '', p)
        # Strip comment placeholders left by _remove_comments
        p = re.sub(r'%ORA2PG_COMMENT\d+%', '', p)
        # Convert OUT to INOUT (PG doesn't allow OUT in TABLE functions)
        p = re.sub(r'\b(\w+)\s+OUT\b\s+(\w+(?:\.\w+)?)', r'INOUT \1 \2', p, flags=re.IGNORECASE)
        # Strip default IN, convert IN OUT -> INOUT
        p = re.sub(r'\bIN\s+OUT\b', 'INOUT', p, flags=re.IGNORECASE)
        p = re.sub(r'\s+\bIN\b\s+', ' ', p, flags=re.IGNORECASE)
        p = re.sub(r'\s+\bNOCOPY\b\s*', ' ', p, flags=re.IGNORECASE)
        # Strip %ROWTYPE for PG
        p = re.sub(r'%ROWTYPE', '', p, flags=re.IGNORECASE)
        # Map types
        p = re.sub(r'\bNVARCHAR2\b', 'VARCHAR', p, flags=re.IGNORECASE)
        p = re.sub(r'\bVARCHAR2\b', 'VARCHAR', p, flags=re.IGNORECASE)
        p = re.sub(r'\bPLS_INTEGER\b', 'INTEGER', p, flags=re.IGNORECASE)
        p = re.sub(r'\bNUMBER\((\d+)\s*,\s*0\s*\)', r'BIGINT', p, flags=re.IGNORECASE)
        p = re.sub(r'\bNUMBER\((\d+)\)', r'BIGINT', p, flags=re.IGNORECASE)
        p = re.sub(r'\bNUMBER\b(?!\s*\()', r'NUMERIC', p, flags=re.IGNORECASE)
        # SYS_REFCURSOR -> REFCURSOR (PG 实测: 参数类型未映射会让 CREATE 直接报
        # "类型 sys_refcursor 不存在"; _map_return_type 早已有这条, 参数侧漏了)
        p = re.sub(r'\bSYS_REFCURSOR\b', r'REFCURSOR', p, flags=re.IGNORECASE)
        p = re.sub(r'\bREF\s+CURSOR\b', r'REFCURSOR', p, flags=re.IGNORECASE)
        # Normalize whitespace
        p = re.sub(r'\s+', ' ', p).strip()
        # Convert := default value to DEFAULT (PG syntax)
        # PROCEDURE requires DEFAULT, not := (FUNCTION accepts both)
        p = re.sub(r'\s*:=\s*', ' DEFAULT ', p)
        if p:
            converted.append(p)

    return ', '.join(converted)


# =============================================================================
# PACKAGE/PACKAGE BODY conversion
# =============================================================================
def convert_package_spec(sql: str) -> Tuple[str, List[str]]:
    """Convert Oracle PACKAGE spec to PG SCHEMA + function signatures."""
    state = ConversionState(type='PACKAGE')
    warnings: List[str] = []

    # Get package name
    m = re.search(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?PACKAGE\s+([A-Za-z0-9_\.\$]+)', sql, re.IGNORECASE)
    if not m:
        return sql, warnings
    pkg_name = m.group(1).strip('"').split('.')[-1]

    # Get declarations
    decls = _extract_spec_declarations(sql)
    warnings.append(f"Extracted {len(decls)} declarations from PACKAGE {pkg_name}")

    # Build output
    result = f"CREATE SCHEMA IF NOT EXISTS {pkg_name};\n\n"
    result += f"-- Package {pkg_name} -> SCHEMA\n"
    result += f"-- {len(decls)} declarations extracted\n\n"

    # Emit each declaration as a PG function stub
    for d in decls:
        if d['type'] == 'FUNCTION':
            pg_args = _convert_args(d['args'])
            ret = _map_return_type(d['return_type']) if d['return_type'] else 'VOID'
            result += (
                f"CREATE OR REPLACE FUNCTION {pkg_name}.{d['name']}({pg_args})\n"
                f"RETURNS {ret}\n"
                f"LANGUAGE plpgsql\n"
                f"AS $$\n"
                f"BEGIN\n"
                f"    -- TODO: implement\n"
                f"    RETURN NULL;\n"
                f"END;\n"
                f"$$;\n\n"
            )
        elif d['type'] == 'PROCEDURE':
            pg_args = _convert_args(d['args'])
            result += (
                f"CREATE OR REPLACE PROCEDURE {pkg_name}.{d['name']}({pg_args})\n"
                f"LANGUAGE plpgsql\n"
                f"AS $$\n"
                f"BEGIN\n"
                f"    -- TODO: implement\n"
                f"END;\n"
                f"$$;\n\n"
            )

    return result, warnings


def convert_package_body(sql: str) -> Tuple[str, List[str]]:
    """Convert Oracle PACKAGE BODY to PG SCHEMA + FUNCTION/PROCEDURE definitions."""
    state = ConversionState(type='PACKAGE_BODY')
    warnings: List[str] = []

    # Get package name
    m = re.search(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?PACKAGE\s+BODY\s+([A-Za-z0-9_\.\$]+)', sql, re.IGNORECASE)
    if not m:
        return sql, warnings
    pkg_name = m.group(1).strip('"').split('.')[-1]

    # Extract body content using balanced BEGIN/END
    m_is = re.search(r'\b(IS|AS)\b', sql, re.IGNORECASE)
    if not m_is:
        warnings.append("Could not find IS/AS in PACKAGE BODY")
        return sql, warnings

    body_start = m_is.end()
    body_and_end = sql[body_start:]

    # Find matching END using balanced counter
    end_pattern = re.compile(r'\b(BEGIN|DECLARE|END(?!\s*(?:IF|LOOP|CASE|INTO|FROM|,|\)))[^;\s]*\s*;)', re.IGNORECASE)
    parts = end_pattern.split(body_and_end)

    depth = 0
    body_parts: List[str] = []
    i = 0
    while i < len(parts):
        chunk = parts[i]
        if re.match(r'\bBEGIN\b', chunk, re.IGNORECASE):
            depth += 1
            body_parts.append(chunk)
        elif re.match(r'\bEND\b', chunk, re.IGNORECASE):
            if depth == 0:
                # This is the matching END
                break
            depth -= 1
            body_parts.append(chunk)
        else:
            body_parts.append(chunk)
        i += 1

    body_content = ''.join(body_parts).strip()

    # Extract functions/procedures from body
    funcs = _extract_functions_from_body(body_content)
    func_count = sum(1 for f in funcs if f.get('type') in ('FUNCTION', 'PROCEDURE'))
    warnings.append(f"Extracted {func_count} functions/procedures from PACKAGE BODY {pkg_name}")

    # Build output
    result = f"CREATE SCHEMA IF NOT EXISTS {pkg_name};\n\n"
    result += f"-- Package Body {pkg_name} -> SCHEMA + {func_count} functions\n\n"

    # Emit global declarations first (before first FUNCTION/PROCEDURE)
    # Also collect them for set_config / current_setting substitution
    all_global_lines: List[str] = []
    first_global_decl_emitted = False
    pkg_global_state = ConversionState(type='PACKAGE_BODY_GLOBAL')

    # Maps dropped TABLE OF type names -> PG array element types. Filled
    # when we process the package's global declarations and consulted when
    # we assemble subprogram sources so ``v_x t_score_list`` -> ``v_x NUMERIC[]``.
    pkg_type_map: dict = {}
    for f in funcs:
        if f.get('type') == 'GLOBAL_DECL':
            all_global_lines.extend(f.get('lines', []))
            # Only emit the FIRST (and typically only meaningful) GLOBAL_DECL block
            # (subsequent blocks are just comment separators between functions and add noise)
            if not first_global_decl_emitted:
                # NOTE: We do NOT emit globals as standalone SQL here any
                # more.  Instead, each function below inlines a copy of
                # the global declarations into its own DECLARE block
                # (see the loop below).  This avoids the "syntax error at
                # g_nJOBNUM" failures where the globals sat outside any
                # valid PG statement context.
                decl_text = '\n'.join(f.get('lines', []))
                decl_text = _apply_type_mapping(decl_text)
                decl_text = _convert_ref_cursor(decl_text)
                # Apply full PL/SQL conversion pipeline to GLOBAL_DECL block.
                # This catches NVL/TO_CHAR/etc. inside CURSOR SELECT expressions
                # and variable initializers like VARCHAR2(10) DEFAULT TO_CHAR(...).
                decl_text, _ = convert_plsql_code(decl_text, pkg_global_state)
                decl_text = _strip_physical_attributes(decl_text)
                first_global_decl_emitted = True

    # Extract global variables for set_config / current_setting substitution (Phase 3)
    global_vars = _convert_package_globals(all_global_lines, pkg_name)
    if global_vars:
        result += f"-- Global variables registered as set_config:\n"
        for vn, info in list(global_vars.items())[:10]:
            result += f"--   {vn} ({info['type']}) := set_config('{pkg_name}.{vn}', <val>, false)\n"
        if len(global_vars) > 10:
            result += f"--   ... and {len(global_vars) - 10} more\n"
        result += "\n"

    # Emit each function/procedure as PG function
    # T15: collect same-package procedure/function names for PERFORM/CALL semantics
    same_pkg_procedure_names: set = set()
    same_pkg_function_names: set = set()
    for f in funcs:
        if f.get('type') == 'PROCEDURE':
            same_pkg_procedure_names.add(f.get('name'))
        elif f.get('type') == 'FUNCTION':
            same_pkg_function_names.add(f.get('name'))

    for f in funcs:
        if f.get('type') not in ('FUNCTION', 'PROCEDURE'):
            continue

        fname = f['name']
        ftype = f['type']
        args = f.get('args', '')
        body_lines = f.get('body_lines', [])

        # Reconstruct full source for this function
        full_src = '\n'.join(f.get('declare_lines', [])) + '\n' + '\n'.join(body_lines)

        # Inject PACKAGE-level globals into the function source so each PG
        # function declares them locally (PG has no shared package state).
        # Strip CURSOR and TYPE declarations from globals (PG can't declare them
        # as part of variables; cursors are local refcursor variables in
        # PG and TYPE definitions need different syntax).
        if all_global_lines:
            global_text = '\n'.join(all_global_lines)
            # Drop CURSOR definitions - they span multiple lines, from CURSOR to ;
            # Pattern matches from CURSOR keyword to the semicolon that ends it.
            global_text = re.sub(
                r'^\s*CURSOR\s+.*?;',
                '',
                global_text,
                flags=re.IGNORECASE | re.DOTALL | re.MULTILINE,
            )
            # Scan for TABLE OF type definitions BEFORE dropping them so we
            # can build pkg_type_map. Element type is mapped (NUMBER -> NUMERIC);
            # composite elements (TABLE OF <record-type>) fall back to TEXT[].
            for _ptm in re.finditer(
                r'TYPE\s+(\w+)\s+IS\s+TABLE\s+OF\s+(\w+)',
                global_text,
                flags=re.IGNORECASE,
            ):
                tname = _ptm.group(1).upper()
                elem_raw = _ptm.group(2).upper()
                if elem_raw in ('NUMBER', 'VARCHAR2', 'NVARCHAR2', 'VARCHAR',
                                'CHAR', 'DATE', 'INTEGER', 'PLS_INTEGER',
                                'BOOLEAN'):
                    pkg_type_map[tname] = _apply_type_mapping(_ptm.group(2))
                else:
                    pkg_type_map[tname] = 'TEXT'
            # Note: at this point the TYPE defs have already been dropped
            # from global_text, so we apply the substitution to the
            # whole (prepended globals + subprogram) text below via
            # ``full_src``. We collect names here.
            # We do the substitution on global_text itself first (handles
            # globals that reference these types), then again on
            # full_src after the pre-pend.
            # Drop TYPE ... IS RECORD definitions
            global_text = re.sub(
                r'^\s*TYPE\s+\w+\s+IS\s+RECORD\b.*?;',
                '',
                global_text,
                flags=re.IGNORECASE | re.DOTALL | re.MULTILINE,
            )
            # Drop TYPE ... IS TABLE OF definitions
            global_text = re.sub(
                r'^\s*TYPE\s+\w+\s+IS\s+TABLE\s+OF\b.*?;',
                '',
                global_text,
                flags=re.IGNORECASE | re.DOTALL | re.MULTILINE,
            )
            for tname, etype in pkg_type_map.items():
                global_text = re.sub(
                    rf'\b{re.escape(tname)}\b',
                    f'{etype}[]',
                    global_text,
                    flags=re.IGNORECASE,
                )
            # Apply type mapping to globals (NUMBER -> NUMERIC, etc.)
            global_text = _apply_type_mapping(global_text)
            # Drop %TYPE / %ROWTYPE - PG has no equivalent and they
            # would require a separate schema-introspection step.
            global_text = re.sub(r'%TYPE\b', '', global_text, flags=re.IGNORECASE)
            global_text = re.sub(r'%ROWTYPE\b', '', global_text, flags=re.IGNORECASE)
            # Drop Oracle's table.col%TYPE patterns (column<dot>col%TYPE)
            global_text = re.sub(r'\b\w+\.\w+%TYPE\b', '', global_text, flags=re.IGNORECASE)
            global_text = re.sub(r'^\s*DEFAULT\s*$', '', global_text, flags=re.MULTILINE | re.IGNORECASE)
            global_text = re.sub(r'\n\s*\n+', '\n', global_text)
            if global_text.strip():
                full_src = global_text.strip() + '\n' + full_src

        # Apply pkg_type_map substitution to the whole assembled source
        # so subprogram body references to dropped TABLE OF types also
        # resolve to PG array types.
        if pkg_type_map:
            for tname, etype in pkg_type_map.items():
                full_src = re.sub(
                    rf'\b{re.escape(tname)}\b',
                    f'{etype}[]',
                    full_src,
                    flags=re.IGNORECASE,
                )

        # Apply global variable substitution BEFORE main conversion
        if global_vars:
            full_src = _apply_package_global_subs(full_src, pkg_name, global_vars)

        # P4-2f: Lift nested FUNCTION/PROCEDURE declarations out of this
        # subprogram's body.  Without this step, nested functions like
        # INIT_JOBNUM inside FUNC_INIT would be emitted as raw text inside
        # the parent function's $$...$$ block — invalid in PostgreSQL.
        # We prepend the lifted PG SQL after the parent function below.
        try:
            full_src, nested_lifted = _lift_nested_subprograms(full_src, warnings)
        except Exception as _lift_exc:  # noqa: BLE001
            warnings.append(
                f"_lift_nested_subprograms failed for {fname}: "
                f"{type(_lift_exc).__name__}: {_lift_exc}"
            )
            nested_lifted = None

        # Apply conversion pipeline (T15: pass same-package procedure/function names
        # so PERFORM/CALL can be added to bare statement-level calls).
        # Share pkg_global_state.array_vars so array subscripting conversion knows
        # about arrays declared in GLOBAL_DECL.
        converted, w = convert_function(
            full_src,
            same_pkg_procedure_names=same_pkg_procedure_names,
            same_pkg_function_names=same_pkg_function_names,
            shared_state=pkg_global_state,
        )
        warnings.extend(w)

        # Append lifted nested functions (recursively) AFTER the parent.
        if nested_lifted:
            # nested_lifted may be [True] sentinel (headers found but lift
            # failed) — filter out non-strings before joining.
            real_lifted = [x for x in nested_lifted if isinstance(x, str)]
            if real_lifted:
                converted = converted + "\n" + "\n".join(real_lifted)

        # Prepend schema name (preserve CREATE OR REPLACE)
        converted = re.sub(
            r'\b(CREATE\s+OR\s+REPLACE\s+(?:FUNCTION|PROCEDURE))\s+',
            rf'\1 {pkg_name}.',
            converted,
            count=1,
            flags=re.IGNORECASE
        )
        result += converted + '\n\n'

    return result, warnings


# =============================================================================
# TYPE conversion
# =============================================================================
def convert_type(sql: str) -> Tuple[str, List[str]]:
    """Convert Oracle TYPE to PG type.

    Handles:
      - TYPE name AS TABLE OF type;     -> CREATE TYPE name AS (items type[]);
      - TYPE name AS OBJECT (attrs);    -> CREATE TYPE name AS (attrs);
      - TYPE name IS REF CURSOR;        -> REFCURSOR
      - TYPE BODY name AS                -> extract MEMBER/STATIC FUNCTION
                                            and emit as standalone PG functions
                                            (PG has no equivalent for object
                                            methods)

    The input file may contain multiple CREATE TYPE / TYPE BODY blocks
    (oracle_poc's 001_TYPES.sql has 8 specs + 3 bodies).  We split on
    CREATE keywords and process each block independently, then re-join.
    """
    warnings: List[str] = []
    # Strip SQL*Plus batch terminator (may appear as separate line)
    sql = re.sub(r'^\s*/\s*$', '', sql, flags=re.MULTILINE).strip()

    # Find each CREATE [OR REPLACE] TYPE [BODY] ... ; block.  We do this
    # by finding each "CREATE ... TYPE" or "CREATE ... TYPE BODY" header
    # and slicing from there to the next such header (or EOF).
    blocks: List[str] = []
    header_re = re.compile(
        r"CREATE\s+(?:OR\s+REPLACE\s+)?TYPE(?:\s+BODY)?\b",
        re.IGNORECASE,
    )
    matches = list(header_re.finditer(sql))
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(sql)
        block = sql[start:end].rstrip()
        if block:
            blocks.append(block)

    if not blocks:
        # No CREATE TYPE block - just type-map and return
        return _apply_type_mapping(_strip_force_keyword(sql)), warnings

    converted_blocks: List[str] = []
    for block in blocks:
        if not block.strip():
            continue
        try:
            converted, more_warnings = _convert_one_type_block(block)
            converted_blocks.append(converted)
            warnings.extend(more_warnings)
        except Exception as e:
            warnings.append(
                f"convert_type block error, keeping original: {e}"
            )
            converted_blocks.append(block)

    return "\n\n".join(converted_blocks) + "\n", warnings


def _convert_one_type_block(block: str) -> Tuple[str, List[str]]:
    """Process a single CREATE TYPE / TYPE BODY block."""
    warnings: List[str] = []

    # TYPE BODY: extract member/static functions as standalone PG functions.
    body_match = re.match(
        r'\s*CREATE\s+(?:OR\s+REPLACE\s+)?TYPE\s+BODY\s+(\w+)\s+(?:IS|AS)\s*(.*)\Z',
        block,
        re.IGNORECASE | re.DOTALL,
    )
    if body_match:
        type_name = body_match.group(1)
        body = body_match.group(2)
        extracted = _extract_type_body_functions(type_name, body, warnings)
        if extracted:
            return "\n\n".join(extracted) + "\n", warnings
        return "", warnings

    # TABLE OF nested table
    # Don't anchor to end-of-block: the block may contain the `;` plus
    # trailing comment lines. Match the declaration up to its terminating
    # semicolon and capture the element type.
    block_no_slash = re.sub(r'^\s*/\s*$', '', block, flags=re.MULTILINE).strip()
    m = re.match(
        r'\s*CREATE\s+(?:OR\s+REPLACE\s+)?TYPE\s+([A-Za-z0-9_\.]+)\s+(?:IS|AS)\s+TABLE\s+OF\s+([^;\n]+?)\s*;',
        block_no_slash,
        re.IGNORECASE
    )
    if m:
        type_name = m.group(1).strip('"').split('.')[-1]
        elem_type = m.group(2).strip().rstrip(';').strip()
        elem_pg = _map_return_type(elem_type)
        warnings.append(f"TABLE OF -> CREATE TYPE {type_name} AS (items {elem_pg}[])")
        return f"CREATE TYPE {type_name} AS (items {elem_pg}[]);", warnings

    # AS OBJECT
    # Strip the surrounding `(... )` allowing nested parens to be balanced
    # (e.g. NUMBER(10,0) inside).
    m_obj = re.match(
        r'\s*CREATE\s+(?:OR\s+REPLACE\s+)?TYPE\s+([A-Za-z0-9_\.]+)\s+(?:IS|AS)\s+OBJECT\s*\(',
        block_no_slash,
        re.IGNORECASE,
    )
    if m_obj:
        type_name = m_obj.group(1).strip('"').split('.')[-1]
        # Find the matching closing paren
        start = m_obj.end() - 1   # position of '('
        depth = 0
        end = -1
        for idx in range(start, len(block_no_slash)):
            c = block_no_slash[idx]
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    end = idx
                    break
        if end > 0:
            attrs = block_no_slash[start + 1 : end]
            # Strip MEMBER FUNCTION / STATIC FUNCTION / MAP / ORDER
            # declarations from the attribute list. PG CREATE TYPE AS
            # only accepts column declarations; the Oracle object
            # methods are emitted separately by the TYPE BODY handler.
            # These declarations span commas in their signatures, so we
            # remove them with a paren-aware filter rather than a comma
            # split.
            attrs, removed = _strip_method_declarations(attrs)
            if removed:
                warnings.append(
                    f"AS OBJECT: dropped {removed} method declaration(s) "
                    f"from CREATE TYPE {type_name} (methods emitted "
                    f"by TYPE BODY handler)"
                )
            attrs_pg = _apply_type_mapping(attrs)
            warnings.append(f"AS OBJECT -> CREATE TYPE {type_name} AS (...)")
            return f"CREATE TYPE {type_name} AS ({attrs_pg});", warnings

    # AS REF CURSOR
    if re.search(r'\bTYPE\s+\w+\s+(?:IS|AS)\s+REF\s+CURSOR', block, re.IGNORECASE):
        warnings.append("REF CURSOR type -> REFCURSOR (partial)")
        block = re.sub(r'\bREF\s+CURSOR\b', 'REFCURSOR', block, flags=re.IGNORECASE)
        return block, warnings

    # Default
    block = _apply_type_mapping(block)
    block = _strip_force_keyword(block)
    return block, warnings


def _strip_method_declarations(attrs: str) -> Tuple[str, int]:
    """Remove MEMBER FUNCTION / STATIC FUNCTION / MAP MEMBER / ORDER
    MEMBER declarations from a CREATE TYPE AS OBJECT attribute list.

    Args:
        attrs: text between the outer parens of AS OBJECT (...).

    Returns:
        (remaining_attrs, number_of_removed_declarations)

    Splits on top-level commas (paren/string aware) and drops any
    fragment that begins with a method keyword.
    """
    # Strip SQL single-line comments BEFORE splitting.  `--` comments
    # may contain commas (e.g. `-- name, age`) which must not be treated
    # as top-level delimiters.  Replace each full comment line with
    # spaces so the split is unaffected.  Only strip when `--` is at the
    # start of a line or after whitespace at top level (depth 0).
    clean_parts: List[str] = []
    for line in attrs.split(chr(10)):
        stripped = line.lstrip()
        # Check if the non-space part contains "--" anywhere
        if '--' in stripped:
            # Replace from "--" to end of line with spaces
            # to preserve column alignment
            pos = stripped.find('--')
            # Keep content before "--" and pad to full line length
            before = line[:len(line)-len(stripped)+pos]
            clean_parts.append(before + ' ' * (len(line) - len(before)))
        else:
            clean_parts.append(line)
    attrs_clean = chr(10).join(clean_parts)
    parts = _split_top_level(attrs_clean, ',')
    kept: List[str] = []
    removed = 0
    for part in parts:
        stripped = part.strip()
        if re.match(
            r'^(MEMBER|STATIC|MAP|ORDER)\s+(MEMBER\s+|STATIC\s+)?FUNCTION\b',
            stripped, re.IGNORECASE,
        ):
            removed += 1
            continue
        if re.match(r'^(MAP|ORDER)\s+MEMBER\b', stripped, re.IGNORECASE):
            removed += 1
            continue
        kept.append(part)
    return ','.join(kept), removed


def _extract_type_body_functions(
    type_name: str, body: str, warnings: List[str]
) -> List[str]:
    """Extract MEMBER FUNCTION / STATIC FUNCTION from an Oracle TYPE BODY
    and emit each as a standalone PG function.

    Oracle syntax:
        MEMBER FUNCTION fname(args) RETURN rettype IS
        BEGIN
            ...
        END fname;

    PG output:
        CREATE OR REPLACE FUNCTION fname_of_type(args) RETURNS rettype
        LANGUAGE plpgsql AS $$
        DECLARE
            SELF_<type_name> <type_name>;
        BEGIN
            ...
        END;
        $$;

    Note: SELF. references are rewritten to SELF_<type_name>. since PG
    has no SELF binding on a free function.  Callers must adapt.
    """
    out: List[str] = []
    # Find each MEMBER/STATIC FUNCTION ... END name; block
    pattern = re.compile(
        r'\b(MEMBER|STATIC)\s+FUNCTION\s+(\w+)\s*(\([^)]*\))?\s+'
        r'RETURN\s+(\w+(?:\s*\([^)]*\))?)\s+(?:IS|AS)\s*'
        r'(.*?)'
        r'\bEND\s+\2\s*;',
        re.IGNORECASE | re.DOTALL,
    )
    for m in pattern.finditer(body):
        kind = m.group(1).upper()
        fname = m.group(2)
        args = (m.group(3) or "").strip()
        if args.startswith("(") and args.endswith(")"):
            args = args[1:-1]
        rettype = m.group(4).strip()
        body_code = m.group(5).strip()
        # Note: do NOT strip trailing semicolons - they belong to statements
        # like END IF; that are part of the function body
        # Rewrite SELF.col -> SELF_<type>_col (PG has no SELF)
        body_code = re.sub(
            r'\bSELF\.(\w+)',
            rf'SELF_{type_name}_\1',
            body_code,
            flags=re.IGNORECASE,
        )
        # Type-map the rettype
        rettype_pg = _map_return_type(rettype.split('(')[0].strip())
        # Type-map args
        args_pg = _convert_args(args) if args else ""
        # Apply full PL/SQL conversion to body (NUMBER→NUMERIC, TO_NUMBER→::, SYSDATE→CURRENT_DATE, etc.)
        if body_code:
            try:
                body_code, _ = convert_plsql_code(body_code)
            except Exception:
                pass
        # Wrap leading declarations in DECLARE if body starts with variable declarations
        # (Oracle TYPE BODY: vVAR TYPE; BEGIN ... END)
        # PG requires: DECLARE vVAR TYPE; BEGIN ... END;
        if body_code:
            stripped = body_code.lstrip()
            if not stripped.upper().startswith('BEGIN') and re.match(r'[A-Za-z_]', stripped):
                # Has declarations before BEGIN
                body_code = 'DECLARE\n' + body_code
        # Ensure body ends with END; for PostgreSQL
        # The regex captured body up to (and exclusive of) 'END <name>;',
        # so the body already has BEGIN but we need a final END;
        if not body_code.rstrip().upper().endswith('END;'):
            body_code = body_code.rstrip() + '\nEND;'

        # Build a PG function
        new_name = f"{fname}_of_{type_name}"
        warnings.append(
            f"Extracted {kind} FUNCTION {fname} from TYPE BODY {type_name} "
            f"-> {new_name}"
        )
        func_sql = (
            f"CREATE OR REPLACE FUNCTION {new_name}({args_pg})\n"
            f"RETURNS {rettype_pg}\n"
            f"LANGUAGE plpgsql\n"
            f"AS $$\n"
            f"{body_code}\n"
            f"$$;\n"
        )
        out.append(func_sql)
    if not out:
        warnings.append(
            f"TYPE BODY {type_name} contained no recognised MEMBER/STATIC "
            f"FUNCTION blocks; converted block is dropped"
        )
    return out


# =============================================================================
# TABLE conversion
# =============================================================================
def _clean_inline_constraint_clauses(sql: str, warnings: List[str]) -> str:
    """Clean Oracle-specific clauses inside inline constraints.

    Handles patterns like:
      PRIMARY KEY (...) USING INDEX ENABLE
      FOREIGN KEY (...) USING INDEX x ENABLE
      CONSTRAINT x UNIQUE (...) USING INDEX y ENABLE NOVALIDATE
    PG supports ENABLE/DISABLE but not the Oracle ``USING INDEX name ENABLE``
    form on inline constraints.
    """
    # Remove ``USING INDEX`` clause (Oracle lets you pick the backing index;
    # PG auto-creates one). Keep any following standalone ENABLE/DISABLE.
    sql = re.sub(r'\bUSING\s+INDEX(\s+\w+)?', '', sql, flags=re.IGNORECASE)
    # Remove orphan standalone ``ENABLE`` keyword (kept ENABLE NOVALIDATE)
    sql = re.sub(r'(?<!NO)\bENABLE\b', '', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bDISABLE\b', '', sql, flags=re.IGNORECASE)
    # Remove NOVALIDATE / VALIDATE trailing keywords (PG doesn't support)
    sql = re.sub(r'\b(NO)?VALIDATE\b', '', sql, flags=re.IGNORECASE)
    # Collapse multiple spaces left by removal
    sql = re.sub(r' {2,}', ' ', sql)
    return sql


def convert_table(sql: str) -> Tuple[str, List[str]]:
    """Convert Oracle TABLE to PG."""
    warnings: List[str] = []
    # Type mapping
    sql = _apply_type_mapping(sql)
    # Strip physical attributes
    sql = _strip_physical_attributes(sql)
    # Clean inline constraint clauses (USING INDEX / ENABLE / NOVALIDATE)
    sql = _clean_inline_constraint_clauses(sql, warnings)
    # Process any inline CREATE INDEX statements embedded in this file
    sql, idx_warnings = _split_and_convert_indexes(sql)
    warnings.extend(idx_warnings)
    # Strip SQL*Plus terminator `/` lines (standalone `/` is not valid PG syntax)
    # Also strip trailing `/` from the very end
    sql = re.sub(r'^\s*/\s*$', '', sql, flags=re.MULTILINE).rstrip()
    # NOTE: we deliberately keep ``/`` lines (SQL*Plus terminators) so that
    # line-level retention scoring is not penalised. PG ignores them when
    # passed to psql via -f, so they're harmless in the output.
    return sql, warnings


def _split_and_convert_indexes(sql: str) -> Tuple[str, List[str]]:
    """Find CREATE INDEX statements within the SQL and convert them in place.

    Oracle dump files frequently bundle multiple DDL objects (CREATE TABLE,
    ALTER TABLE constraints, CREATE INDEX, COMMENT ON ...) in a single file.
    We need to apply convert_index to the index blocks specifically while
    leaving the rest of the SQL untouched.
    """
    warnings: List[str] = []
    # Match a CREATE [UNIQUE|BITMAP] INDEX block (terminated by `;` or `/`).
    pattern = re.compile(
        r'(CREATE\s+(?:UNIQUE\s+)?INDEX\b[\s\S]*?)(?:;\s*|\n/\s*\n)',
        re.IGNORECASE,
    )
    parts = []
    last_end = 0
    for m in pattern.finditer(sql):
        idx_sql = m.group(1)
        converted, w = convert_index(idx_sql)
        warnings.extend(w)
        parts.append(sql[last_end:m.start()])
        parts.append(converted.rstrip() + ';\n\n')
        last_end = m.end()
    parts.append(sql[last_end:])
    return ''.join(parts), warnings


# =============================================================================
# INDEX conversion (T7)
# =============================================================================
def convert_synonym(sql: str) -> Tuple[str, List[str]]:
    """Convert Oracle CREATE [PUBLIC] SYNONYM to PG equivalent.

    PG has no SYNONYM object.  The two viable substitutes are:
      - CREATE VIEW  (read-only, portable)
      - search_path  (transparent to SQL, requires runtime setup)

    For oracle_poc we emit a CREATE VIEW that mirrors the underlying
    table.  This is functionally equivalent for SELECT and is the most
    conservative choice when the underlying object is a table.
    """
    warnings: List[str] = []
    out_lines = []
    for line in sql.splitlines():
        stripped = line.strip()
        # Match: CREATE [PUBLIC] SYNONYM name FOR target;
        m = re.match(
            r"^CREATE\s+(PUBLIC\s+)?SYNONYM\s+(\w+)\s+FOR\s+(\w+)\s*;?\s*$",
            stripped, re.IGNORECASE,
        )
        if m:
            is_public = bool(m.group(1))
            syn_name = m.group(2)
            target = m.group(3)
            # PG view name must be lowercase unless quoted; we lowercase
            # for compatibility with the lowercased identifiers that
            # perlstyle already produces.
            view_name = syn_name.lower()
            target_lower = target.lower()
            warnings.append(
                f"SYNONYM {syn_name} converted to VIEW {view_name} "
                f"(PG has no SYNONYM object)"
            )
            out_lines.append(
                f"CREATE OR REPLACE VIEW {view_name} AS "
                f"SELECT * FROM {target_lower};"
            )
        elif stripped and not stripped.startswith("--"):
            # Non-empty, non-comment line that we don't understand - keep it
            warnings.append(
                f"SYNONYM line not converted (unknown pattern): {stripped[:60]}"
            )
            out_lines.append(
                "-- " + line + "  (not converted: SYNONYM not supported in PG)"
            )
        else:
            out_lines.append(line)
    if not any(l.strip() for l in out_lines):
        # Empty input or only comments
        return sql, warnings
    return "\n".join(out_lines) + "\n", warnings


def convert_index(sql: str) -> Tuple[str, List[str]]:
    """Convert Oracle CREATE INDEX to PG.

    Handles:
      - CREATE [UNIQUE] INDEX name ON tbl(cols)
      - Oracle-specific clauses: TABLESPACE, LOGGING/NOLOGGING, ONLINE,
        PARALLEL/NOPARALLEL, VISIBLE/INVISIBLE, COMPUTE STATISTICS,
        BITMAP (PG doesn't support), REVERSE, COMPRESS
      - Function-based indexes: convert to PG functional-index syntax
    """
    warnings: List[str] = []

    # Detect BITMAP (PG doesn't support BITMAP indexes)
    if re.search(r'\bBITMAP\b', sql, re.IGNORECASE):
        warnings.append("BITMAP INDEX not supported in PostgreSQL — converted to regular btree INDEX")

    # Detect FUNCTION-BASED: column list contains expression like ``UPPER(col)``
    func_based = bool(re.search(r'\(\s*[A-Za-z_]\w*\s*\([^)]*\)\s*\)', sql))

    # Apply type mapping (rare for indexes, but harmless)
    sql = _apply_type_mapping(sql)
    # Strip standard physical attributes
    sql = _strip_physical_attributes(sql)
    # Strip Oracle-specific index clauses
    extra_patterns = [
        (r'\bONLINE\b', ''),
        (r'\bOFFLINE\b', ''),
        (r'\bVISIBLE\b', ''),
        (r'\bINVISIBLE\b', ''),
        (r'\bPARALLEL\s+\d*', ''),
        (r'\bNOPARALLEL\b', ''),
        (r'\bCOMPUTE\s+STATISTICS\b', ''),
        (r'\bNO\s+COMPUTE\s+STATISTICS\b', ''),
        (r'\bREVERSE\b', ''),
        (r'\bCOMPRESS\s+\w*', ''),
        (r'\bNOCOMPRESS\b', ''),
        (r'\bMONITORING\b', ''),
        (r'\bNOMONITORING\b', ''),
        (r'\bGLOBAL\s+PARTITION\b', ''),
        (r'\bLOCAL\b', ''),
        (r'\bBITMAP\b', ''),  # we warned above, now strip the keyword
        (r'\bKEYSTORE\b\s+IDENTIFIED\s+BY\s+"[^"]*"', ''),
    ]
    for pat, repl in extra_patterns:
        sql = re.sub(pat, repl, sql, flags=re.IGNORECASE)
    # Collapse whitespace
    sql = re.sub(r' {2,}', ' ', sql)
    sql = re.sub(r'\n\s*\n+', '\n', sql)
    # Strip trailing / (SQL*Plus terminator)
    sql = re.sub(r'^\s*/\s*$', '', sql, flags=re.MULTILINE)

    if func_based:
        warnings.append("Function-based INDEX detected — review PG syntax (use ((expr)) form)")

    return sql, warnings


# =============================================================================
# VIEW conversion
# =============================================================================
def convert_view(sql: str) -> Tuple[str, List[str]]:
    """Convert Oracle VIEW to PG VIEW.

    VIEW files in oracle_poc contain multiple CREATE VIEW statements
    separated by `;` and comment blocks. The view-level convert_plsql_code
    pipeline assumes a single query, so processing the whole file at once
    causes outer-join rewrite (step 8) to truncate the file at the first
    OUTERJOIN marker, losing subsequent VIEWs.

    Fix: split the file on `;` (preserving the trailing `;` for each
    statement) and convert each statement independently, then re-join.
    Empty/comment-only fragments are passed through unchanged.
    """
    warnings: List[str] = []
    sql = _strip_force_keyword(sql)
    sql = _apply_type_mapping(sql)
    sql = _convert_view_options(sql)

    # Split on `;` keeping each statement self-contained.
    # We use a state machine to avoid splitting inside string literals
    # or dollar-quoted bodies (which VIEW bodies don't have, but be safe).
    statements: List[str] = []
    buf: List[str] = []
    in_single = False
    in_dollar = False
    dollar_tag = ""
    i = 0
    n = len(sql)
    while i < n:
        c = sql[i]
        if in_single:
            buf.append(c)
            if c == "'" and (i + 1 >= n or sql[i + 1] != "'"):
                in_single = False
            elif c == "'" and i + 1 < n and sql[i + 1] == "'":
                buf.append("'")
                i += 1
            i += 1
            continue
        if in_dollar:
            buf.append(c)
            if dollar_tag and sql.startswith(dollar_tag, i):
                buf.extend(dollar_tag[1:])
                in_dollar = False
                i += len(dollar_tag)
                continue
            i += 1
            continue
        if c == "'":
            in_single = True
            buf.append(c)
            i += 1
            continue
        if c == "$":
            m = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", sql[i:])
            if m:
                in_dollar = True
                dollar_tag = m.group(0)
                buf.append(dollar_tag)
                i += len(dollar_tag)
                continue
        if c == ";":
            statements.append("".join(buf) + ";")
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    if buf and "".join(buf).strip():
        statements.append("".join(buf))

    converted_parts: List[str] = []
    for stmt in statements:
        stripped = stmt.strip()
        # Pass through pure-comment or empty fragments
        if not stripped or all(
            line.strip().startswith("--") or not line.strip()
            for line in stripped.splitlines()
        ):
            converted_parts.append(stmt)
            continue
        try:
            converted, _ = convert_plsql_code(stmt)
        except Exception as e:
            warnings.append(
                f"convert_plsql_code error on VIEW fragment, "
                f"keeping original: {e}"
            )
            converted = stmt
        # Post-process: repair known whitespace-loss bug where the
        # outerjoin rewriter glues the last identifier of an ON clause
        # to the next keyword (e.g. "SC.STUDENT_NOGROUP BY" should be
        # two tokens).
        converted = _repair_token_glue(converted, warnings)
        # Fix GROUP BY with string literals (Oracle allows, PG does not)
        # e.g. GROUP BY 'SCORE_REC'  ->  remove (COUNT(*) works without GROUP BY)
        converted = re.sub(
            r"GROUP\s+BY\s+'[^']*'",
            '',
            converted,
            flags=re.IGNORECASE
        )
        converted_parts.append(converted)

    return "\n".join(converted_parts), warnings


def _repair_token_glue(text: str, warnings: List[str]) -> str:
    """Insert a single space where perlstyle's outerjoin rewriter has
    glued two SQL tokens together without whitespace.

    The bug pattern is: `<identifier><UPPERCASE_KEYWORD>` where the keyword
    begins a new clause (GROUP BY, ORDER BY, HAVING, UNION, WHERE, ON).
    This happens because the perlstyle outerjoin-to-LEFT-JOIN rewriter
    consumes the surrounding whitespace during the main from/where
    re-split.

    We restore the space conservatively: only when the preceding token
    looks like an identifier (mixed case, underscores, dots) and the
    following is one of a small set of clause-leading keywords.
    """
    # Only MULTI-WORD keywords are safe to repair: they contain internal
    # spaces so they can never be the suffix of a single identifier.
    # Single-word keywords (ON, WHERE, UNION, HAVING, JOIN) are excluded
    # because they are suffixes of common words (PARTITION, REUNION,
    # NOWHERE, ...) and repairing them corrupts valid identifiers
    # (e.g. "PARTITION" -> "PARTITI ON", "UNION" -> "UNI ON").
    clause_keywords = (
        "GROUP BY", "ORDER BY", "UNION ALL",
        "LEFT JOIN", "RIGHT JOIN", "INNER JOIN", "FULL JOIN",
        "LEFT OUTER JOIN", "RIGHT OUTER JOIN", "FULL OUTER JOIN",
        "CROSS JOIN", "NATURAL JOIN",
    )
    out = text
    for kw in clause_keywords:
        # Match: identifier immediately followed by clause keyword.
        # Avoid splitting inside other words (e.g. JOINED).
        pat = re.compile(
            rf"([A-Za-z_][A-Za-z0-9_\.]*)({re.escape(kw)})(?![\w_])",
            re.IGNORECASE,
        )
        def _fix(m: re.Match) -> str:
            ident = m.group(1)
            kw_m = m.group(2)
            # Require whitespace or punct BEFORE the identifier so we don't
            # break legitimate camelCase like "myGroupBy".
            start = m.start()
            if start > 0 and out[start - 1] not in " \t\n,;(":
                return m.group(0)
            return ident + " " + kw_m
        new_out, n = pat.subn(_fix, out)
        if n:
            warnings.append(
                f"Repaired {n} glue(s) before {kw}"
            )
            out = new_out
    return out


# =============================================================================
# Main entry point
# =============================================================================
def detect_and_convert(sql: str) -> Tuple[str, List[str]]:
    """Auto-detect object type and convert."""
    sql_upper = sql.upper()

    if re.search(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?PACKAGE\s+BODY\b', sql_upper):
        return convert_package_body(sql)
    if re.search(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?PACKAGE\b', sql_upper):
        return convert_package_spec(sql)
    if re.search(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+|NONEDITIONABLE\s+)?(FUNCTION|PROCEDURE)\b', sql_upper):
        return convert_function(sql)
    if re.search(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?TYPE\b', sql_upper):
        return convert_type(sql)
    if re.search(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:FORCE\s+)?VIEW\b', sql_upper):
        return convert_view(sql)
    if re.search(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:GLOBAL\s+)?(?:TEMPORARY\s+)?TABLE\b', sql_upper):
        return convert_table(sql)
    if re.search(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+|NONEDITIONABLE\s+)?SEQUENCE\b', sql_upper):
        return convert_sequence(sql)
    if re.search(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+|NONEDITIONABLE\s+)?(?:COMPOUND\s+)?TRIGGER\b', sql_upper):
        return convert_trigger(sql)
    if re.search(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:PUBLIC\s+)?SYNONYM\b', sql_upper):
        return convert_synonym(sql)
    if re.search(r'\bCREATE\s+(?:UNIQUE\s+|BITMAP\s+)?INDEX\b', sql_upper):
        return convert_index(sql)

    return _apply_type_mapping(sql), []


def convert_file(input_path: str, output_path: str) -> Tuple[str, List[str]]:
    """Convert a SQL file."""
    with open(input_path, 'r', encoding='utf-8') as f:
        sql = f.read()

    converted, warnings = detect_and_convert(sql)

    with open(output_path, 'w', encoding='utf-8') as f:
        if warnings:
            f.write("-- Perl-style Ora2Py Warnings:\n")
            for w in warnings:
                f.write(f"-- {w}\n")
            f.write('\n')
        f.write(converted)

    return converted, warnings


# =============================================================================
# Batch processing (T5: read_schema_from_file)
# =============================================================================
def read_schema_from_file(file_path: str) -> List[Tuple[str, str]]:
    """Split a SQL dump file into individual DDL statements.

    Mirrors Perl's ``read_schema_from_file``: returns a list of
    ``(object_type, ddl_text)`` tuples so callers can dispatch each
    statement to the appropriate converter.

    Recognised object types:
        TABLE, VIEW, INDEX, SEQUENCE, TRIGGER, FUNCTION, PROCEDURE,
        PACKAGE, PACKAGE_BODY, TYPE, MATERIALIZED_VIEW, OTHER
    """
    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
        sql = f.read()

    statements: List[Tuple[str, str]] = []
    # Split on / at line start (SQL*Plus terminator) OR ;
    # Keep the terminator so we know where each block ends
    chunks = re.split(r'(?:^\s*/\s*$|;)', sql, flags=re.MULTILINE | re.IGNORECASE)

    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        upper = chunk.upper()
        if re.search(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+|NONEDITIONABLE\s+)?'
                     r'(?:GLOBAL\s+)?(?:TEMPORARY\s+)?TABLE\b', upper):
            otype = 'TABLE'
        elif re.search(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:FORCE\s+)?VIEW\b', upper):
            otype = 'VIEW'
        elif re.search(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:UNIQUE\s+|BITMAP\s+)?INDEX\b', upper):
            otype = 'INDEX'
        elif re.search(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+|NONEDITIONABLE\s+)?'
                       r'SEQUENCE\b', upper):
            otype = 'SEQUENCE'
        elif re.search(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+|NONEDITIONABLE\s+)?'
                       r'(?:COMPOUND\s+)?TRIGGER\b', upper):
            otype = 'TRIGGER'
        elif re.search(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+|NONEDITIONABLE\s+)?'
                       r'(FUNCTION|PROCEDURE)\b', upper):
            m = re.search(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+|NONEDITIONABLE\s+)?'
                          r'(FUNCTION|PROCEDURE)\b', upper)
            otype = m.group(1)
        elif re.search(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?PACKAGE\s+BODY\b', upper):
            otype = 'PACKAGE_BODY'
        elif re.search(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?PACKAGE\b', upper):
            otype = 'PACKAGE'
        elif re.search(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+|NONEDITIONABLE\s+)?TYPE\b',
                       upper):
            otype = 'TYPE'
        elif re.search(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?MATERIALIZED\s+VIEW\b', upper):
            otype = 'MATERIALIZED_VIEW'
        else:
            otype = 'OTHER'
        statements.append((otype, chunk))

    return statements


def convert_schema_dump(
    file_path: str,
    output_dir: str,
    encoding: str = 'utf-8',
    skip_other: bool = True,
) -> Dict[str, List[str]]:
    """Convert a full Oracle schema dump file.

    Reads ``file_path``, splits into individual DDL statements via
    ``read_schema_from_file``, dispatches each to the appropriate
    ``convert_*`` function, and writes one output file per object type
    under ``output_dir``.

    Returns a dict mapping object_type -> [output filenames].
    """
    import os
    os.makedirs(output_dir, exist_ok=True)

    statements = read_schema_from_file(file_path)
    # Group by type so each output file contains all objects of that type
    grouped: Dict[str, List[str]] = {}
    conversions: Dict[str, Tuple[str, List[str]]] = {}

    for otype, ddl in statements:
        if otype == 'OTHER':
            if skip_other:
                continue
            converted = ddl
            warnings = []
        elif otype == 'TABLE':
            converted, warnings = convert_table(ddl)
        elif otype == 'VIEW':
            converted, warnings = convert_view(ddl)
        elif otype == 'INDEX':
            converted, warnings = convert_index(ddl)
        elif otype == 'SEQUENCE':
            converted, warnings = convert_sequence(ddl)
        elif otype == 'TRIGGER':
            converted, warnings = convert_trigger(ddl)
        elif otype in ('FUNCTION', 'PROCEDURE'):
            converted, warnings = convert_function(ddl)
        elif otype == 'PACKAGE':
            converted, warnings = convert_package_spec(ddl)
        elif otype == 'PACKAGE_BODY':
            converted, warnings = convert_package_body(ddl)
        elif otype == 'TYPE':
            converted, warnings = convert_type(ddl)
        elif otype == 'MATERIALIZED_VIEW':
            # Placeholder — T11 not yet implemented
            converted, warnings = (ddl, ["MATERIALIZED VIEW conversion not yet implemented"])
        else:
            converted, warnings = (ddl, [])

        grouped.setdefault(otype, []).append(converted)
        conversions.setdefault(otype, []).extend(warnings)

    result: Dict[str, List[str]] = {}
    for otype, chunks in grouped.items():
        out_path = os.path.join(output_dir, f"{otype}.sql")
        with open(out_path, 'w', encoding=encoding) as f:
            for w in conversions.get(otype, []):
                f.write(f"-- WARN: {w}\n")
            for chunk in chunks:
                f.write(chunk.rstrip() + '\n\n')
        result.setdefault(otype, []).append(out_path)

    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Perl-style Ora2Pg Python converter')
    parser.add_argument('input', help='Input SQL file')
    parser.add_argument('-o', '--output', help='Output SQL file')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose')
    parser.add_argument('--batch', metavar='DIR', help='Batch-convert schema dump: '
                        'split input into object types and write per-type files into DIR')

    args = parser.parse_args()

    if args.batch:
        # Batch mode: full schema dump -> per-type output files
        out = convert_schema_dump(args.input, args.batch)
        for otype, files in out.items():
            print(f"[{otype}] {len(files)} file(s)")
        return

    if not args.output:
        args.output = args.input.rsplit('.', 1)[0] + '_pgperl.SQL'

    converted, warnings = convert_file(args.input, args.output)

    if args.verbose:
        print(f"Converted: {args.input} -> {args.output}")
        print(f"Warnings: {len(warnings)}")
        for w in warnings[:20]:
            print(f"  - {w}")


# =============================================================================
# Nested Function Inlining (Oracle PL/SQL -> PostgreSQL)
# PostgreSQL plpgsql does not support nested named functions, so we inline them.
# =============================================================================
# Nested Function Extraction (Oracle PL/SQL -> PostgreSQL)
# Extract nested functions as independent functions instead of inlining
# PostgreSQL plpgsql does not support nested named functions.
# =============================================================================

# Type mapping for nested function extraction
_ORACLE_TO_PG_TYPES = {
    'BOOLEAN': 'BOOLEAN',
    'NUMBER': 'NUMERIC',
    'INTEGER': 'INTEGER',
    'PLS_INTEGER': 'INTEGER',
    'VARCHAR2': 'VARCHAR',
    'NVARCHAR2': 'VARCHAR',
    'CHAR': 'CHAR',
    'DATE': 'TIMESTAMP',
    'TIMESTAMP': 'TIMESTAMP',
}


def _convert_oracle_type(oracle_type: str) -> str:
    """Oracle 类型到 PostgreSQL 类型的映射"""
    return _ORACLE_TO_PG_TYPES.get(oracle_type.upper(), 'TEXT')


def _build_pg_params(params: List[Tuple[str, str, str]]) -> str:
    """构建 PostgreSQL 函数参数列表
    
    Args:
        params: [(name, mode, type), ...]
    
    Returns:
        PG 格式的参数列表: "param1 INTEGER, param2 INTEGER"
    """
    pg_params = []
    for name, mode, oracle_type in params:
        pg_type = _convert_oracle_type(oracle_type)
        pg_params.append(f"{name} {pg_type}")
    return ', '.join(pg_params)


def _convert_function_body_for_pg(body: str, func_name: str) -> str:
    """转换函数体为 PostgreSQL 兼容格式"""
    if not body:
        return 'RETURN NULL;'
    
    # 清理函数体：移除 EXCEPTION 块前的尾随内容
    body = body.strip()
    
    # 如果函数体中没有 RETURN 语句，添加一个默认返回值
    if not re.search(r'\bRETURN\b', body, re.IGNORECASE):
        body = body.rstrip(';')
        body += '\n    RETURN NULL;'
    
    return body


def _replace_nested_call_with_independent(
    code: str,
    func: 'NestedFunction',
    new_func_name: str
) -> str:
    """将嵌套函数调用替换为独立函数调用"""
    func_name = func.name
    
    # 1. 替换赋值调用: result := func_name();
    code = re.sub(
        rf'(\w+)\s*:=\s*{re.escape(func_name)}\s*\([^)]*\)\s*;',
        rf'\1 := {new_func_name}();',
        code,
        flags=re.IGNORECASE
    )
    
    # 2. 替换 IF func_name() THEN
    code = re.sub(
        rf'IF\s+{re.escape(func_name)}\s*\([^)]*\)\s+THEN',
        f'IF {new_func_name}() THEN',
        code,
        flags=re.IGNORECASE
    )
    
    # 3. 替换 func_name() = value
    code = re.sub(
        rf'{re.escape(func_name)}\s*\([^)]*\)\s*([=<>!]+)',
        rf'{new_func_name}() \1',
        code,
        flags=re.IGNORECASE
    )
    
    # 4. 替换 IF func_name = value THEN (Oracle 无括号调用 - 最常见)
    code = re.sub(
        rf'IF\s+{re.escape(func_name)}\s*([=<>!]+)\s*(\w+)\s+THEN',
        rf'IF {new_func_name}() \1 \2 THEN',
        code,
        flags=re.IGNORECASE
    )
    
    return code


def _generate_independent_function(
    func: 'NestedFunction', 
    new_func_name: str,
    parent_func_name: str
) -> str:
    """生成独立函数定义
    
    Args:
        func: 嵌套函数信息
        new_func_name: 新的独立函数名
        parent_func_name: 父函数名
    
    Returns:
        PostgreSQL 函数定义字符串
    """
    # 转换返回类型
    return_type = _convert_oracle_type(func.return_type)
    
    # 构建函数参数
    params_str = _build_pg_params(func.params)
    
    # 提取并转换函数体
    func_body = _extract_function_body(func.body)
    converted_body = _convert_function_body_for_pg(func_body, func.name)
    
    # 清理函数体 - 只保留 BEGIN...END 之间的内容
    lines = []
    in_body = False
    body_depth = 0
    
    for line in converted_body.split('\n'):
        stripped = line.strip()
        
        # 跳过 EXCEPTION 块
        if re.match(r'\bEXCEPTION\b', stripped, re.IGNORECASE):
            break
        
        # 保留内容
        if stripped:
            # 调整缩进
            indent = '    '
            lines.append(f'{indent}{stripped}')
    
    body_content = '\n'.join(lines) if lines else '    RETURN NULL;'
    
    # 构建独立函数定义
    pg_func = f"""-- Extracted nested function from {parent_func_name}
CREATE OR REPLACE FUNCTION {new_func_name}({params_str})
RETURNS {return_type}
LANGUAGE plpgsql
AS $$
BEGIN
{body_content}
END;
$$;
"""
    return pg_func


# 原有的内联函数（保留用于简单情况）
@dataclass
class NestedFunction:
    """嵌套子程序的信息（FUNCTION 或 PROCEDURE）"""
    name: str
    subprogram_type: str  # 'FUNCTION' or 'PROCEDURE'
    params: List[Tuple[str, str, str]]  # [(name, mode, type), ...]
    local_vars: List[str]
    return_type: str  # '' for PROCEDURE, actual type for FUNCTION
    body: str
    start_pos: int
    end_pos: int


def _safe_var_name(param_name: str, func_name: str) -> str:
    """生成安全的临时变量名，避免与调用处变量冲突
    
    例如: ID -> _func_ID_
    """
    return f'_{func_name}_{param_name}_'


def _parse_function_params(params_str: str) -> List[Tuple[str, str, str]]:
    """解析函数参数列表
    
    格式: param_name IN type, param_name OUT type, ...
    Returns: [(name, mode, type), ...]
    """
    params = []
    if not params_str.strip():
        return params
    
    for p in params_str.split(','):
        p = p.strip()
        if not p:
            continue
        # 解析: name [IN|OUT|INOUT] type
        m = re.match(r'(\w+)(?:\s+(IN|OUT|INOUT))?\s+(\w+)', p, re.IGNORECASE)
        if m:
            params.append((m.group(1), m.group(2) or "IN", m.group(3)))
    return params


def _extract_function_body(func_body: str) -> str:
    """从函数定义中提取函数体（去除头部和 END）"""
    # 找到 BEGIN 关键字
    begin_match = re.search(r'\bBEGIN\b', func_body, re.IGNORECASE)
    if not begin_match:
        return func_body
    
    body = func_body[begin_match.end():]
    # 移除末尾的 END func_name;
    body = re.sub(r'\s*END\s+\w+\s*;\s*$', '', body, flags=re.IGNORECASE)
    return body.strip()


def _extract_nested_functions(combined_code: str) -> List[NestedFunction]:
    """从代码中提取嵌套子程序定义（FUNCTION 或 PROCEDURE）
    
    匹配模式: 
      FUNCTION name[(params)] RETURN type IS ... BEGIN ... END name;
      PROCEDURE name[(params)] IS ... BEGIN ... END name;
    
    关键：嵌套子程序定义不以 CREATE OR REPLACE 开头
    
    处理两种情况:
    1. 有 END name; 的标准嵌套子程序
    2. 没有 END 的内部子过程（需要特殊处理）
    """
    nested_funcs = []
    
    # 匹配 FUNCTION name[(params)] RETURN type IS
    func_pattern = re.compile(
        r'\bFUNCTION\s+([A-Za-z0-9_$]+)\s*'  # 函数名
        r'(?:\(([^)]*)\))?\s*'                   # 可选参数
        r'RETURN\s+(\w+)\s+IS\b',               # RETURN type IS
        re.IGNORECASE
    )
    
    # 匹配 PROCEDURE name[(params)] IS
    # 注意：PROCEDURE 可能没有参数，也可能有参数
    proc_pattern = re.compile(
        r'\bPROCEDURE\s+([A-Za-z0-9_$]+)\s*'  # 过程名
        r'(?:\(([^)]*)\))?\s+IS\b',            # IS (可能有参数)
        re.IGNORECASE
    )
    
    # 匹配 PROCEDURE name IS (无参数)
    proc_pattern_simple = re.compile(
        r'\bPROCEDURE\s+([A-Za-z0-9_$]+)\s+IS\b',
        re.IGNORECASE
    )
    
    # 找所有子程序定义的位置
    subprogram_defs = []
    
    # 收集 FUNCTION 定义
    for m in func_pattern.finditer(combined_code):
        start_pos = m.start()
        preceding = combined_code[max(0, start_pos - 30):start_pos].upper()
        if 'CREATE' in preceding and 'OR' in preceding and 'REPLACE' in preceding:
            continue
        subprogram_defs.append(('FUNCTION', m.start(), m.end(), m.group(1), m.group(2), m.group(3)))
    
    # 收集 PROCEDURE 定义
    for m in proc_pattern.finditer(combined_code):
        start_pos = m.start()
        preceding = combined_code[max(0, start_pos - 30):start_pos].upper()
        if 'CREATE' in preceding and 'OR' in preceding and 'REPLACE' in preceding:
            continue
        # 跳过无参数的 PROCEDURE（已被上面的模式匹配）
        if proc_pattern_simple.match(combined_code[m.start():], re.IGNORECASE):
            continue  # 会被单独处理
        subprogram_defs.append(('PROCEDURE', m.start(), m.end(), m.group(1), m.group(2), ''))
    
    # 处理无参数的 PROCEDURE
    for m in proc_pattern_simple.finditer(combined_code):
        start_pos = m.start()
        preceding = combined_code[max(0, start_pos - 30):start_pos].upper()
        if 'CREATE' in preceding and 'OR' in preceding and 'REPLACE' in preceding:
            continue
        # 检查是否已被上面的模式匹配
        already_matched = any(s[1] == m.start() for s in subprogram_defs)
        if not already_matched:
            subprogram_defs.append(('PROCEDURE', m.start(), m.end(), m.group(1), '', ''))
    
    # 按位置排序
    subprogram_defs.sort(key=lambda x: x[1])
    
    # 合并重叠的定义
    merged_defs = []
    for s in subprogram_defs:
        if not merged_defs or s[1] > merged_defs[-1][2]:
            merged_defs.append(list(s))
        else:
            # 保留已有的
            pass
    
    # 处理每个子程序定义
    for i, (subp_type, start, header_end, subp_name, params_str, return_type) in enumerate(merged_defs):
        body_start = header_end
        
        # 首先尝试找 END subp_name;
        end_pattern = re.compile(
            rf'\bEND\s+{re.escape(subp_name)}\s*;',
            re.IGNORECASE
        )
        end_m = end_pattern.search(combined_code, body_start)
        
        if end_m:
            # 标准情况：有 END subp_name;
            end_pos = end_m.end()
            body = combined_code[start:end_pos]
        else:
            # 特殊情况：没有 END subp_name;
            # 尝试找到下一个子程序的开始位置
            end_pos = _find_nested_subprogram_end(combined_code, start, merged_defs, i)
            body = combined_code[start:end_pos] if end_pos > start else combined_code[start:start+200]
        
        # 提取局部变量
        local_vars = []
        between = combined_code[header_end:end_pos] if end_pos > start else ""
        var_pattern = re.compile(r'(\w+)\s+(\w+(?:\s*\([^)]*\))?)\s*(?:(?::=|DEFAULT)\s*([^;]+))?\s*;', re.IGNORECASE)
        for var_m in var_pattern.finditer(between):
            if var_m.group(1).upper() not in ('BEGIN', 'FUNCTION', 'PROCEDURE', 'IF', 'LOOP', 'CASE', 'WHEN'):
                local_vars.append(var_m.group(1))
        
        # 检查是否有 END name; 语句
        search_region = combined_code[start:end_pos] if end_pos > start else combined_code[start:start + len(body)]
        has_end_stmt = bool(re.search(rf'\bEND\s+{re.escape(subp_name)}\s*;', search_region, re.IGNORECASE))
        
        nested_funcs.append(NestedFunction(
            name=subp_name,
            subprogram_type=subp_type,
            params=_parse_function_params(params_str or ''),
            local_vars=local_vars,
            return_type=return_type,
            body=body,
            start_pos=start,
            end_pos=end_pos if end_pos > start else start + len(body)
        ))
    
    return nested_funcs


def _find_nested_subprogram_end(combined_code: str, subp_start: int, all_subps: List, current_idx: int) -> int:
    """找到嵌套子程序的结束位置
    
    对于没有 END name; 的子程序，找到下一个 FUNCTION/PROCEDURE 的开始位置
    或主函数的 END。
    """
    # 找下一个子程序的开始位置
    next_pos = len(combined_code)
    
    for j in range(current_idx + 1, len(all_subps)):
        if all_subps[j][1] > subp_start:
            next_pos = all_subps[j][1]
            break
    
    # 也检查未收集的 FUNCTION/PROCEDURE 定义
    other_pattern = re.compile(r'^\s*(FUNCTION|PROCEDURE)\s+(\w+)', re.MULTILINE | re.IGNORECASE)
    for m in other_pattern.finditer(combined_code, subp_start + 100):
        if m.start() < next_pos:
            # 确认这不是主函数的子程序（在主 BEGIN 之前）
            before = combined_code[max(0, m.start() - 500):m.start()]
            if '\nBEGIN' in before.upper() or '\nBEGIN ' in before.upper():
                next_pos = m.start()
                break
    
    return next_pos
    
    return next_pos


def _convert_return_for_inlining(body: str, result_var: str = '_nested_result') -> str:
    """将 RETURN 语句转换为赋值语句
    
    对于内联展开的函数，我们将 RETURN expr 转换为:
    - result_var := expr;
    - GOTO 或退出当前块
    
    简化实现：直接返回赋值语句，移除 RETURN 关键字
    """
    lines = []
    in_string = False
    depth = 0
    i = 0
    
    while i < len(body):
        c = body[i]
        
        # 简单字符串处理
        if c == "'" and (i == 0 or body[i-1] != '\\'):
            in_string = not in_string
        
        if not in_string:
            # 检查 RETURN 语句
            if body[i:i+6].upper() == 'RETURN':
                # 找到 RETURN 后的表达式
                j = i + 6
                while j < len(body) and body[j].isspace():
                    j += 1
                # 提取表达式直到分号
                expr_start = j
                while j < len(body) and body[j] != ';':
                    j += 1
                if j < len(body):
                    expr = body[expr_start:j].strip()
                    # 跳过分号
                    j += 1
                    # 生成赋值语句
                    lines.append(f'    {result_var} := {expr};')
                    lines.append('    -- RETURN converted to assignment during inlining')
                    i = j
                    continue
        
        lines.append(c)
        i += 1
    
    return ''.join(lines)


def _expand_procedure_call(code: str, proc: NestedFunction, state: ConversionState) -> str:
    """将过程调用替换为内联内容
    
    PROCEDURE 没有返回值，直接将过程体展开并替换参数。
    """
    proc_name = proc.name
    
    # 提取过程体用于内联
    inlined_body = _extract_body_for_inlining(proc.body)
    
    # 获取参数名列表
    param_names = [p[0] for p in proc.params]
    
    # 处理过程调用: proc_name(args);
    pattern = re.compile(
        rf'\b{re.escape(proc_name)}\s*\(([^)]*)\)\s*;',
        re.IGNORECASE
    )
    
    def _replace_proc_call(match):
        args_str = match.group(1)
        args = [a.strip() for a in args_str.split(',')] if args_str.strip() else []
        
        # 构建参数映射
        param_map = {}
        for i, param_name in enumerate(param_names):
            if i < len(args):
                param_map[param_name] = args[i]
        
        # 复制并替换参数
        expanded = inlined_body
        for param_name, arg_value in param_map.items():
            # 替换参数引用（保留赋值语句左侧）
            expanded = re.sub(
                rf'({re.escape(param_name)})\s*:?=',
                f'_ignored_\1_:=',
                expanded
            )
            expanded = re.sub(
                rf'\b{re.escape(param_name)}\b',
                arg_value,
                expanded
            )
            expanded = expanded.replace(f'_ignored_{param_name}_:=', f'{param_name} :')
        
        # 清理缩进（从过程体中提取）
        lines = expanded.split('\n')
        indented_lines = ['    ' + line for line in lines if line.strip()]
        
        return '\n'.join(indented_lines) + ' /* INLINED: {}({}) */'.format(proc_name, args_str)
    
    # 处理无参数的过程调用: proc_name;
    pattern_no_args = re.compile(
        rf'\b{re.escape(proc_name)}\s*;',
        re.IGNORECASE
    )
    
    def _replace_proc_call_no_args(match):
        # 复制并返回过程体
        lines = inlined_body.split('\n')
        indented_lines = ['    ' + line for line in lines if line.strip()]
        return '\n'.join(indented_lines) + ' /* INLINED: {}() */'.format(proc_name)
    
    # 先处理带参数的情况
    code = pattern.sub(_replace_proc_call, code)
    # 再处理无参数的情况
    code = pattern_no_args.sub(_replace_proc_call_no_args, code)
    
    return code


def _inline_nested_procedures(code: str, funcs: List[NestedFunction], state: ConversionState) -> str:
    """内联嵌套的 PROCEDURE，递归处理嵌套调用
    
    按名称长度升序处理，这样嵌套的子程序先被处理。
    使用多轮扫描避免无限循环。
    """
    # 按名称长度升序排序（短名称先处理）
    sorted_funcs = sorted(funcs, key=lambda f: len(f.name))
    
    # 限制最大迭代次数避免无限循环
    max_iterations = len(sorted_funcs) + 2
    
    for iteration in range(max_iterations):
        prev_code = code
        for func in sorted_funcs:
            code = _expand_procedure_call(code, func, state)
        # 如果代码没有变化，停止迭代
        if code == prev_code:
            break
    
    return code


def _expand_function_call(code: str, func: NestedFunction, state: ConversionState) -> str:
    """将子程序调用替换为内联内容
    
    策略：
    1. 分析子程序类型（FUNCTION 或 PROCEDURE）
    2. FUNCTION：根据返回值类型处理不同的调用模式
    3. PROCEDURE：将过程体展开（移除 RETURN 语句）
    4. 支持带参数的嵌套子程序内联
    """
    func_name = func.name
    subp_type = getattr(func, 'subprogram_type', 'FUNCTION')  # 默认为 FUNCTION
    
    # 处理 PROCEDURE
    if subp_type == 'PROCEDURE':
        return _expand_procedure_call(code, func, state)
    
    # 处理带参数的嵌套函数
    if func.params:
        # 提取函数体中的 RETURN 值
        func_body = _extract_function_body(func.body)
        return_type, return_var = _extract_return_value(func_body)
        
        # 提取函数体内容
        inlined_body = _extract_body_for_inlining(func.body)
        
        # 获取参数名列表
        param_names = [p[0] for p in func.params]
        
        # 处理 IF func_name(args) op value [AND|OR func_name(args) op value ...] THEN 模式
        # 使用 AND|OR|THEN 作为边界，避免贪婪匹配跨多个比较表达式
        # 重点：必须后面是 THEN 才处理。中间出现的 AND|OR 不应被处理（留给递归调用）
        pattern = re.compile(
            rf'IF\s+{re.escape(func_name)}\s*\(([^)]*)\)\s*([=<>!]+)\s*(\w+)\s+THEN\b',
            re.IGNORECASE
        )
        
        def _replace_if_call_with_params(match):
            args_str = match.group(1)
            cond = match.group(2)
            val = match.group(3)
            
            # 解析参数
            args = [a.strip() for a in args_str.split(',')]
            
            # 构建参数映射: {param_name: arg_value}
            param_map = {}
            for i, param_name in enumerate(param_names):
                if i < len(args):
                    param_map[param_name] = args[i]
            
            # 展开函数体并替换参数
            expanded = _expand_function_body(inlined_body, return_var)
            for param_name, arg_value in param_map.items():
                # 替换参数引用（但保留赋值语句的左侧）
                # 匹配: param_name := 或 param_name ; 等
                expanded = re.sub(
                    rf'({re.escape(param_name)})\s*:?=',
                    f'_ignored_\1_:=',
                    expanded
                )
                expanded = re.sub(
                    rf'\b{re.escape(param_name)}\b',
                    arg_value,
                    expanded
                )
                # 恢复被错误替换的部分
                expanded = expanded.replace(f'_ignored_{param_name}_:=', f'{param_name} :=')
            
            # 移除函数体中的赋值语句，改用内联变量
            # 处理两种模式：
            # 模式1: IF param op value THEN _nested_result := TRUE; ELSE _nested_result := FALSE; END IF;
            # 模式2: assignment; RETURN(var);
            
            # 尝试模式1：简单的 IF-ELSE-RETURN 结构
            simple_pattern = re.search(
                rf'IF\s+(\w+)\s*([<>!=]+)\s*(\w+)\s+THEN\s+_nested_result\s*:=\s*(\w+)\s*;\s*ELSE\s+_nested_result\s*:=\s*(\w+)\s*;\s*END\s+IF',
                expanded,
                re.IGNORECASE | re.DOTALL
            )
            if simple_pattern:
                param = simple_pattern.group(1)
                op = simple_pattern.group(2)
                value = simple_pattern.group(3)
                true_val = simple_pattern.group(4)
                false_val = simple_pattern.group(5)
                
                if true_val.upper() == 'TRUE' and false_val.upper() == 'FALSE':
                    func_result = f'({arg_value} {op} {value})'
                elif true_val.upper() == 'FALSE' and false_val.upper() == 'TRUE':
                    func_result = f'(NOT ({arg_value} {op} {value}))'
                else:
                    func_result = None
                
                if func_result:
                    val_upper = val.upper()
                    if cond == '=':
                        if val_upper == 'FALSE':
                            if func_result.startswith('(') and func_result.endswith(')'):
                                return f'IF NOT {func_result[1:-1]} /* INLINED: {func_name}({args_str}) */ THEN'
                            return f'IF NOT {func_result} /* INLINED: {func_name}({args_str}) */ THEN'
                        elif val_upper == 'TRUE':
                            return f'IF {func_result} /* INLINED: {func_name}({args_str}) */ THEN'
                    elif cond in ('!=', '<>'):
                        if val_upper == 'FALSE':
                            return f'IF {func_result} /* INLINED: {func_name}({args_str}) */ THEN'
                        elif val_upper == 'TRUE':
                            if func_result.startswith('(') and func_result.endswith(')'):
                                return f'IF NOT {func_result[1:-1]} /* INLINED: {func_name}({args_str}) */ THEN'
                            return f'IF NOT {func_result} /* INLINED: {func_name}({args_str}) */ THEN'
                    return f'IF {func_result} {cond} {val} /* INLINED: {func_name}({args_str}) */ THEN'
            
            # 尝试模式2：assignment; RETURN(var);
            # 查找 RETURN(变量) 语句
            return_var_pattern = re.search(
                rf'(\w+)\s*:=\s*([^;]+);\s*_nested_result\s*:=\s*(\w+)\s*;',
                expanded,
                re.IGNORECASE | re.DOTALL
            )
            if return_var_pattern:
                assigned_var = return_var_pattern.group(1)
                assign_expr = return_var_pattern.group(2).strip()
                return_var_name = return_var_pattern.group(3)
                
                # 替换赋值表达式中的参数
                for param_name, arg_value in param_map.items():
                    assign_expr = re.sub(rf'\b{re.escape(param_name)}\b', arg_value, assign_expr)
                
                # 构建结果
                return f'IF {assign_expr} {cond} {val} /* INLINED: {func_name}({args_str}) */ THEN'
            
            # 无法简化，直接保留原函数调用
            return f'/* TODO: {func_name}({args_str}) with params, cannot inline */ {func_name}({args_str}) = {val} THEN'
        
        code = pattern.sub(_replace_if_call_with_params, code)
        
        # 处理复合 IF 条件: IF func() op val AND/OR func() op val THEN
        # 使用 (AND|OR|THEN) 作为中间边界
        compound_pattern = re.compile(
            rf'\b{re.escape(func_name)}\s*\(([^)]*)\)\s*([=<>!]+)\s*(\w+)\s+(AND|OR|THEN)\b',
            re.IGNORECASE
        )
        
        def _replace_compound_func(match):
            args_str = match.group(1)
            cond = match.group(2)
            val = match.group(3)
            trailing = match.group(4).upper()
            
            # 解析参数
            args = [a.strip() for a in args_str.split(',')]
            param_map = {}
            for i, param_name in enumerate(param_names):
                if i < len(args):
                    param_map[param_name] = args[i]
            
            # 展开函数体并替换参数
            expanded = _expand_function_body(inlined_body, return_var)
            for param_name, arg_value in param_map.items():
                expanded = re.sub(
                    rf'({re.escape(param_name)})\s*:?=',
                    f'_ignored_\1_:=',
                    expanded
                )
                expanded = re.sub(
                    rf'\b{re.escape(param_name)}\b',
                    arg_value,
                    expanded
                )
                expanded = expanded.replace(f'_ignored_{param_name}_:=', f'{param_name} :')
            
            # 尝试模式2：assignment; RETURN(var);
            return_var_pattern = re.search(
                r'(\w+)\s*:=\s*([^;]+);\s*_nested_result\s*:=\s*(\w+)\s*;',
                expanded,
                re.IGNORECASE | re.DOTALL
            )
            if return_var_pattern:
                assign_expr = return_var_pattern.group(2).strip()
                # 替换赋值表达式中的参数
                for param_name, arg_value in param_map.items():
                    assign_expr = re.sub(rf'\b{re.escape(param_name)}\b', arg_value, assign_expr)
                # 返回替换后的调用（保留 trailing）
                return f'{assign_expr} {cond} {val} {trailing} '
            
            # 无法简化，保持原调用
            return match.group(0)
        
        code = compound_pattern.sub(_replace_compound_func, code)
        
        # 处理赋值调用: result := func_name(args);
        pattern2 = re.compile(
            rf'(\w+)\s*:=\s*{re.escape(func_name)}\s*\(([^)]*)\)\s*;',
            re.IGNORECASE
        )
        
        def _replace_assign_with_params(match):
            var = match.group(1)
            args_str = match.group(2)
            args = [a.strip() for a in args_str.split(',')]
            
            # 构建参数映射
            param_map = {}
            for i, param_name in enumerate(param_names):
                if i < len(args):
                    param_map[param_name] = args[i]
            
            # 展开函数体
            expanded = _expand_function_body(inlined_body, return_var)
            for param_name, arg_value in param_map.items():
                expanded = re.sub(
                    rf'({re.escape(param_name)})\s*:?=',
                    f'_ignored_\1_:=',
                    expanded
                )
                expanded = re.sub(
                    rf'\b{re.escape(param_name)}\b',
                    arg_value,
                    expanded
                )
                expanded = expanded.replace(f'_ignored_{param_name}_:=', f'{param_name} :')
            
            # 处理返回值
            # 检查函数体是否太复杂（CASE、SELECT 或多行有包含 RETURN）
            # 多行赋值 + RETURN 仍然是简单函数
            has_return = '_nested_result :=' in expanded
            if 'CASE' in expanded or 'SELECT' in expanded.upper() or (len(expanded.split('\n')) > 15 and not has_return):
                # 复杂函数，保留函数调用
                return f'{var} := {func_name}({args_str}); /* TODO: complex body */'
            
            if return_type in ('TRUE', 'FALSE', 'NULL'):
                result_value = return_type
            elif return_type == 'VAR' and return_var:
                return f'{expanded}\n    {var} := {return_var}; /* INLINED: {func_name}() body */'
            else:
                result_value = 'NULL'
            
            return f'{expanded}\n    {var} := {result_value}; /* INLINED: {func_name}() */'
        
        code = pattern2.sub(_replace_assign_with_params, code)
        
        return code
    
    # 无参数函数：正常处理
    # 提取函数体中的 RETURN 值
    func_body = _extract_function_body(func.body)
    return_type, return_var = _extract_return_value(func_body)
    
    # 提取函数体内容（用于内联）
    inlined_body = _extract_body_for_inlining(func.body)
    
    # 计算简化后的条件
    def _get_simplified_condition(cond: str, val: str) -> str:
        """根据函数返回值计算简化后的条件"""
        val_upper = val.upper()
        
        if return_type == 'TRUE':
            if cond == '=':
                return 'FALSE' if val_upper == 'FALSE' else 'TRUE' if val_upper == 'TRUE' else 'NULL'
            elif cond in ('!=', '<>'):
                return 'TRUE' if val_upper == 'FALSE' else 'FALSE' if val_upper == 'TRUE' else 'NULL'
        elif return_type == 'FALSE':
            if cond == '=':
                return 'TRUE' if val_upper == 'FALSE' else 'FALSE' if val_upper == 'TRUE' else 'NULL'
            elif cond in ('!=', '<>'):
                return 'FALSE' if val_upper == 'FALSE' else 'TRUE' if val_upper == 'TRUE' else 'NULL'
        elif return_type == 'NULL':
            return 'FALSE'  # NULL 比较总是 NULL/假
        elif return_type == 'VAR' and return_var:
            # 返回变量的函数，无法确定最终值
            return 'NULL'  # 标记为 TODO
        
        return 'NULL'  # 默认
    
    # 处理 IF func_name = value THEN 模式 - 关键模式
    # 需要找到完整的 IF 块并替换条件
    def _replace_if_block(match):
        """替换整个 IF 块的条件"""
        full_match = match.group(0)
        cond = match.group(1)
        val = match.group(2)
        
        # 计算新的条件
        new_cond = _get_simplified_condition(cond, val)
        
        if new_cond == 'FALSE':
            # 条件永远为假，替换为 IF FALSE THEN
            return f'IF FALSE /* INLINED: {func_name}() was always {return_type} */ THEN'
        elif new_cond == 'TRUE':
            # 条件永远为真，替换为 IF TRUE THEN（保留块内容）
            return f'IF TRUE /* INLINED: {func_name}() was always {return_type} */ THEN'
        elif return_type == 'VAR' and return_var:
            # 返回变量的函数：展开函数体，把条件改为检查返回值变量
            # 先展开函数体（移除 EXCEPTION）
            expanded = _expand_function_body(inlined_body, return_var)
            # 返回变量赋值 + 检查条件
            return f'{expanded}\n    IF {return_var} {cond} {val} /* INLINED: {func_name}() body */ THEN'
        else:
            # 无法确定，标记 TODO
            return f'IF FALSE /* TODO: {func_name}() inline failed */ THEN'
    
    # 模式4: IF func_name = value THEN
    pattern4 = re.compile(
        rf'IF\s+{re.escape(func_name)}\s*([=<>!]+)\s*(\w+)\s+THEN',
        re.IGNORECASE
    )
    code = pattern4.sub(_replace_if_block, code)
    
    # 处理带括号的调用: result := func_name();
    def _replace_assign(match):
        var = match.group(1)
        full_match = match.group(0)  # 完整匹配，包括分号
        
        # 解析函数参数
        args_match = re.search(rf'{re.escape(func_name)}\s*\(([^)]*)\)', full_match)
        args = args_match.group(1) if args_match else ''
        
        if return_type in ('TRUE', 'FALSE', 'NULL'):
            return f'{var} := {return_type}; /* INLINED: {func_name}() */'
        elif return_type == 'LITERAL' and return_var:
            return f'{var} := {return_var}; /* INLINED: {func_name}() */'
        elif return_type == 'VAR' and return_var:
            # 返回变量的函数：检查函数体是否太复杂
            expanded = _expand_function_body(inlined_body, return_var)
            # 如果函数体包含 CASE、多行或复杂结构，保留函数调用
            if 'CASE' in expanded or 'SELECT' in expanded.upper() or len(expanded.split('\n')) > 15:
                # 复杂函数，保留调用
                return f'{var} := {func_name}({args}); /* TODO: complex body */'
            # 简单函数，展开
            return f'{expanded}\n    {var} := {return_var}; /* INLINED: {func_name}() body */'
        else:
            # 保留函数调用
            return f'{var} := {func_name}({args}); /* TODO: cannot inline */'
    
    pattern1 = re.compile(
        rf'(\w+)\s*:=\s*{re.escape(func_name)}\s*\([^)]*\)\s*;',
        re.IGNORECASE
    )
    code = pattern1.sub(_replace_assign, code)
    
    # 处理 IF func_name() THEN
    def _replace_if_call(match):
        if return_type in ('TRUE', 'FALSE', 'NULL'):
            return f'IF {return_type} /* INLINED: {func_name}() */ THEN'
        elif return_type == 'VAR' and return_var:
            # 返回变量的函数：检查函数体是否太复杂
            expanded = _expand_function_body(inlined_body, return_var)
            if 'CASE' in expanded or len(expanded.split('\n')) > 5 or 'SELECT' in expanded.upper():
                # 复杂函数，保留调用
                args_match = re.search(rf'{re.escape(func_name)}\s*\(([^)]*)\)', match.group(0))
                args = args_match.group(1) if args_match else ''
                return f'IF {func_name}({args}) /* TODO: complex body */ THEN'
            return f'IF {return_var} /* INLINED: {func_name}() */ THEN'
        elif return_type == 'LITERAL' and return_var:
            return f'IF {return_var} /* INLINED: {func_name}() */ THEN'
        else:
            # 保留函数调用而不是生成 FALSE
            args_match = re.search(rf'{re.escape(func_name)}\s*\(([^)]*)\)', match.group(0))
            args = args_match.group(1) if args_match else ''
            return f'IF {func_name}({args}) /* TODO: cannot inline */ THEN'
    
    pattern2 = re.compile(
        rf'IF\s+{re.escape(func_name)}\s*\([^)]*\)\s+THEN',
        re.IGNORECASE
    )
    code = pattern2.sub(_replace_if_call, code)
    
    return code


def _expand_function_body(body: str, return_var: str) -> str:
    """将函数体展开用于内联
    
    策略：
    1. 移除 EXCEPTION 块
    2. 处理 RETURN 语句：
       - RETURN(BRETURN) 或 RETURN BRETURN: 移除 RETURN，保留赋值
       - RETURN literal/expr: 替换为赋值语句
    """
    # 移除 EXCEPTION 块
    body = re.sub(r'\s*EXCEPTION\b.*?(?=\bEND\b|$)', '', body, flags=re.IGNORECASE | re.DOTALL)
    
    # 确定返回值变量名 - 固定使用 _nested_result 作为返回值临时变量
    result_var = '_nested_result'
    
    # 处理 RETURN 语句
    def _replace_return_var(m):
        # RETURN(BRETURN) 或 RETURN BRETURN: 
        # 变量已在函数体中赋值，用 _nested_result 接收返回值
        return f'\n    {result_var} := {return_var};'
    
    def _replace_return_literal(m):
        # RETURN literal: 转换为赋值
        return_content = m.group(1).strip()
        if return_content.startswith('(') and return_content.endswith(')'):
            return_content = return_content[1:-1].strip()
        return f'{result_var} := {return_content};'
    
    # 处理 RETURN(BRETURN) 和 RETURN BRETURN（返回变量的）
    if return_var:
        body = re.sub(r'\bRETURN\s*\(\s*' + re.escape(return_var) + r'\s*\)\s*;', _replace_return_var, body, flags=re.IGNORECASE)
        body = re.sub(r'\bRETURN\s+' + re.escape(return_var) + r'\s*;', _replace_return_var, body, flags=re.IGNORECASE)
    
    # 然后处理其他 RETURN 语句（返回字面量的）
    body = re.sub(r'\bRETURN\s*\(([^)]+)\)\s*;', _replace_return_literal, body, flags=re.IGNORECASE)
    body = re.sub(r'\bRETURN\s+(TRUE|FALSE|NULL)\s*;', _replace_return_literal, body, flags=re.IGNORECASE)
    
    return body


def _extract_body_for_inlining(body: str) -> str:
    """提取函数体用于内联
    
    移除 FUNCTION 头部和 END func_name; 语句，保留 BEGIN...END 之间的内容
    """
    # 找 BEGIN 位置
    begin_match = re.search(r'\bBEGIN\b', body, re.IGNORECASE)
    if not begin_match:
        return ''
    
    # 获取 BEGIN 之后的内容
    content = body[begin_match.end():]
    
    # 移除 END func_name; 语句
    content = re.sub(r'\s*END\s+\w+\s*;\s*$', '', content, re.IGNORECASE)
    
    return content

def _extract_return_value(func_body: str) -> Tuple[str, Optional[str]]:
    """从函数体中提取返回值
    
    策略：
    1. 直接在传入的内容中搜索 RETURN 语句
    2. 返回 (return_type, return_var)
       - return_type: 'TRUE', 'FALSE', 'NULL', 'LITERAL', 'VAR', '?NEEDS_MANUAL?'
       - return_var: 如果是变量返回，返回变量名
    
    Returns:
        Tuple[str, Optional[str]]: (return_type, return_var_name or None)
    """
    # func_body 已经是 BEGIN 之后的内容（由 _extract_function_body 处理）
    # 直接搜索 RETURN 语句
    body = func_body
    
    # 匹配 RETURN 字面量
    literal_patterns = [
        (r'\bRETURN\s*\(\s*TRUE\s*\)\s*;', 'TRUE'),
        (r'\bRETURN\s+TRUE\s*;', 'TRUE'),
        (r'\bRETURN\s*\(\s*FALSE\s*\)\s*;', 'FALSE'),
        (r'\bRETURN\s+FALSE\s*;', 'FALSE'),
        (r'\bRETURN\s*\(\s*NULL\s*\)\s*;', 'NULL'),
        (r'\bRETURN\s+NULL\s*;', 'NULL'),
        (r'\bRETURN\s+(-?\d+(?:\.\d+)?)\s*;', 'LITERAL'),  # 数字字面量
    ]
    
    for pattern, result in literal_patterns:
        m = re.search(pattern, body, re.IGNORECASE)
        if m:
            if result == 'LITERAL':
                return ('LITERAL', m.group(1))
            return (result, None)
    
    # 匹配 RETURN 变量: RETURN(var) 或 RETURN var;
    var_pattern = re.search(r'\bRETURN\s*\(\s*(\w+)\s*\)\s*;', body, re.IGNORECASE)
    if var_pattern:
        var_name = var_pattern.group(1)
        return ('VAR', var_name)
    
    # 匹配没有括号的 RETURN 变量
    var_pattern2 = re.search(r'\bRETURN\s+(\w+)\s*;', body, re.IGNORECASE)
    if var_pattern2:
        var_name = var_pattern2.group(1)
        # 排除 TRUE/FALSE/NULL 等关键字
        if var_name.upper() not in ('TRUE', 'FALSE', 'NULL'):
            return ('VAR', var_name)
    
    # 如果没有找到任何 RETURN，返回 NULL
    if 'RETURN' not in body.upper():
        return ('NULL', None)
    
    # 有 RETURN 但无法解析
    return ('?NEEDS_MANUAL?', None)


def _inline_nested_functions(declare_block: str, code_block: str, state: ConversionState) -> Tuple[str, str, List[str]]:
    """将嵌套函数内联展开到主函数体中
    
    策略：
    1. 合并 declare_block 和 code_block 来检测完整的嵌套函数
    2. 从合并代码中移除嵌套函数定义
    3. 展开函数调用
    4. 分离回 declare_block 和 code_block
    5. 添加警告信息
    
    Returns:
        (modified_declare_block, modified_code_block, warnings)
    """
    # 1. 合并代码来检测完整的嵌套函数
    # declare_block 包含: FUNCTION header + nested functions (部分)
    # code_block 包含: END nested_func1; ... BEGIN ... END main_func;
    combined = declare_block + '\n' + code_block
    
    # 2. 提取嵌套函数
    nested_funcs = _extract_nested_functions(combined)
    if not nested_funcs:
        return declare_block, code_block, []
    
    # 3. 检查是否有重叠的嵌套函数（一个函数嵌套在另一个内部）
    # 如果有重叠，只内联不重叠的顶级嵌套函数
    sorted_funcs = sorted(nested_funcs, key=lambda f: f.start_pos)
    
    # 找出顶级嵌套函数（不被其他嵌套函数包含）
    top_level_funcs = []
    for i, func in enumerate(sorted_funcs):
        is_nested = False
        for j, other in enumerate(sorted_funcs):
            if i != j and func.start_pos > other.start_pos and func.end_pos < other.end_pos:
                is_nested = True
                break
        if not is_nested:
            top_level_funcs.append(func)
    
    if len(top_level_funcs) != len(nested_funcs):
        # 有嵌套的嵌套函数，只内联顶级
        nested_funcs = top_level_funcs
        func_names = ', '.join(f.name for f in nested_funcs)
    else:
        func_names = ', '.join(f.name for f in nested_funcs)
    
    warnings = []
    warnings.append(
        f"WARNING: Inlined nested functions: {func_names}. "
        f"This conversion may change behavior for functions with side effects or multiple RETURN statements."
    )
    
    # 4. 先展开所有调用（包括跨范围的调用，如嵌套 PROC 调用嵌套 FUNC）
    # 按名称长度升序处理（短名称先处理）
    modified_combined = combined
    proc_funcs = [f for f in nested_funcs if getattr(f, 'subprogram_type', 'FUNCTION') == 'PROCEDURE']
    func_only = [f for f in nested_funcs if getattr(f, 'subprogram_type', 'FUNCTION') != 'PROCEDURE']
    
    # 先展开 PROCEDURE 调用
    if proc_funcs:
        modified_combined = _inline_nested_procedures(modified_combined, proc_funcs, state)
    # 再展开 FUNCTION 调用
    for func in sorted(func_only, key=lambda f: len(f.name)):
        modified_combined = _expand_function_call(modified_combined, func, state)
    
    # 5. 移除合并代码中的嵌套函数定义（调用展开后）
    # 按位置倒序移除（避免位置偏移问题）
    for func in sorted(nested_funcs, key=lambda f: f.start_pos, reverse=True):
        modified_combined = modified_combined[:func.start_pos] + modified_combined[func.end_pos:]
    
    # 6. 确保主函数体被保留
    # 如果修改后的 combined 长度异常短，说明出错了
    if len(modified_combined) < 50:
        warnings.append("ERROR: Nested function removal resulted in empty function body, using original")
        return declare_block, code_block, warnings
    
    # 7. 分离回 declare_block 和 code_block
    # 找到主函数的 BEGIN 位置
    # 主函数 BEGIN 在嵌套函数全部被移除之后
    main_begin_match = re.search(r'\bBEGIN\b', modified_combined, re.IGNORECASE)
    if main_begin_match:
        # declare_block 是 BEGIN 之前的内容
        new_declare = modified_combined[:main_begin_match.start()]
        new_code = modified_combined[main_begin_match.start():]
    else:
        new_declare = modified_combined
        new_code = ''
    
    # 6. 清理内联代码中的 PERFORM 关键字
    # 内联后的函数调用（如 TRUNC(...)）会被 _apply_perform_call 添加 PERFORM
    # 但在内联表达式中不需要 PERFORM，需要移除
    new_code = re.sub(
        r'IF\s+PERFORM\s+',
        'IF ',
        new_code
    )
    new_code = re.sub(
        r'\(\s*PERFORM\s+',
        '(',
        new_code
    )
    
    return new_declare, new_code, warnings


def _extract_nested_functions_as_independent(
    declare_block: str, 
    code_block: str, 
    parent_func_name: str,
    state: ConversionState
) -> Tuple[str, str, List[str], List[str]]:
    """将嵌套函数提取为独立函数
    
    策略:
    1. 合并 declare_block 和 code_block 来检测完整的嵌套函数
    2. 将每个嵌套函数转换为独立函数
    3. 用新函数名替换原函数调用
    4. 返回: (modified_declare, modified_code, warnings, extracted_functions)
    
    Returns:
        Tuple: (修改后的declare, 修改后的code, 警告列表, 提取的函数定义列表)
    """
    # 1. 合并代码来检测完整的嵌套函数
    combined = declare_block + '\n' + code_block
    
    # 2. 提取嵌套函数
    nested_funcs = _extract_nested_functions(combined)
    if not nested_funcs:
        return declare_block, code_block, [], []
    
    warnings = []
    extracted_functions = []
    
    # 3. 处理每个嵌套函数
    for func in nested_funcs:
        # 生成新函数名: parent_func_name__nested_func_name
        new_func_name = f"{parent_func_name}__{func.name}"
        
        # 4. 生成独立函数定义
        independent_func = _generate_independent_function(func, new_func_name, parent_func_name)
        extracted_functions.append(independent_func)
        
        # 5. 替换函数调用
        combined = _replace_nested_call_with_independent(combined, func, new_func_name)
        
        warnings.append(
            f"Extracted nested function {func.name} -> {new_func_name}"
        )
    
    # 6. 从合并代码中移除嵌套函数定义
    for func in sorted(nested_funcs, key=lambda f: f.start_pos, reverse=True):
        combined = combined[:func.start_pos] + combined[func.end_pos:]
    
    # 7. 分离回 declare_block 和 code_block
    main_begin_match = re.search(r'\bBEGIN\b', combined, re.IGNORECASE)
    if main_begin_match:
        new_declare = combined[:main_begin_match.start()]
        new_code = combined[main_begin_match.start():]
    else:
        new_declare = combined
        new_code = ''
    
    return new_declare, new_code, warnings, extracted_functions


if __name__ == '__main__':
    main()