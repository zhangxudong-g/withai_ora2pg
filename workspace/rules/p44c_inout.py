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
_FN_START = re.compile(r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+([\w.]+)\s*\(", re.IGNORECASE)
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
    sql = fix_inout_returns(sql)
    sql = _convert_multi_out_to_procedure(sql)
    # 注意: _fix_bare_procedure_calls 只能在 PACKAGE_BODY 上跑（它会给
    # 大写开头的裸调用加 PERFORM，误伤返回值的函数如 FORMAT_CONTENT）。
    # 在 post_pass 中按文件名调用，这里只做 core 修复。
    return sql


# -------------------------------------------------------------------------
# 追加: 大写开头的 procedure/function 调用 → PERFORM
# -------------------------------------------------------------------------
def _fix_bare_procedure_calls(sql: str) -> str:
    """为所有大写开头的裸函数/过程调用添加 PERFORM 前缀（表达式上下文除外）。

    Oracle 转换后的 PostgreSQL 代码中，procedure 调用如 `PROC_INSERT_WORK(...)`
    需要 `PERFORM` 或 `CALL` 前缀。
    排除: SELECT 列、括号内表达式、行首关键字（IF/WHEN/THEN 等）。
    """
    stripped, restore = _strip_strings_and_comments(sql)
    # 先修复 PERFORM <字面量> -- stub 的错误 stub
    stripped = re.sub(
        r"^\s*PERFORM\s+\d+\s*",
        "-- stub: ",
        stripped,
        flags=re.MULTILINE,
    )
    # 修复 DECLARE 后缩进错位的裸变量声明 (如 "bRTN    BOOLEAN;")
    stripped = re.sub(
        r"^(\w+)\s+(\w+);$",
        r"    \1 \2;",
        stripped,
        flags=re.MULTILINE,
    )
    # 修复 CALL proc(...) 作为语句 → PERFORM proc(...)
    # PG 15: CALL 不能在 plpgsql 块内作为普通语句
    stripped = re.sub(
        r"^\s*CALL\s+",
        "PERFORM ",
        stripped,
        flags=re.MULTILINE,
    )
    # 修复 PERFORM 被误加到表达式行 (PERFORM expr || ',' || ...)
    # 去掉大写开头函数的 PERFORM 前缀（PERFORM FOO(...) → FOO(...)）
    stripped = re.sub(
        r"^\s*PERFORM\s+([A-Z_][A-Z0-9_]*)\(",
        r"\1(",
        stripped,
        flags=re.MULTILINE,
    )
    lines = stripped.split("\n")
    out = []
    _PG_KEYWORDS = frozenset({
        "SELECT", "INSERT", "UPDATE", "DELETE", "WITH", "COPY", "EXPLAIN",
        "TABLE", "VALUES", "SET", "SHOW", "VACUUM", "ANALYZE", "GRANT",
        "REVOKE", "CREATE", "ALTER", "DROP", "TRUNCATE", "LOCK", "NOTIFY",
        "LISTEN", "LOAD", "DEALLOCATE", "DISCARD", "FETCH", "MOVE", "CLOSE",
        "PREPARE", "EXECUTE", "CALL", "DO", "COMMENT", "REFRESH", "IMPORT",
        "AND", "OR", "NOT", "CASE", "WHEN", "THEN", "ELSE", "IF", "LOOP",
        "RETURN", "RAISE", "EXECUTE", "PERFORM",
    })
        # 大写开头的裸调用: 行首 + 标识符 + (
    _PROC_CALL_RE = re.compile(r"^\s*([A-Z_][A-Z0-9_]*)\s*\(", re.MULTILINE)
    # 排除行首关键字上下文
    _LEADING_KW_RE = re.compile(
        r"^\s*(?:SELECT|INSERT|UPDATE|DELETE|WITH|VALUES|FROM|WHERE|AND|OR|"
        r"WHEN|IF|THEN|ELSE|ELSIF|RETURN|CASE|END|LOOP|WHILE|FOR|EXIT|UNTIL|RAISE|EXECUTE|PERFORM|INTO)",
        re.IGNORECASE,
    )
    # 跟踪是否在 SELECT 列中间（SELECT 之后到 INTO/FROM 之前）
    in_select_columns = False
    # 跟踪是否在 VALUES (...) 列表中
    in_values = False
    for line in lines:
        stripped_line = line.strip()
        # 检测 SELECT 开头
        if re.match(r"^\s*SELECT\b", line, re.IGNORECASE):
            in_select_columns = True
        # 检测 INTO / FROM 结束 SELECT 列
        if in_select_columns and re.match(r"^\s*(INTO|FROM)\b", stripped_line, re.IGNORECASE):
            in_select_columns = False
        # 检测 VALUES 开头 (可能在本行或前一行)
        if re.match(r"^\s*VALUES\s*\(", stripped_line, re.IGNORECASE):
            in_values = True
        # 也检测前一行是否以 VALUES ( 结尾
        if not in_values and out and re.search(r"VALUES\s*\(\s*$", out[-1], re.IGNORECASE):
            in_values = True
        # 检测 VALUES 列表结束 (行尾为 )
        if in_values and stripped_line.endswith(");"):
            in_values = False
        # 在 SELECT 列中间不加 PERFORM
        if in_select_columns:
            out.append(line)
            continue
        # 在 VALUES 列表中不加 PERFORM
        if in_values:
            out.append(line)
            continue
        # 跳过 PERFORM <字面量>（错误 stub）
        if re.match(r"^\s*PERFORM\s+\d", line):
            out.append(line)
            continue
        # 跳过表达式续行（以 || 开头或前一行以 || 结尾）
        if stripped_line.startswith("||"):
            out.append(line)
            continue
        # 检查前一行是否以 || 结尾（本行是续行）
        if out and out[-1].strip().endswith("||"):
            out.append(line)
            continue
        m = _PROC_CALL_RE.match(line)
        if (m and not line.lstrip().startswith("PERFORM")
                and not _LEADING_KW_RE.match(line)):
            name = m.group(1)
            if name.upper() not in _PG_KEYWORDS:
                rest = line[m.end()-1:]
                out.append(f"PERFORM {name}{rest}")
                continue
        out.append(line)
    return _restore_placeholders("\n".join(out), restore)


# ---------------------------------------------------------------------------
# 多 OUT 参数函数 → PROCEDURE（PG 15: 函数调用不能做独立语句）
# ---------------------------------------------------------------------------
def _convert_multi_out_to_procedure(sql: str) -> str:
    """带 ≥2 个 OUT/INOUT 参数的 FUNCTION → PROCEDURE。

    PG 15 实测：带多 OUT 参数的函数调用 `func(a, OUT b, OUT c)` 作为独立语句
    会报 `syntax error near func_name`。改为 PROCEDURE（无 RETURNS 子句）。
    同时将函数体内的调用改为 PERFORM。

    同时修复 preceding `DROP FUNCTION IF EXISTS` → `DROP PROCEDURE IF EXISTS`
    （因为函数被转为 PROCEDURE 后，旧的 DROP FUNCTION 会报"is not a function"）。
    """
    stripped, restore = _strip_strings_and_comments(sql)

    # 第一遍: 找出所有多 OUT 函数名
    _DEF_RE = re.compile(
        r"CREATE\s+OR\s+REPLACE\s+(?:FUNCTION|PROCEDURE)\s+([\w.]+)"
        r"\s*\(([^)]*)\)",
        re.IGNORECASE,
    )
    multi_out_names = set()
    for m in _DEF_RE.finditer(stripped):
        params = m.group(2)
        out_count = len(re.findall(r"\b(?:INOUT|OUT)\s+\w+", params, re.IGNORECASE))
        if out_count >= 2:
            multi_out_names.add(m.group(1))

    if not multi_out_names:
        return _restore_placeholders(stripped, restore)

    # 第二遍: 改 FUNCTION → PROCEDURE，删 RETURNS，补 DROP PROCEDURE
    lines = stripped.split("\n")
    out: list = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]

        # 检测 FUNCTION 定义（不包括 PROCEDURE）
        fn_m = re.match(
            r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+([\w.]+)\s*\(",
            line, re.IGNORECASE,
        )
        if fn_m:
            fn_name = fn_m.group(1)
            if fn_name in multi_out_names:
                # 向前找 DROP FUNCTION IF EXISTS 行，改为 DROP PROCEDURE IF EXISTS
                if out and re.match(r"DROP\s+FUNCTION\s+IF\s+EXISTS", out[-1], re.IGNORECASE):
                    out[-1] = re.sub(
                        r"DROP\s+FUNCTION\s+IF\s+EXISTS",
                        "DROP PROCEDURE IF EXISTS",
                        out[-1], 0, re.IGNORECASE,
                    )
                # 追加 DROP PROCEDURE IF EXISTS（兜底）
                out.append(f"DROP PROCEDURE IF EXISTS {fn_name};")
                # 改 FUNCTION → PROCEDURE
                out.append(re.sub(
                    r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+",
                    "CREATE OR REPLACE PROCEDURE ",
                    line, 0, re.IGNORECASE,
                ))
                i += 1
                # 跳过 RETURNS 行
                while i < n and not lines[i].strip().upper().startswith("LANGUAGE"):
                    i += 1
                continue
        out.append(line)
        i += 1

    result = "\n".join(out)

    # 第三遍: 语句级调用改 PERFORM（不在多 OUT 定义内）
    # PG: 函数/过程调用可写作 `PERFORM func(args)`
    # 不改: IF/WHEN/THEN/ELSIF/RETURN 等表达式上下文
    _DEF_RE2 = re.compile(
        r"CREATE\s+OR\s+REPLACE\s+(?:FUNCTION|PROCEDURE)\s+([\w.]+)\s*\(",
        re.IGNORECASE,
    )
    lines = result.split("\n")
    out = []
    in_multi_out_def = False
    for line in lines:
        fn_m = _DEF_RE2.match(line)
        if fn_m and fn_m.group(1) in multi_out_names:
            in_multi_out_def = True
            out.append(line)
        elif in_multi_out_def and re.match(r"\s*\$\$", line.strip()):
            in_multi_out_def = False
            out.append(line)
        elif not in_multi_out_def:
            new_line = line
            # 跳过表达式上下文（行首关键字）
            if not re.match(
                    r"^\s*(?:IF|WHEN|ELSIF|RETURN|CASE\b|SELECT\b|VALUES\b"
                    r"|THEN\b|ELSE\b|AND\b|OR\b|NOT\b)",
                    new_line, re.IGNORECASE):
                for fn in multi_out_names:
                    new_line = re.sub(
                        rf"\b({re.escape(fn)})\s*\(",
                        rf"PERFORM \1(",
                        new_line,
                    )
            out.append(new_line)
        else:
            out.append(line)

    return _restore_placeholders("\n".join(out), restore)
