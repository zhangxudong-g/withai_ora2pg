"""P1-3: 残留扫描器（DoD-2 硬门禁）。

按 DoD-2 完整清单建模式表，独立于 ``patterns.py``（后者偏评分用，规则分散）；
本模块是**硬门禁**——命中即 fail，不再是扣分项。

设计原则：
- **覆盖 ``/* TODO`` 与 ``-- TODO`` 两种形态**（HANDOFF §1.3 实测：TODO=28，
  其中块注释 16 + 行注释 12，行注释全在 00_PACKAGE.sql）
- **不在注释里误报**（除 TODO 本身——TODO 就是注释，必须在原始代码上匹配）
- **输出结构化**：``ResidualMatch`` + ``ScanReport``，供 Gate 直接消费
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Pattern, Tuple

# ---------------------------------------------------------------------------
# DoD-2 规则表：(正则, 规则名, 严重级别, 是否在原始代码上匹配)
#   第 4 字段 raw=True 表示不剥离注释（TODO 本身就是注释标记）
#   第 4 字段 raw=False 表示先剥离注释再匹配（防 Oracle 原注释误报）
# ---------------------------------------------------------------------------
Rule = Tuple[Pattern, str, str, bool]


def _R(pat: str, name: str, sev: str, raw: bool = False) -> Rule:
    return (re.compile(pat, re.IGNORECASE), name, sev, raw)


DOD2_RULES: List[Rule] = [
    # --- 占位符 / 内联残骸（最高优先，命中即 fail） ---
    _R(r'/\*\s*TODO\s*:', 'TODO 块注释占位符 /* TODO:', 'major', raw=True),
    _R(r'--\s*TODO\s*:', 'TODO 行注释占位符 -- TODO:', 'major', raw=True),
    _R(r'%\s*SUBQUERY\s*\d+\s*%', '%SUBQUERY<n>% 未回填占位符（数据丢失）', 'critical'),
    _R(r':\s*IS\s+NULL\s*;\s*/\*\s*INLINED\s*\*/', 'INLINED 残骸 (x : IS NULL; /* INLINED */)', 'critical', raw=True),
    _R(r'/\*\s*INLINED\s*:', 'INLINED 块标记残留', 'major', raw=True),

    # --- Oracle 游标属性 / 异常（PG 不接受） ---
    _R(r'%\s*NOTFOUND\b', '%NOTFOUND 游标属性', 'major'),
    _R(r'%\s*FOUND\b', '%FOUND 游标属性', 'major'),
    _R(r'%\s*ISOPEN\b', '%ISOPEN 游标属性', 'major'),
    _R(r'%\s*ROWCOUNT\b', 'SQL%ROWCOUNT', 'major'),
    _R(r'%\s*TYPE\b', '%TYPE 表列类型引用', 'major'),
    _R(r'%\s*ROWTYPE\b', '%ROWTYPE 行类型引用', 'major'),
    _R(r'\bSQLCODE\b', 'SQLCODE 异常码', 'major'),
    _R(r'\bSQLERRM\b', 'SQLERRM 异常信息', 'major'),

    # --- Oracle 批量/管道/文件 I/O（PG 无原生等价） ---
    _R(r'\bBULK\s+COLLECT\b', 'BULK COLLECT 批量取数', 'major'),
    _R(r'\bFORALL\b', 'FORALL 批量绑定', 'major'),
    _R(r'\bPIPE\s+ROW\b', 'PIPE ROW 管道行（PG 用 RETURNS SETOF）', 'major'),
    _R(r'\bUTL_FILE\b', 'UTL_FILE 文件 I/O', 'major'),
    _R(r'\bEXECUTE\s+IMMEDIATE\b', 'EXECUTE IMMEDIATE 动态 SQL', 'major'),
    _R(r'\bPRAGMA\s+AUTONOMOUS_TRANSACTION\b', 'PRAGMA AUTONOMOUS_TRANSACTION 自治事务', 'major'),

    # --- Oracle 外连接 / 同义词 / DUAL ---
    _R(r'\(\s*\+\s*\)', '(+) Oracle 外连接', 'major'),
    _R(r'\bCREATE\s+(?:PUBLIC\s+)?SYNONYM\b', 'CREATE SYNONYM（PG 无同义词对象）', 'major'),
    _R(r'\bFROM\s+DUAL\b', 'FROM DUAL 伪表', 'major'),

    # --- 包（PG 无包） ---
    _R(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?PACKAGE\s+BODY\b', 'CREATE PACKAGE BODY（PG 无包）', 'critical'),
    _R(r'\bCREATE\s+(?:OR\s+REPLACE\s+)?PACKAGE\b(?!\s+BODY)', 'CREATE PACKAGE（PG 无包）', 'critical'),
]


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class ResidualMatch:
    filename: str
    line_no: int
    rule_name: str
    severity: str
    snippet: str


@dataclass
class ScanReport:
    files_scanned: int = 0
    total_hits: int = 0
    critical_count: int = 0
    major_count: int = 0
    minor_count: int = 0
    hits: List[ResidualMatch] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """硬门禁：critical + major = 0 才通过（DoD-2 要求 major=0）"""
        return self.critical_count == 0 and self.major_count == 0


# ---------------------------------------------------------------------------
# 注释剥离（与 patterns.strip_comments 同语义，但保留 TODO 行/块）
# ---------------------------------------------------------------------------
_COMMENT_LINE = re.compile(r'--[^\n]*')
_COMMENT_BLOCK = re.compile(r'/\*.*?\*/', re.DOTALL)


def _strip_comments_keep_structure(sql: str) -> str:
    """剥离注释但保留行结构（用等长空白替换，行号仍对得上）。

    注意：本函数会把 ``/* TODO: ... */`` 和 ``-- TODO: ...`` 也剥掉，
    所以 TODO 规则必须在原始代码上匹配（raw=True）。
    """
    def blank(m: re.Match) -> str:
        return re.sub(r'[^\n]', ' ', m.group(0))
    out = sql
    out = _COMMENT_BLOCK.sub(blank, out)
    out = _COMMENT_LINE.sub(blank, out)
    return out


def _line_no_of(sql: str, pos: int) -> int:
    return sql.count('\n', 0, pos) + 1


# ---------------------------------------------------------------------------
# 扫描入口
# ---------------------------------------------------------------------------
def scan_sql(sql: str, filename: str = "<memory>") -> List[ResidualMatch]:
    """对单段 SQL 文本扫描，返回命中列表（按行号排序）。"""
    clean = _strip_comments_keep_structure(sql)
    hits: List[ResidualMatch] = []
    for pat, name, sev, raw in DOD2_RULES:
        target = sql if raw else clean
        for m in pat.finditer(target):
            snippet = m.group(0).strip().replace('\n', ' ')[:120]
            hits.append(ResidualMatch(
                filename=filename,
                line_no=_line_no_of(target, m.start()),
                rule_name=name,
                severity=sev,
                snippet=snippet,
            ))
    hits.sort(key=lambda h: (h.line_no, h.rule_name))
    return hits


def scan_directory(root: Path) -> ScanReport:
    """递归扫描目录下所有 .sql 文件（排除驱动/报告）。"""
    root = Path(root)
    report = ScanReport()
    skip_names = {"00_CREATE_ALL_PG.sql"}
    skip_suffixes = ()  # 报告 .md/.json 不扫，按后缀白名单更稳
    for f in sorted(root.rglob("*.sql")):
        if f.name in skip_names:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        report.files_scanned += 1
        rel = str(f.relative_to(root)).replace('\\', '/')
        for h in scan_sql(text, filename=rel):
            report.hits.append(h)
            report.total_hits += 1
            if h.severity == "critical":
                report.critical_count += 1
            elif h.severity == "major":
                report.major_count += 1
            else:
                report.minor_count += 1
    return report
