"""
P2-4: 序列/同义话语义治理

设计：
- 表同义词 (target 是表): 保持 VIEW 不变 (已工作)
- 序列同义词 (target 是序列): 当前转换器写成 `CREATE VIEW sm_x AS SELECT * FROM seq_y;`
  在 PG 中语法错误——序列不是表，`SELECT * FROM seq_y` 报错
- 本模块识别 target 是序列时，改写为:
  `CREATE OR REPLACE VIEW sm_x AS SELECT nextval('seq_y') AS next_value;`

MAXVALUE 策略开关（本期不改）:
- 已有 "MAXVALUE capped" 警告在引擎里
- 不在本期 P2-4 范围内（属业务决策项，留 P2-4 二期 / 业务方确认）

实现要点：
- identify_sequences(sql): 从 SEQUENCE 文件中提取序列名（大写归一）
- rewrite_sequence_synonym_views(sql, seqs): 改写 SYNONYM 文件中针对序列的 VIEW
"""

from __future__ import annotations

import re
from typing import List, Set, Tuple


# ---------------------------------------------------------------------------
# 1. identify_sequences: 从 SEQUENCE 文件识别序列名
# ---------------------------------------------------------------------------


# 匹配 CREATE [TEMPORARY|UNLOGGED] SEQUENCE [IF NOT EXISTS] name
_SEQUENCE_RE = re.compile(
    r"\bCREATE\s+(?:TEMPORARY\s+|UNLOGGED\s+)?SEQUENCE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)",
    re.IGNORECASE,
)


def identify_sequences(sql: str) -> Set[str]:
    """从 SEQUENCE 文件识别所有 CREATE SEQUENCE 名（大写归一）。

    忽略 -- 注释和 /* */ 注释中的 "CREATE SEQUENCE" 假匹配。
    """
    if not sql:
        return set()

    # 剥离注释（行注释和块注释）
    stripped: List[str] = []
    i = 0
    n = len(sql)
    while i < n:
        c = sql[i]
        if c == "-" and i + 1 < n and sql[i + 1] == "-":
            # 行注释
            while i < n and sql[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and sql[i + 1] == "*":
            # 块注释
            i += 2
            while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i += 2
            continue
        stripped.append(c)
        i += 1

    clean = "".join(stripped)
    return {m.group(1).upper() for m in _SEQUENCE_RE.finditer(clean)}


# ---------------------------------------------------------------------------
# 2. rewrite_sequence_synonym_views: 改写序列同义词 VIEW
# ---------------------------------------------------------------------------


# 匹配: CREATE OR REPLACE VIEW <name> AS SELECT * FROM <target>;
# 我们只关心 SELECT * FROM target; 这种形式（来自 convert_synonym 的产出）
_VIEW_SELECT_ALL_RE = re.compile(
    r"(?P<create>CREATE\s+OR\s+REPLACE\s+VIEW\s+(?P<view_name>\w+)\s+AS\s+SELECT\s+\*\s+FROM\s+)(?P<target>\w+)(?P<rest>\s*;)",
    re.IGNORECASE,
)


def rewrite_sequence_synonym_views(sql: str, sequences: Set[str]) -> Tuple[str, int]:
    """对 SYNONYM 产物中的 VIEW 做序列改写。

    对每条 `CREATE OR REPLACE VIEW x AS SELECT * FROM y;`：
    - 若 y 是序列（在 sequences 集合中）→ 改写为 `SELECT nextval('y') AS next_value`
    - 否则保持原样

    Args:
        sql: SYNONYM 文件内容
        sequences: 已知的序列名集合（大写）

    Returns:
        (new_sql, n_substituted)
    """
    if not sql or not sequences:
        return sql, 0

    norm_seqs = {s.upper() for s in sequences}
    count = 0

    def _replace(m: re.Match) -> str:
        nonlocal count
        target = m.group("target")
        if target.upper() not in norm_seqs:
            return m.group(0)  # 保持
        # 改写为 nextval()
        count += 1
        # 用小写 target（PG 默认），加 next_value 列名
        return (
            f"CREATE OR REPLACE VIEW {m.group('view_name')} AS "
            f"SELECT nextval('{target.lower()}') AS next_value;"
        )

    new_sql = _VIEW_SELECT_ALL_RE.sub(_replace, sql)
    return new_sql, count
