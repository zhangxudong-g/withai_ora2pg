"""P4-4c 阶段 5：OUT/INOUT 参数 + RETURNS 类型错配修复（PG 15 实测校准版）

PG 15.19 实测规则（见 commit 修复说明，勿凭记忆改动）：

1. 函数带 OUT/INOUT 参数时，**CREATE 时**就要求结果类型匹配：
   - 1 个 OUT/INOUT → ``RETURNS`` 必须是该参数的类型
     `ERROR: 因为OUT参数，函数的结果类型必须是numeric`
   - ≥2 个 OUT/INOUT → 必须是 ``record``
     `ERROR: 因为OUT参数，函数的结果类型必须是record`
   （``INOUT``/``OUT`` 写在参数名前后两种写法都要识别）

2. 类型对齐之后，带输出参数的函数**只能裸 RETURN**：
   `ERROR: 在带有输出参数的函数中RETURN不能有参数`
   即 ``RETURN TRUE;`` / ``RETURN o_x;`` 都要改成 ``RETURN;``

因此正确的修法是「改 RETURNS 类型 + 把函数体内 RETURN 表达式改成裸 RETURN」。

⚠️ 历史缺陷（v1 实测）：v1 用 ``re.sub`` 对**整份文件**做 ``RETURN TRUE/FALSE``
全局替换，把「最后一个被修复函数的 INOUT 变量名」灌注到同文件所有函数，
在 PACKAGE_BODY 里造出 6 处作用域外引用（``o_nPASS``），首调必炸。
本版改为**逐函数、按 body 区间**处理，修复范围不越界。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rules.exception_map import (  # noqa: E402
    _restore_placeholders,
    _strip_strings_and_comments,
)

# 允许「OUT/INOUT 在名前」和「在名后」两种写法
_PARAM_SPLIT = re.compile(r",(?![^()]*\))")
_OUT_PARAM = re.compile(
    r"^\s*(?:(INOUT|OUT)\s+(\w+)\s+([\w.]+(?:\s*\(\s*[\d,\s]+\s*\))?)"
    r"|(\w+)\s+(INOUT|OUT)\s+([\w.]+(?:\s*\(\s*[\d,\s]+\s*\))?))\s*$",
    re.IGNORECASE,
)
_FN_START = re.compile(r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+(\w+)\s*\(", re.IGNORECASE)
_RETURNS_RE = re.compile(r"RETURNS\s+([\w.]+)", re.IGNORECASE)
_DOLLAR = re.compile(r"\$\$")
# 集合返回 / 触发器函数：无输出参数，不在本规则范围
_SKIP_RETURNS = {"TRIGGER", "TABLE", "SETOF", "VOID", "SETOF%ROWTYPE"}
# 带输出参数时只允许裸 RETURN；RETURN QUERY / RETURN NEXT 属集合返回，不动
_RETURN_EXPR = re.compile(
    r"\bRETURN\s+(?!QUERY\b|NEXT\b)([^;]+);", re.IGNORECASE)


def _parse_out_params(params: str) -> list:
    """返回 [(name, type)]，type 统一大写。兼容 OUT/INOUT 前后两种写法。"""
    outs: list = []
    for part in _PARAM_SPLIT.split(params):
        if not part.strip():
            continue
        m = _OUT_PARAM.match(part)
        if not m:
            continue
        mode_first, name_first, type_first, name_last, _mode, type_last = m.groups()
        name = (name_first or name_last)
        typ = (type_first or type_last)
        if name and typ:
            outs.append((name, re.sub(r"\s+", "", typ).upper()))
    return outs


def _iter_functions(stripped: str):
    """产出 (name, params, ret_start, ret_end, ret_type, body_start, body_end)。"""
    for m in _FN_START.finditer(stripped):
        # 参数表括号配平
        i = m.end() - 1
        depth = 0
        j = i
        while j < len(stripped):
            ch = stripped[j]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        params = stripped[i + 1:j]
        rm = _RETURNS_RE.search(stripped, j, min(j + 300, len(stripped)))
        if not rm:
            continue
        d1 = _DOLLAR.search(stripped, j)
        if not d1:
            continue
        d2 = _DOLLAR.search(stripped, d1.end())
        if not d2:
            continue
        yield (m.group(1), params, rm.start(1), rm.end(1), rm.group(1).upper(),
               d1.end(), d2.start())


def _plan(stripped: str) -> tuple:
    """返回 (编辑列表, returns_fixed 计数, bare_return 计数, 函数明细)。"""
    edits: list = []
    returns_fixed = 0
    bare_returns = 0
    detail: list = []
    for name, params, rs, re_, ret, bs, be in _iter_functions(stripped):
        if ret in _SKIP_RETURNS:
            continue
        outs = _parse_out_params(params)
        if not outs:
            continue
        body = stripped[bs:be]
        if not _RETURN_EXPR.search(body):
            # 没有带表达式的 RETURN，仍可能需要改 RETURNS
            n_bare = 0
        else:
            n_bare = len(_RETURN_EXPR.findall(body))

        types = [t for _, t in outs]
        new_ret = types[0] if len(outs) == 1 else "RECORD"
        changed_ret = new_ret != ret
        if changed_ret:
            edits.append((rs, re_, new_ret))
            returns_fixed += 1
        if n_bare:
            new_body = _RETURN_EXPR.sub("RETURN;", body)
            edits.append((bs, be, new_body))
            bare_returns += n_bare
        if changed_ret or n_bare:
            detail.append({
                "function": name,
                "returns": ret,
                "returns_new": new_ret if changed_ret else None,
                "out_params": outs,
                "bare_return": n_bare,
            })
    return edits, returns_fixed, bare_returns, detail


def scan_inout_fixes(sql: str) -> dict:
    """只统计不改文本，供转换脚本打印与单测断言。"""
    stripped, _restore = _strip_strings_and_comments(sql)
    _edits, returns_fixed, bare_returns, detail = _plan(stripped)
    return {"returns_fixed": returns_fixed, "bare_return": bare_returns,
            "functions": detail}


def fix_inout_returns(sql: str) -> str:
    """逐函数修复 OUT/INOUT + RETURNS 错配，并把带表达式的 RETURN 改成裸 RETURN。"""
    stripped, restore = _strip_strings_and_comments(sql)
    edits, _returns_fixed, _bare_returns, _detail = _plan(stripped)
    if not edits:
        return _restore_placeholders(stripped, restore)
    # 从后往前应用，避免位置偏移
    result = stripped
    for start, end, new in sorted(edits, key=lambda e: e[0], reverse=True):
        result = result[:start] + new + result[end:]
    return _restore_placeholders(result, restore)


def apply_p44c_inout(sql: str) -> str:
    return fix_inout_returns(sql)