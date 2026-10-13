"""
Level 2: 语法与结构验证

1. 用 pglast（PG 官方语法解析器）逐语句解析
2. 对 plpgsql 块做 BEGIN/END、IF/END IF 配对粗筛

重要限制（报告中会明确标注）：
pglast 只解析 SQL 外层语法。`CREATE FUNCTION ... $$ ... $$ LANGUAGE plpgsql` 中，
函数体在 pglast 眼里只是一个字符串常量，**plpgsql 体内的语法错误 pglast 查不出来**。
体内错误只有在 Level 3 真正安装到 PG 时才会暴露。
因此 Level 2 通过 ≠ 代码可运行，Level 3 才是权威判据。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import Issue
from .sqlsplit import count_blocks, split_statements


def check_statements(code: str) -> List[Dict[str, Any]]:
    """切分语句并返回每条语句的元信息"""
    stmts = split_statements(code)
    return [{"index": i, "text": s} for i, s in enumerate(stmts)]


def check_pglast(code: str) -> Dict[str, Any]:
    """
    用 pglast 逐语句解析

    Returns:
        {
          "available": bool,
          "version": str,
          "total": int,
          "passed": int,
          "failed": int,
          "errors": [{"index": int, "head": str, "error": str}],
          "unparsed": int,        # pglast 不可用时为语句总数
          "error": str,           # 不可用原因
        }
    """
    statements = check_statements(code)
    result: Dict[str, Any] = {
        "available": False,
        "version": "",
        "total": len(statements),
        "passed": 0,
        "failed": 0,
        "errors": [],
        "unparsed": len(statements),
    }

    try:
        import pglast
    except ImportError:
        result["error"] = "pglast 未安装，Level 2 语法解析跳过"
        return result

    result["available"] = True
    try:
        result["version"] = getattr(pglast, "__version__", "")
    except Exception:
        result["version"] = ""

    for st in statements:
        try:
            pglast.parse_sql(st["text"])
            result["passed"] += 1
        except Exception as e:                       # pglast 抛 ParseError
            result["failed"] += 1
            head = " ".join(st["text"].split())[:100]
            result["errors"].append({
                "index": st["index"],
                "head": head,
                "error": str(e).split("\n")[0][:200],
            })

    result["unparsed"] = 0
    return result


def check_structure(code: str) -> List[Issue]:
    """
    plpgsql 块结构配对粗筛

    只检查明显不配对的情况，作为 pglast 覆盖不到体内语法的补充信号。
    注意：END 同时用于 END IF/END LOOP，所以这里只报"END 明显少于 BEGIN"的情况，
    避免把正常的 IF/LOOP 嵌套误判为错误。
    """
    issues: List[Issue] = []
    for st in check_statements(code):
        begin, end, ifs, endifs = count_blocks(st["text"])
        head = " ".join(st["text"].split())[:80]
        if begin > end:
            issues.append(Issue(
                code="block_mismatch",
                name=f"BEGIN({begin}) 多于 END({end})",
                severity="critical", count=begin - end, samples=[head],
            ))
        if ifs != endifs and ifs > endifs:
            issues.append(Issue(
                code="block_mismatch",
                name=f"IF({ifs}) 多于 END IF({endifs})",
                severity="major", count=ifs - endifs, samples=[head],
            ))
    return issues


def score_syntax(pglast_result: Dict[str, Any], structure: List[Issue]) -> float:
    """
    评分（0-100）

    - pglast 不可用时不做扣分（由 framework 决定 skip 或降级）
    - 解析失败率 * 80 + 结构问题扣分
    """
    score = 100.0
    if pglast_result.get("available"):
        total = pglast_result.get("total", 0)
        failed = pglast_result.get("failed", 0)
        if total:
            score -= 80.0 * failed / total
    for i in structure:
        score -= i.count * (12.0 if i.severity == "critical" else 5.0)
    return round(max(0.0, score), 1)
