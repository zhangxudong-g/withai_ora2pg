"""
P3-1: AST 适配器（grammar_adapter）

设计：
- parse_plsql(sql): 输入 PL/SQL 源代码，返回 ParseResult
- 统一异常处理: 解析失败不抛错，返回 degraded 结果（errors 非空）
- 降级策略: 上层调用方根据 errors 是否为空决定走 AST 路径还是回退
- 不抛 ANTLR 原生异常——全部 wrap 成 ParseResult

落点: workspace/parsers/grammar_adapter.py

依赖:
- antlr4-python3-runtime==4.13.2（与 grammar 严格同版本）
- workspace/parsers/grammar/ 下的 PlSqlLexer / PlSqlParser

注意：
- PL/SQL grammar 是社区版（ANTLR 4.13.2），对部分语法宽容度有限
- 解析失败时调用方应回退到"按文本规则处理"路径（如 P2-3 的正则方案）
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional


# ---------------------------------------------------------------------------
# 把 grammar/ 目录加入 sys.path（按需）
# ---------------------------------------------------------------------------

_PARSERS_DIR = Path(__file__).resolve().parent
_GRAMMAR_DIR = _PARSERS_DIR / "grammar"
if str(_GRAMMAR_DIR) not in sys.path:
    sys.path.insert(0, str(_GRAMMAR_DIR))

# 延迟导入——grammar 模块加载较重（6.9 MB 解析器）
_antlr_import_error: Optional[Exception] = None
try:
    from antlr4 import CommonTokenStream, InputStream  # noqa: E402
    from PlSqlLexer import PlSqlLexer  # noqa: E402
    from PlSqlParser import PlSqlParser  # noqa: E402
    _ANTLR_AVAILABLE = True
except ImportError as e:  # pragma: no cover - 环境异常路径
    _ANTLR_AVAILABLE = False
    _antlr_import_error = e


# ---------------------------------------------------------------------------
# ParseResult
# ---------------------------------------------------------------------------


@dataclass
class ParseResult:
    """解析结果统一封装。

    Attributes:
        ok: 是否解析成功（无错误）
        tree: 解析树根（antlr4 RuleContext），可能为 None（解析失败时）
        parser: PlSqlParser 实例（持有 parse tree），可能为 None
        errors: 错误信息列表（ok=True 时通常为空）
    """
    ok: bool
    tree: Optional[Any] = None
    parser: Optional[Any] = None
    errors: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def parse_plsql(sql: Optional[str]) -> ParseResult:
    """解析 PL/SQL 源代码，返回 ParseResult。

    永不抛错——所有异常 wrap 成 errors 字段。

    Args:
        sql: PL/SQL 源代码字符串（None 视为空输入）

    Returns:
        ParseResult 实例。ok=False 时调用方应走降级路径。
    """
    # 1. 输入校验
    if sql is None:
        return ParseResult(ok=False, errors=["input is None"])
    if not isinstance(sql, str):
        return ParseResult(ok=False, errors=[f"input must be str, got {type(sql).__name__}"])
    if not sql.strip():
        return ParseResult(ok=False, errors=["input is empty"])

    # 2. 环境检查
    if not _ANTLR_AVAILABLE:
        return ParseResult(
            ok=False,
            errors=[f"ANTLR runtime not available: {_antlr_import_error!r}"],
        )

    # 3. 实际解析（带异常 wrap）
    try:
        input_stream = InputStream(sql)
        lexer = PlSqlLexer(input_stream)
        token_stream = CommonTokenStream(lexer)
        parser = PlSqlParser(token_stream)

        # 移除默认 error handler 的输出（避免污染 stdout）
        # 用 parser.removeErrorListeners() + 我们自己的 listener
        from antlr4.error.ErrorListener import ErrorListener  # noqa: E402

        class _CollectingListener(ErrorListener):
            def __init__(self):
                self.errors: List[str] = []

            def syntaxError(self, recognizer, offendingSymbol, line, column, msg, e):  # noqa: N802
                self.errors.append(f"line {line}:{column} {msg}")

        collector = _CollectingListener()
        parser.removeErrorListeners()
        parser.addErrorListener(collector)

        # 解析根 rule: Sql_script（社区 grammar 根）
        tree = parser.sql_script()  # type: ignore[attr-defined]

        # 收集错误
        if collector.errors:
            return ParseResult(
                ok=False,
                tree=tree,
                parser=parser,
                errors=collector.errors,
            )

        return ParseResult(ok=True, tree=tree, parser=parser, errors=[])

    except Exception as e:  # pragma: no cover - 防御性
        return ParseResult(
            ok=False,
            errors=[f"parse exception: {type(e).__name__}: {e}"],
        )
