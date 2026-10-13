"""
P4-2f: ANTLR-based Oracle PL/SQL chunker

Design:
- chunk_oracle_by_antlr(sql_src): parse Oracle PL/SQL with ANTLR,
  return a list of dicts describing each function/procedure body found.
- Each dict: {name, kind, start_line, stop_line, depth, text}
  depth=0: top-level (CREATE OR REPLACE FUNCTION/PROCEDURE block)
  depth=1+: nested inside another function body
- SHA256 caching in oracle_poc/output/.antlr_cache/ (created if absent).
- Fallback: if ANTLR unavailable or parse fails, fall back to
  workspace/agent/chunker.chunk_function_or_procedure (regex-based).

Interface:
    from workspace.parsers.chunker_ast import chunk_oracle_by_antlr

    chunks = chunk_oracle_by_antlr(oracle_sql_src)
    # -> [{'name': 'F_GET_TOTAL_SCORE', 'kind': 'FUNCTION',
    #      'start_line': 8, 'stop_line': 110,
    #      'depth': 0, 'text': '...CREATE...END...'}, ...]

Fallback behavior (never raises — always returns something):
    - ANTLR unavailable / parse error → returns regex chunks
    - Any other exception → returns regex chunks

The ANTLR grammar used is the community PlSql grammar (ANTLR 4.13.2).
Key AST nodes:
    Create_function_bodyContext  → top-level CREATE FUNCTION block (depth=0)
    Create_procedure_bodyContext  → top-level CREATE PROCEDURE block (depth=0)
    Function_bodyContext          → nested FUNCTION bodies (depth ≥ 1)
    Procedure_bodyContext         → nested PROCEDURE bodies (depth ≥ 1)
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# sys.path: put grammar/ ahead of antlr4-runtime to avoid wrong-version import
# ---------------------------------------------------------------------------
_PARSERS_DIR = Path(__file__).resolve().parent
_GRAMMAR_DIR = _PARSERS_DIR / "grammar"
if str(_GRAMMAR_DIR) not in sys.path:
    sys.path.insert(0, str(_GRAMMAR_DIR))

# ---------------------------------------------------------------------------
# Cache directory (relative to repo root — caller should set REPO_ROOT env)
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # workspace/parsers/ → repo root
_CACHE_DIR = _REPO_ROOT / "oracle_poc" / "output" / ".antlr_cache"


# ---------------------------------------------------------------------------
# ANTLR availability check (lazy — imported only when needed)
# ---------------------------------------------------------------------------
_ANTLR_ERROR: Optional[str] = None
_ANTLR_AVAILABLE: bool = False
_PlSqlParser: Optional[type] = None
_PlSqlLexer: Optional[type] = None
_antlr4_InputStream: Optional[type] = None
_antlr4_CommonTokenStream: Optional[type] = None

try:
    from antlr4 import InputStream as _antlr4_InputStream_
    from antlr4 import CommonTokenStream as _antlr4_CommonTokenStream_
    from PlSqlLexer import PlSqlLexer
    from PlSqlParser import PlSqlParser

    _ANTLR_AVAILABLE = True
    _antlr4_InputStream = _antlr4_InputStream_
    _antlr4_CommonTokenStream = _antlr4_CommonTokenStream_
    _PlSqlLexer = PlSqlLexer
    _PlSqlParser = PlSqlParser
except ImportError as e:  # pragma: no cover
    _ANTLR_ERROR = str(e)


# ---------------------------------------------------------------------------
# Fallback chunker (regex-based, from workspace/agent/chunker.py)
# ---------------------------------------------------------------------------

def _chunk_by_regex(code: str) -> List[Dict[str, Any]]:
    """Regex fallback: split Oracle SQL into function/procedure chunks.

    Returns the same dict shape as ANTLR-based chunking.
    """
    from dataclasses import dataclass

    @dataclass
    class _Chunk:
        index: int
        code: str
        object_name: str
        object_kind: str

    _HEADER_RE = re.compile(
        r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?"
        r"(?:EDITIONABLE\s+|NONEDITIONABLE\s+)?"
        r"(FUNCTION|PROCEDURE)\s+(\w+)",
        re.IGNORECASE | re.MULTILINE,
    )
    starts = list(_HEADER_RE.finditer(code.upper()))
    if not starts:
        return []

    chunks: List[Dict[str, Any]] = []
    for i, m in enumerate(starts):
        kind = m.group(1).upper()
        name = m.group(3 if m.lastindex >= 3 else 2).lower()
        start = m.start()
        end = starts[i + 1].start() if i + 1 < len(starts) else len(code)
        body = code[start:end].strip()
        lines = code[:start].count("\n") + 1
        stop_line = lines + body.count("\n")
        chunks.append({
            "name": name,
            "kind": kind,
            "start_line": lines,
            "stop_line": stop_line,
            "depth": 0,
            "text": body,
        })
    return chunks


# ---------------------------------------------------------------------------
# ANTLR parsing helpers
# ---------------------------------------------------------------------------

def _get_line(ctx: Any) -> int:
    start = getattr(ctx, "start", None)
    return start.line if start else 1


def _get_stop_line(ctx: Any) -> int:
    stop = getattr(ctx, "stop", None)
    return stop.line if stop else _get_line(ctx)


def _get_text(ctx: Any, source: str) -> str:
    """Extract text from token stream using ctx.start.stop index."""
    start = getattr(ctx, "start", None)
    stop = getattr(ctx, "stop", None)
    if start and stop and hasattr(start, "start") and hasattr(stop, "stop"):
        try:
            return source[start.start : stop.stop + 1]
        except Exception:
            pass
    return source


def _find_function_body_nodes(
    node: Any,
    source: str,
    depth: int,
    results: List[Dict[str, Any]],
) -> None:
    """Walk AST, collecting all function/procedure body nodes with relative depth.

    Depth tracking (nearest function ancestor):
        - Top-level CREATE FUNCTION/PROCEDURE blocks → depth 0
        - Nested FUNCTION/PROCEDURE directly inside top-level → depth 1
        - Nested FUNCTION/PROCEDURE inside another nested → depth 2

    We track "are we inside a function body?" explicitly.
    "is_inside" is True when the current traversal is within a function body
    (top-level or nested).  When we find a Function_bodyContext:
        - record at current depth (which is the depth of the nearest func ancestor)
        - recurse with is_inside=True (depth stays same for sibling nested bodies)
    """
    if node is None:
        return

    ctx_name = type(node).__name__
    is_create_func = ctx_name == "Create_function_bodyContext"
    is_create_proc = ctx_name == "Create_procedure_bodyContext"
    is_func_body = ctx_name == "Function_bodyContext"
    is_proc_body = ctx_name == "Procedure_bodyContext"

    is_top_level = is_create_func or is_create_proc
    is_nested_body = is_func_body or is_proc_body

    if is_top_level:
        # Record this CREATE FUNCTION/PROCEDURE block.
        #
        # When ANTLR parses a file, `Create_function_bodyContext` (grammar rule
        # 'create_function_body') is always a top-level unit — so depth=0.
        #
        # But when we prepend `CREATE OR REPLACE ` to a bare nested-function text
        # (e.g. `FUNCTION BUILD_SQL(...)...`) to make it parseable by ANTLR,
        # ANTLR wraps it in `Create_function_bodyContext` *instead* of
        # `Function_bodyContext`.  In that case `depth > 0` (we are already inside
        # a function scope), so we record at the current depth, not 0.
        #
        # Recording depth 0 in this second scenario would trick the caller into
        # thinking the nested function is top-level (causing it to be
        # re-lifted incorrectly).
        effective_depth = depth if depth > 0 else 0
        name_node = getattr(node, "function_name", None)
        if name_node is None:
            name_node = getattr(node, "procedure_name", None)
        name = _safe_name(name_node)
        kind = "FUNCTION" if is_create_func else "PROCEDURE"
        results.append({
            "name": name,
            "kind": kind,
            "start_line": _get_line(node),
            "stop_line": _get_stop_line(node),
            "depth": effective_depth,
            "text": _get_text(node, source),
        })
        # Entering a function scope: nested bodies inside this are depth+1
        _walk_children(node, source, depth=depth + 1, results=results)

    elif ctx_name == "Anonymous_blockContext":
        # An anonymous PL/SQL block (DECLARE...BEGIN...END or just BEGIN...END)
        # acts as a function scope for nested functions declared inside it.
        # Enter: depth+1 for declarations inside this block.
        _walk_children(node, source, depth=depth + 1, results=results)

    elif is_nested_body:
        # We are inside a function body (top-level or another nested).
        # Record at the given depth (depth = depth of the nearest function ancestor).
        name = _find_identifier_child(node) or "?"
        kind = "FUNCTION" if is_func_body else "PROCEDURE"
        results.append({
            "name": name,
            "kind": kind,
            "start_line": _get_line(node),
            "stop_line": _get_stop_line(node),
            "depth": depth,
            "text": _get_text(node, source),
        })
        # Recurse children: depth stays the same (siblings share same depth).
        # When we find another Function_bodyContext among children, it will be
        # recorded at the same depth (sibling) and its children at depth+1.
        _walk_children(node, source, depth=depth, results=results)

    else:
        # Non-function node: recurse at same depth
        _walk_children(node, source, depth=depth, results=results)


def _walk_children(
    node: Any,
    source: str,
    depth: int,
    results: List[Dict[str, Any]],
    max_depth: int = 20,
) -> None:
    """Walk all children of a node."""
    if depth > max_depth:
        return
    children = getattr(node, "children", None)
    if not children:
        return
    for child in children:
        _find_function_body_nodes(child, source, depth, results)


def _safe_name(name_node: Any) -> str:
    """Extract function/procedure name from identifier node.

    Handles three cases:
      1. name_node is a PlSqlParser context with getText() method
      2. name_node is a bound method of Create_function_bodyContext /
         Create_procedure_bodyContext (returns Function_nameContext)
      3. name_node is a list of IdentifierContext children (nested functions)
    """
    if name_node is None:
        return "?"
    # Case 2: bound method (function_name() / procedure_name() of Create_*_body)
    if callable(name_node):
        try:
            name_node = name_node()
        except Exception:
            return "?"
    # name_node is now a context
    if hasattr(name_node, "getText"):
        try:
            return name_node.getText().strip().lower()
        except Exception:
            pass
    return "?"


def _find_identifier_child(node: Any) -> Optional[str]:
    """Find an IdentifierContext descendant and return its text."""
    if node is None:
        return None
    name = type(node).__name__
    if "Identifier" in name:
        try:
            return node.getText().strip().lower()
        except Exception:
            pass
    for ch in getattr(node, "children", []) or []:
        result = _find_identifier_child(ch)
        if result:
            return result
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def chunk_oracle_by_antlr(
    sql_src: str,
    allow_errors: bool = False,
) -> List[Dict[str, Any]]:
    """Parse Oracle PL/SQL source with ANTLR, return list of function/procedure chunks.

    Each dict:
        name        (str): function/procedure name, lower-cased
        kind        (str): "FUNCTION" or "PROCEDURE"
        start_line  (int): 1-based line number of start
        stop_line   (int): 1-based line number of end
        depth       (int): 0 = top-level; ≥1 = nested inside another function
        text        (str): full source text of this chunk

    Always returns a list (never raises):
        - ANTLR unavailable / parse error → regex fallback
        - Empty input → empty list
    """
    if not sql_src or not sql_src.strip():
        return []

    # 1. Try cache (SHA256 keyed)
    cache_file = None
    if _CACHE_DIR.is_dir():
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        digest = hashlib.sha256(sql_src.encode("utf-8")).hexdigest()
        cache_file = _CACHE_DIR / f"{digest}.json"
        if cache_file.exists():
            try:
                cached = cache_file.read_text(encoding="utf-8")
                result = json.loads(cached)
                if isinstance(result, list):
                    return result
            except Exception:
                pass  # ignore cache corruption → re-parse

    # 2. Parse with ANTLR (if available)
    if _ANTLR_AVAILABLE:
        chunks = _parse_with_antlr(sql_src, allow_errors=allow_errors)
        if chunks:
            # Cache the result (only for strict/non-allow_errors calls)
            if cache_file and not allow_errors:
                try:
                    cache_file.write_text(
                        json.dumps(chunks, ensure_ascii=False, indent=1),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
            return chunks

    # 3. Fallback to regex
    return _chunk_by_regex(sql_src)


def _parse_with_antlr(
    sql_src: str,
    allow_errors: bool = False,
) -> List[Dict[str, Any]]:
    """Internal ANTLR parse, returns list of chunks or [] on failure.

    Args:
        sql_src: Oracle PL/SQL source text to parse.
        allow_errors: If True, return chunks even when there are parse errors
            (useful for DECLARE-wrapped nested-function text where the wrapper
            may cause minor parse artifacts).  If False (default), return [] on
            any parse error (strict mode — triggers fallback to regex).
    """
    if not _ANTLR_AVAILABLE:
        return []

    try:
        from antlr4.error.ErrorListener import ErrorListener

        class _CollectingListener(ErrorListener):
            def __init__(self):
                self.errors: List[str] = []

            def syntaxError(self, recognizer, offendingSymbol, line, column, msg, e):
                self.errors.append(f"line {line}:{column} {msg}")

        input_stream = _antlr4_InputStream(sql_src)
        lexer = _PlSqlLexer(input_stream)
        token_stream = _antlr4_CommonTokenStream(lexer)
        parser = _PlSqlParser(token_stream)

        collector = _CollectingListener()
        parser.removeErrorListeners()
        parser.addErrorListener(collector)

        tree = parser.sql_script()  # type: ignore[attr-defined]

        # Walk the tree collecting function/procedure bodies
        results: List[Dict[str, Any]] = []
        _find_function_body_nodes(tree, sql_src, depth=0, results=results)

        # Strict mode: return [] if there are any parse errors
        # (unless we found chunks — they may still be valid).
        if collector.errors and not results and not allow_errors:
            return []
        return results

    except Exception:
        return []


# ---------------------------------------------------------------------------
# Convenience: get nested functions only (depth ≥ 1)
# ---------------------------------------------------------------------------

def get_nested_functions(sql_src: str) -> List[Dict[str, Any]]:
    """Return only nested (depth ≥ 1) function/procedure chunks."""
    return [c for c in chunk_oracle_by_antlr(sql_src) if c["depth"] >= 1]


def get_top_level_functions(sql_src: str) -> List[Dict[str, Any]]:
    """Return only top-level (depth = 0) function/procedure chunks."""
    return [c for c in chunk_oracle_by_antlr(sql_src) if c["depth"] == 0]
