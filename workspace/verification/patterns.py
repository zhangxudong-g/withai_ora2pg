"""
静态检测规则表

原来规则分散在 test_reports/.../config.py 和 verification/level1_static.py 两处，
且两处重复且不一致（例如 config.py 把 `SYSDATE` 列为残留，level1 又列一次）。
这里合并为唯一一份，并补上遗漏项。

规则格式: (正则, 规则名, 严重级别)
严重级别: critical / major / minor / info
"""
from __future__ import annotations

import re
from typing import List, Pattern, Tuple

Rule = Tuple[Pattern, str, str]


def _rules(rows: List[Tuple[str, str, str]], flags: int = re.IGNORECASE) -> List[Rule]:
    return [(re.compile(p, flags), name, sev) for p, name, sev in rows]


# ---------------------------------------------------------------------------
# 逻辑错误：转换后仍然存在就说明转换破坏了语义（最高优先级）
# ---------------------------------------------------------------------------
LOGIC_ERRORS: List[Rule] = _rules([
    # `IF NULL THEN` 是嵌套子程序内联失败后遗留的典型残骸
    (r'\bIF\s+NULL\b', 'IF NULL 条件（恒假，通常是内联失败残骸）', 'critical'),
    (r'=\s*NULL\s+THEN', '= NULL 比较（恒不成立）', 'critical'),
    (r':=\s*NULL\s*;', '赋值 NULL（空操作）', 'major'),
    # 在剥离注释后的代码上匹配：恒真分支通常是占位替换的残留
    (r'\bIF\s+TRUE\s+THEN', 'IF TRUE 恒真分支（通常是占位符残留）', 'critical'),
    (r'\bIF\s+\?\s*NEEDS_MANUAL', 'IF 条件为 NEEDS_MANUAL 占位符', 'critical'),
    # Oracle 游标属性在 PG 不存在，漏转会导致永远走 NOT FOUND 分支
    (r'%\s*NOTFOUND\b', '%NOTFOUND 游标属性（PG 无此语法）', 'major'),
    (r'%\s*FOUND\b', '%FOUND 游标属性（PG 无此语法）', 'major'),
    (r'%\s*ISOPEN\b', '%ISOPEN 游标属性（PG 无此语法）', 'minor'),
])

# ---------------------------------------------------------------------------
# 占位符：转换器未能处理而留下的标记
# ---------------------------------------------------------------------------
PLACEHOLDERS: List[Rule] = _rules([
    (r'/\*\s*TODO\s*:', 'TODO 标记', 'major'),
    (r'/\*\s*INLINED\s*:', 'INLINED 标记', 'minor'),
    (r'/\*\s*BEGIN INLINED\s*:', 'BEGIN INLINED 标记', 'minor'),
    (r'/\*\s*END INLINED\s*:', 'END INLINED 标记', 'minor'),
    (r'\bNEEDS_MANUAL\b', 'NEEDS_MANUAL 占位符', 'major'),
    (r'\?NEEDS_MANUAL\?', 'NEEDS_MANUAL 占位符', 'major'),
])

# ---------------------------------------------------------------------------
# 无效语法：PG 明确不接受
# ---------------------------------------------------------------------------
INVALID_SYNTAX: List[Rule] = _rules([
    (r'\bCREATE\s+(OR\s+REPLACE\s+)?PACKAGE\s+BODY\b', 'CREATE PACKAGE BODY（PG 无包）', 'critical'),
    (r'\bCREATE\s+(OR\s+REPLACE\s+)?PACKAGE\b(?!\s+BODY)', 'CREATE PACKAGE（PG 无包）', 'critical'),
    (r'\bRETURN\s+\w+\s+IS\b(?!\s*(?:NOT\b|NULL\b|\$))', 'Oracle 的 RETURN <type> IS 写法', 'critical'),
    (r'%\s*ROWTYPE\b', '%ROWTYPE（PG 无此语法）', 'major'),
    (r'%\s*TYPE\b', '%TYPE（PG 无此语法）', 'major'),
    (r'\bIS\s+(NULL|NOT\s+NULL)\s*;', 'Oracle 列级 IS [NOT] NULL 约束', 'major'),
])

# ---------------------------------------------------------------------------
# Oracle 残留：函数/类型/伪列
# 注意：NVL/TO_DATE 在 PG 是合法的自定义函数名，只在未映射时才是残留，
#      因此 severity=minor，真正的语义问题交给 Level 3/4 判定。
# ---------------------------------------------------------------------------
ORACLE_RESIDUALS: List[Rule] = _rules([
    (r'\bDBMS_OUTPUT\b', 'DBMS_OUTPUT', 'major'),
    (r'\bSQLCODE\b', 'SQLCODE（PG 无此异常码）', 'major'),
    (r'\bSQLERRM\b', 'SQLERRM（PG 无此异常信息）', 'major'),
    (r'\bUTL_FILE\b', 'UTL_FILE', 'major'),
    (r'\bVARCHAR2\b', 'VARCHAR2 类型', 'minor'),
    (r'\bNVARCHAR2\b', 'NVARCHAR2 类型', 'minor'),
    (r'\bNUMBER\b', 'NUMBER 类型', 'minor'),
    (r'\bPLS_INTEGER\b', 'PLS_INTEGER 类型', 'minor'),
    (r'\bBINARY_INTEGER\b', 'BINARY_INTEGER 类型', 'minor'),
    (r'\bRAWTOHEX\s*\(', 'RAWTOHEX 函数', 'minor'),
    (r'\bHEXTORAW\s*\(', 'HEXTORAW 函数', 'minor'),
    (r'\bNVL\s*\(', 'NVL 函数（PG 用 COALESCE）', 'minor'),
    (r'\bDECODE\s*\(', 'DECODE 函数（PG 用 CASE）', 'minor'),
    (r'\bSYSDATE\b', 'SYSDATE（PG 用 CURRENT_DATE）', 'minor'),
    (r'\bSYSTIMESTAMP\b', 'SYSTIMESTAMP（PG 用 CURRENT_TIMESTAMP）', 'minor'),
    (r'\bADD_MONTHS\s*\(', 'ADD_MONTHS 函数', 'minor'),
    (r'\bLAST_DAY\s*\(', 'LAST_DAY 函数', 'minor'),
    (r'\bCONNECT\s+BY\b', 'CONNECT BY 层次查询（PG 用递归 CTE）', 'major'),
    (r'\bROWNUM\b', 'ROWNUM 伪列', 'major'),
    (r'\(\+\)', '(+) 外连接（PG 用 LEFT JOIN）', 'major'),
    (r':=NEW\b', 'NEW 触发器伪记录写法错误', 'major'),
    (r':=OLD\b', 'OLD 触发器伪记录写法错误', 'major'),
    (r'\bFORALL\b', 'FORALL 批量绑定（PG 无此语法）', 'major'),
    (r'\bBULK\s+COLLECT\b', 'BULK COLLECT（PG 无此语法）', 'major'),
    (r'\bPIPELINED\b', 'PIPELINED（PG 用 RETURNS SETOF）', 'minor'),
    (r'\bPRAGMA\s+AUTONOMOUS_TRANSACTION\b', '自治事务（PG 无等价物）', 'major'),
    (r'\bSYS_REFCURSOR\b', 'SYS_REFCURSOR（PG 用 REFCURSOR）', 'minor'),
])

# 每类问题的扣分权重（按单个问题实例计）
SEVERITY_PENALTY = {
    "critical": 12.0,
    "major": 5.0,
    "minor": 1.5,
    "info": 0.5,
}

# 类别归并（便于报告分组）
RULE_GROUPS = {
    "logic_error": LOGIC_ERRORS,
    "placeholder": PLACEHOLDERS,
    "invalid_syntax": INVALID_SYNTAX,
    "oracle_residual": ORACLE_RESIDUALS,
}

# 注释内容不参与检测：转换器会保留 Oracle 原始注释，
# 注释里出现 NVL/VARCHAR2 属于误报。
COMMENT_PATTERNS = [
    re.compile(r'--[^\n]*'),
    re.compile(r'/\*.*?\*/', re.DOTALL),
]


def strip_comments(sql: str) -> str:
    """
    去掉注释，但保留行结构（用等长空白替换），使行号仍然对得上。
    检测残留时必须用这个结果，否则原始注释会造成大量误报。
    """
    def blank(m: re.Match) -> str:
        return re.sub(r'[^\n]', ' ', m.group(0))

    out = sql
    for pat in COMMENT_PATTERNS:
        out = pat.sub(blank, out)
    return out


def all_rules() -> List[Tuple[str, Rule]]:
    """返回 (类别, 规则) 列表"""
    rows: List[Tuple[str, Rule]] = []
    for group, rules in RULE_GROUPS.items():
        for r in rules:
            rows.append((group, r))
    return rows
