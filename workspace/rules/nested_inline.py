"""P4-4c 嵌套函数内联：将单 RETURN 的嵌套函数内联到调用处。

引擎 _lift_nested_subprograms 对二层嵌套覆盖不全。本规则处理简单情况：
嵌套函数体只有一条 RETURN 语句 → 删除函数定义，在调用处替换为返回表达式。

匹配模式：
    FUNCTION <name>(<params>) RETURN <type>
    IS
    BEGIN
        RETURN <expr>;
    END <name>;
    ...
    <call>: <var> := <name>(<args>);

替换为：
    <call>: <var> := <expr_with_params_substituted>;
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rules.exception_map import (  # noqa: E402
    _strip_strings_and_comments,
    _restore_placeholders,
)


# 匹配嵌套函数定义（单 RETURN）
# FUNCTION name(p1 type, p2 type) RETURN type
# IS
# BEGIN
#     RETURN expr;
# END name;
_NESTED_FUNC_RE = re.compile(
    r"(?P<indent>\s*)"
    r"FUNCTION\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)\s*"
    r"RETURN\s+(?P<rettype>\w+)\s+"
    r"(?:IS|AS)\s*\n"
    r"(?:\s*\w+\s+\w+[^\n]*?\n)*"  # optional variable declarations
    r"\s*BEGIN\s*\n"
    r"\s*RETURN\s+(?P<expr>[^;]+);\s*\n"
    r"\s*(?:END\s+(?P=name)\s*;|END\s*;)",
    re.IGNORECASE,
)


def _substitute_params(expr: str, params: str, args: str) -> str:
    """将表达式中的参数名替换为实参。"""
    param_names = []
    for p in params.split(","):
        p = p.strip()
        if p:
            # 提取参数名（第一个 token）
            m = re.match(r"(\w+)\s", p)
            if m:
                param_names.append(m.group(1))
            else:
                param_names.append(p.split()[0])
    
    arg_names = [a.strip() for a in args.split(",") if a.strip()]
    
    # 按长度降序替换（避免短名被长名的一部分替换）
    for pn, an in zip(param_names, arg_names):
        expr = re.sub(rf"\b{re.escape(pn)}\b", an, expr, flags=re.IGNORECASE)
    
    return expr


def rewrite_nested_inline(sql: str) -> str:
    """内联单 RETURN 的嵌套函数。"""
    stripped, restore = _strip_strings_and_comments(sql)
    
    # 迭代处理（内联一个后可能出现新的）
    for _ in range(10):
        m = _NESTED_FUNC_RE.search(stripped)
        if not m:
            break
        
        fname = m.group("name")
        params = m.group("params")
        expr = m.group("expr").strip()
        
        # 先删除函数定义
        stripped = stripped[:m.start()] + stripped[m.end():]
        
        # 然后在调用处替换：fname(arg1, arg2) → substituted expr
        # 调用模式: fname(...)  或 var := fname(...)
        call_re = re.compile(
            rf"\b{re.escape(fname)}\s*\(([^)]*)\)",
            re.IGNORECASE,
        )
        stripped = call_re.sub(
            lambda cm: _substitute_params(expr, params, cm.group(1)),
            stripped,
        )
    
    return _restore_placeholders(stripped, restore)


def apply_nested_inline(sql: str) -> str:
    """post-pass 入口。"""
    return rewrite_nested_inline(sql)
