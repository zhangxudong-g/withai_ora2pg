"""P4-3 批次 1：动态 SQL + DUAL 机械替换

纯函数，字符串/注释豁免（复用 exception_map 的剥离工具）。

规则：
- rewrite_from_dual:
    * NAME.NEXTVAL → nextval('NAME')
    * NAME.CURRVAL → currval('NAME')
    * FROM DUAL → 删除（PG SELECT 可不带 FROM）
- rewrite_execute_immediate:
    * EXECUTE IMMEDIATE → EXECUTE（PL/pgSQL 动态 SQL 关键字）
    * INTO / USING 子句原样保留（PG 语法兼容）
    * BULK COLLECT INTO 数组形态：本批只去 IMMEDIATE，BULK COLLECT 留批次 2

批次 2（BULK COLLECT/FORALL/PIPE ROW）与批次 3（UTL_FILE/INDEX BY）后续实现。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Tuple

# 复用 exception_map 的字符串/注释剥离（保持豁免行为一致）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rules.exception_map import (  # noqa: E402
    _strip_strings_and_comments,
    _restore_placeholders,
)


# ===========================================================================
# FROM DUAL + NEXTVAL/CURRVAL
# ===========================================================================

# NAME.NEXTVAL（NAME 是标识符，可能带 schema 前缀 A.B.NEXTVAL）
_NEXTVAL_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*)?)\s*\.\s*NEXTVAL\b",
    re.IGNORECASE,
)
_CURRVAL_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*)?)\s*\.\s*CURRVAL\b",
    re.IGNORECASE,
)
# FROM DUAL（允许前后空白；DUAL 后到语句边界）
_FROM_DUAL_RE = re.compile(
    r"\s+FROM\s+DUAL\b",
    re.IGNORECASE,
)


def rewrite_from_dual(sql: str) -> str:
    """NEXTVAL/CURRVAL 序列函数化 + 删除 FROM DUAL。"""
    stripped, restore = _strip_strings_and_comments(sql)

    # 1) NAME.NEXTVAL → nextval('NAME')（取最后一段作为序列名，schema 限定保留）
    def _next(m: re.Match) -> str:
        name = m.group(1)
        # 若带 schema 前缀（SCHEMA.SEQ），nextval 用 'SCHEMA.SEQ'
        return f"nextval('{name}')"

    stripped = _NEXTVAL_RE.sub(_next, stripped)
    stripped = _CURRVAL_RE.sub(lambda m: f"currval('{m.group(1)}')", stripped)

    # 2) 删除 FROM DUAL（PG SELECT 不带 FROM）
    stripped = _FROM_DUAL_RE.sub("", stripped)

    return _restore_placeholders(stripped, restore)


# ===========================================================================
# EXECUTE IMMEDIATE → EXECUTE
# ===========================================================================

# 仅替换关键字组合 EXECUTE IMMEDIATE（保留 EXECUTE 与后续 SQL 表达式）
_EXEC_IMM_RE = re.compile(r"\bEXECUTE\s+IMMEDIATE\b", re.IGNORECASE)


def rewrite_execute_immediate(sql: str) -> str:
    """EXECUTE IMMEDIATE → EXECUTE（PL/pgSQL 动态 SQL）。

    INTO/USING 子句原样保留。BULK COLLECT 关键字不在本批处理。
    """
    stripped, restore = _strip_strings_and_comments(sql)
    stripped = _EXEC_IMM_RE.sub("EXECUTE", stripped)
    return _restore_placeholders(stripped, restore)


# ===========================================================================
# 组合入口
# ===========================================================================
def rewrite_dynamic_sql_all(sql: str) -> str:
    """P4-3 批次 1 全部机械替换。"""
    sql = rewrite_from_dual(sql)
    sql = rewrite_execute_immediate(sql)
    return sql
