"""P2-5: 异常与运行时属性映射体系（Oracle → PostgreSQL）

落点：scripts/convert_oracle_poc.py post-pass（P2-5 调用入口）

6 个子函数（实施路径 v3.1 P2-5）：
- map_sqlcode (P2-5a): SQLCODE → _p25_sqlstate 变量（GET STACKED DIAGNOSTICS）
- map_sqlerrm (P2-5b): SQLERRM → _p25_msg 变量
- map_rowcount (P2-5d): SQL%ROWCOUNT → GET DIAGNOSTICS v := ROW_COUNT（紧跟）
- map_cursor_attrs (P2-5e): %NOTFOUND/%ISOPEN/%FOUND → NOT FOUND/IS OPEN/FOUND
- map_exception_names (P2-5f): DUP_VAL_ON_INDEX 等 → unique_violation 等
- enforce_when_others_raise (P2-5c): WHEN OTHERS 静默吞错 → 强制 RAISE

组合：apply_all_exception_maps(sql) 顺序调用 6 个子函数。

设计原则：
- 纯函数：输入 SQL 字符串，返回 SQL 字符串
- 字符串/注释豁免：用 _strip_strings_and_comments 占位符策略
- 宽松策略：找不到 / 改不动时保留原文 + warnings（不抛错，不阻断）
"""
from __future__ import annotations

import re
from typing import List, Tuple


# ===========================================================================
# Oracle 异常名 → PG 异常名（手册 §13.1 + §18）
# ===========================================================================
# PG SQLSTATE 映射（https://www.postgresql.org/docs/current/errcodes-appendix.html）
ORACLE_EXCEPTION_MAP = {
    "DUP_VAL_ON_INDEX": "unique_violation",
    "TOO_MANY_ROWS": "too_many_rows",
    "NO_DATA_FOUND": "no_data_found",
    "VALUE_ERROR": "data_exception",
    "ZERO_DIVIDE": "division_by_zero",
    "INVALID_CURSOR": "invalid_cursor_state",
    "LOGIN_DENIED": "insufficient_privilege",
    "NOT_LOGGED_ON": "admin_shutdown",
    "INVALID_NUMBER": "invalid_text_representation",
    "ROWTYPE_MISMATCH": "datatype_mismatch",
    "ACCESS_INTO_NULL": "null_value_not_allowed",
    "COLLECTION_IS_NULL": "null_value_not_allowed",
    "SUBSCRIPT_BEYOND_COUNT": "subscript_error",
    "SUBSCRIPT_OUTSIDE_LIMIT": "subscript_error",
}

# P2-5 注入变量名（EXCEPTION 块首注入，SQLCODE/SQLERRM 引用之）
_SQLSTATE_VAR = "_p25_sqlstate"
_MSG_VAR = "_p25_msg"


# ===========================================================================
# 字符串/注释豁免工具
# ===========================================================================
_PLACEHOLDER_FMT = "%%P25_STRIP_%d%%"
_STRIP_RE_CACHE: dict = {}


def _strip_strings_and_comments(sql: str) -> Tuple[str, dict]:
    """把字符串字面量、注释、dollar-quoted 块替换为占位符。

    关键：进入 `$$ ... $$` 内部后继续递归剥离内部 `' '` 和 `--` 注释，
    但 `$$` 边界**不**被剥离（不像之前会把整个函数体当占位符吃掉）。
    """
    restore: dict = {}
    counter = [0]

    def _put(text: str) -> str:
        key = _PLACEHOLDER_FMT % counter[0]
        counter[0] += 1
        restore[key] = text
        return key

    def _process_segment(text: str) -> str:
        """处理一段文本（可能是函数体外、也可能是 $$ ... $$ 内部）。"""
        out: List[str] = []
        i = 0
        n = len(text)
        while i < n:
            # 单行注释 -- ... \n
            if text[i:i + 2] == "--":
                end = text.find("\n", i)
                if end < 0:
                    end = n
                out.append(_put(text[i:end]))
                i = end
                continue
            # 块注释 /* ... */
            if text[i:i + 2] == "/*":
                end = text.find("*/", i + 2)
                if end < 0:
                    end = n
                else:
                    end += 2
                out.append(_put(text[i:end]))
                i = end
                continue
            # $$ ... $$ (PG dollar-quote) —— 递归处理内部
            if text[i:i + 2] == "$$":
                end = text.find("$$", i + 2)
                if end < 0:
                    end = n
                else:
                    end += 2
                # 内部递归处理（继续剥离 ' 字符串 和 -- 注释）
                inner = text[i + 2:end - 2]
                processed_inner = _process_segment(inner)
                out.append("$$" + processed_inner + "$$")
                i = end
                continue
            # 单引号字符串
            if text[i] == "'":
                j = i + 1
                while j < n:
                    if text[j] == "'":
                        if j + 1 < n and text[j + 1] == "'":
                            j += 2
                            continue
                        break
                    j += 1
                j = min(j + 1, n)
                out.append(_put(text[i:j]))
                i = j
                continue
            out.append(text[i])
            i += 1
        return "".join(out)

    return _process_segment(sql), restore


def _restore_placeholders(sql: str, restore: dict) -> str:
    """把占位符还原成原内容。"""
    if not restore:
        return sql
    # 注：_PLACEHOLDER_FMT = "%%P25_STRIP_%d%%"，经 % 格式化后是 "%P25_STRIP_52%"（2 个 %）。
    pattern = re.compile(r"%P25_STRIP_\d+%")
    return pattern.sub(lambda m: restore.get(m.group(0), m.group(0)), sql)


# ===========================================================================
# 找 EXCEPTION 块起始（注入 GET STACKED DIAGNOSTICS 的位置）
# ===========================================================================
_EXCEPTION_KEYWORD_RE = re.compile(r"\bEXCEPTION\b", re.IGNORECASE)


def _find_exception_blocks(sql: str) -> List[Tuple[int, int]]:
    """返回 [(ex_start_pos, end_pos), ...]，其中 end_pos 是块结束位置（END;）。

    PL/pgSQL 中 EXCEPTION 块从 EXCEPTION 关键字开始，到 END; 结束。
    只在 EXCEPTION 块内替换 SQLCODE/SQLERRM 等，避免误伤参数名/列名。
    """
    results: List[Tuple[int, int]] = []
    pos = 0
    while True:
        m = _EXCEPTION_KEYWORD_RE.search(sql, pos)
        if not m:
            break
        ex_start = m.start()
        end_pos = _find_exception_end(sql, m.end())
        results.append((ex_start, end_pos))
        pos = end_pos
    return results


def _find_exception_end(sql: str, start: int) -> int:
    """从 start 开始找 EXCEPTION 块的 END;。处理嵌套 BEGIN/END（理论简单，
    EXCEPTION 块一般不嵌套，但保险起见计数）。
    """
    depth = 0
    i = start
    while i < len(sql):
        # 跳字符串/注释
        if sql[i] == "-" and i + 1 < len(sql) and sql[i + 1] == "-":
            j = sql.find("\n", i)
            i = len(sql) if j < 0 else j + 1
            continue
        if sql[i] == "/" and i + 1 < len(sql) and sql[i + 1] == "*":
            j = sql.find("*/", i + 2)
            i = len(sql) if j < 0 else j + 2
            continue
        if sql[i] == "'":
            j = i + 1
            while j < len(sql):
                if sql[j] == "'":
                    if j + 1 < len(sql) and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            i = j + 1
            continue
        # 匹配 BEGIN/END
        m_begin = re.match(r"\bBEGIN\b", sql[i:], re.IGNORECASE)
        m_end = re.match(r"\bEND\b", sql[i:], re.IGNORECASE)
        if m_begin:
            depth += 1
            i += m_begin.end()
            continue
        if m_end:
            # END; 是 EXCEPTION 块结束；END IF/LOOP/CASE 跳过
            rest = sql[i + m_end.end():i + m_end.end() + 30].lstrip()
            if rest.upper().startswith(("IF", "LOOP", "CASE")):
                i += m_end.end()
                continue
            if depth == 0:
                # 找 END; 的 ;
                semi = sql.find(";", i + m_end.end())
                if semi < 0:
                    return len(sql)
                return semi + 1
            depth -= 1
            i += m_end.end()
            continue
        i += 1
    return len(sql)


def _process_exception_blocks(
    sql: str,
    block_transformer,
) -> str:
    """对每个 EXCEPTION 块应用 block_transformer(块内容) → 新块内容。

    块外内容保持不变。这是 P2-5 替换的核心：只动 EXCEPTION 块内。
    """
    blocks = _find_exception_blocks(sql)
    if not blocks:
        return sql
    # 从后往前替换，避免位置偏移
    result = sql
    for ex_start, ex_end in reversed(blocks):
        block_content = result[ex_start:ex_end]
        new_block = block_transformer(block_content)
        result = result[:ex_start] + new_block + result[ex_end:]
    return result


# ===========================================================================
# P2-5a: SQLCODE → _p25_sqlstate
# ===========================================================================
def _transform_sqlcode_in_block(block: str) -> str:
    """块转换器：在第一个 WHEN ... THEN 之后注入 GET STACKED DIAGNOSTICS，
    替换块内 SQLCODE → _p25_sqlstate。

    PL/pgSQL EXCEPTION 块结构：
        EXCEPTION
            WHEN cond THEN
                <handler>;  ← 注入点
            WHEN OTHERS THEN
                <handler>;
    """
    # 注：原在 EXCEPTION 关键字后注入会破坏 PL/pgSQL 语法（EXCEPTION 后必须 WHEN）
    # 改为：在第一个 WHEN ... THEN 之后注入
    inject = (
        f"GET STACKED DIAGNOSTICS {_SQLSTATE_VAR} = RETURNED_SQLSTATE,\n"
        f"                            {_MSG_VAR} = MESSAGE_TEXT;\n"
    )
    # 找第一个 WHEN ... THEN
    when_then_re = re.compile(r"(\bWHEN\s+\w+(?:\s*,\s*\w+)*\s+THEN\s+)", re.IGNORECASE)
    m = when_then_re.search(block)
    if not m:
        return block
    new_block = block[:m.end()] + inject + block[m.end():]
    # 替换 SQLCODE → _p25_sqlstate
    new_block = re.sub(r"\bSQLCODE\b", _SQLSTATE_VAR, new_block)
    return new_block


def map_sqlcode(sql: str) -> str:
    """EXCEPTION 块内 SQLCODE → _p25_sqlstate + 块首注入 GET STACKED DIAGNOSTICS。

    严格只处理 EXCEPTION 块内（避免误伤参数名/列名）。
    注：若没有 EXCEPTION 块，原文返回。
    """
    return _process_exception_blocks(sql, _transform_sqlcode_in_block)


# ===========================================================================
# P2-5b: SQLERRM → _p25_msg
# ===========================================================================
def _transform_sqlerrm_in_block(block: str) -> str:
    """块转换器：替换 SQLERRM → _p25_msg。

    若 P2-5a 已注入过 _p25_sqlstate，这里就只换 SQLERRM；
    若没注入，在第一个 WHEN ... THEN 后注入 GET STACKED DIAGNOSTICS。
    """
    if _SQLSTATE_VAR not in block:
        inject = (
            f"GET STACKED DIAGNOSTICS {_SQLSTATE_VAR} = RETURNED_SQLSTATE,\n"
            f"                            {_MSG_VAR} = MESSAGE_TEXT;\n"
        )
        when_then_re = re.compile(r"(\bWHEN\s+\w+(?:\s*,\s*\w+)*\s+THEN\s+)", re.IGNORECASE)
        m = when_then_re.search(block)
        if m:
            block = block[:m.end()] + inject + block[m.end():]
    block = re.sub(r"\bSQLERRM\b", _MSG_VAR, block)
    return block


def map_sqlerrm(sql: str) -> str:
    """EXCEPTION 块内 SQLERRM → _p25_msg（保证 _p25_msg 变量被注入）。"""
    return _process_exception_blocks(sql, _transform_sqlerrm_in_block)


# ===========================================================================
# P2-5d: SQL%ROWCOUNT → GET DIAGNOSTICS v := ROW_COUNT（紧跟前一条语句）
# ===========================================================================
_ROWCOUNT_ASSIGN_RE = re.compile(
    r"(\b\w+\s*:=\s*)SQL%ROWCOUNT\s*;",
    re.IGNORECASE,
)


def map_rowcount(sql: str) -> str:
    """`v := SQL%ROWCOUNT;` → `GET DIAGNOSTICS v := ROW_COUNT;`（行内替换）。

    注：手版 §11.5 说"必须紧跟前一条语句"，但实现上我们直接
    替换为 GET DIAGNOSTICS 形式本身是 PL/pgSQL 的合法形式。
    字符串拼接里出现的 SQL%ROWCOUNT 不处理（语义不同）。
    """
    stripped, restore = _strip_strings_and_comments(sql)

    def _replace_one(m: re.Match) -> str:
        var_assign = m.group(1)
        var_match = re.match(r"\s*(\w+)\s*:=\s*", var_assign)
        if not var_match:
            return m.group(0)
        var = var_match.group(1)
        return f"GET DIAGNOSTICS {var} := ROW_COUNT;"

    new = _ROWCOUNT_ASSIGN_RE.sub(_replace_one, stripped)
    return _restore_placeholders(new, restore)


# ===========================================================================
# P2-5e: 游标属性 %NOTFOUND/%ISOPEN/%FOUND
# ===========================================================================
def map_cursor_attrs(sql: str) -> str:
    """cursor%NOTFOUND → NOT FOUND；cursor%ISOPEN → IS NOT FOUND；cursor%FOUND → FOUND。

    PL/pgSQL 不支持游标 IS OPEN，但支持 FOUND/NOT FOUND（最近的 FETCH）。
    原本 Oracle 中 `v_cursor%NOTFOUND` 是 "v_cursor 游标的 FOUND 状态"，
    PL/pgSQL 中只有全局 FOUND/NOT FOUND（最近 FETCH），所以丢游标名。
    多个游标同函数内可能有冲突——本改写不处理，使用者要注意。

    %ISOPEN 语义近似 `IS NOT FOUND`（游标 EOF 后可认为 "已不开放"）—— 粗近似。
    """
    stripped, restore = _strip_strings_and_comments(sql)
    # 整个 `XXX%NOTFOUND` → `NOT FOUND`（含游标名）
    stripped = re.sub(r"\w+%NOTFOUND\b", "NOT FOUND", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\w+%ISOPEN\b", "IS NOT FOUND", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\w+%FOUND\b", "FOUND", stripped, flags=re.IGNORECASE)
    return _restore_placeholders(stripped, restore)


# ===========================================================================
# P2-5f: Oracle 异常名 → PG SQLSTATE
# ===========================================================================
def map_exception_names(sql: str) -> str:
    """Oracle 预定义异常名 → PG SQLSTATE（在 EXCEPTION/WHEN 子句中）"""
    stripped, restore = _strip_strings_and_comments(sql)
    for ora, pg in ORACLE_EXCEPTION_MAP.items():
        # 大小写不敏感替换为 PG 形式
        stripped = re.sub(
            r"\b" + re.escape(ora) + r"\b",
            pg,
            stripped,
            flags=re.IGNORECASE,
        )
    return _restore_placeholders(stripped, restore)


# ===========================================================================
# P2-5c: WHEN OTHERS 静默吞错检测 + 强制 RAISE
# ===========================================================================
# 匹配 `WHEN OTHERS THEN <something-statement>;` 单行形式
_WHEN_OTHERS_SIMPLE_RE = re.compile(
    r"(\bWHEN\s+OTHERS\s+THEN\s+)(?P<stmt>"
    r"RETURN\s+[^;]+;?"   # RETURN ...
    r"|NULL\s*;?"         # NULL
    r"|RAISE\s+[^;]+;?"   # 已有 RAISE（不强制改）
    r")",
    re.IGNORECASE | re.DOTALL,
)


def enforce_when_others_raise(sql: str) -> str:
    """`WHEN OTHERS THEN RETURN X;` / `WHEN OTHERS THEN NULL;` → 强制 RAISE。

    实现策略：
    1. 找 WHEN OTHERS 块
    2. 检测块内是否有 RAISE（已有则跳过）
    3. 没有 RAISE：在块首注入 GET DIAGNOSTICS + RAISE EXCEPTION
    """
    stripped, restore = _strip_strings_and_comments(sql)

    # 找所有 WHEN OTHERS 块（用花括号法不适用 PL/SQL，改用 END; 收尾）
    # 简化策略：行级处理，每行匹配
    lines = stripped.split("\n")
    new_lines: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # 检测 WHEN OTHERS 起始
        if re.search(r"\bWHEN\s+OTHERS\b", line, re.IGNORECASE):
            # 看块内（向后直到 END;）有没有 RAISE
            block_lines: List[str] = [line]
            j = i + 1
            while j < len(lines):
                block_lines.append(lines[j])
                if re.search(r"^\s*END\s*;", lines[j], re.IGNORECASE):
                    break
                j += 1
            j = min(j, len(lines) - 1)  # 防止越界
            block_text = "\n".join(block_lines)
            has_raise = bool(re.search(r"\bRAISE\b", block_text, re.IGNORECASE))
            if not has_raise:
                # 在 WHEN OTHERS 后注入（保留原 line 的 WHEN OTHERS THEN）
                m_then = re.search(
                    r"(\bWHEN\s+OTHERS\s+THEN\s*)(.*)$", line, re.IGNORECASE
                )
                if m_then:
                    indent_m = re.match(r"^(\s*)", line)
                    indent = indent_m.group(1) if indent_m else ""
                    # 关键修复：inject 替换了原 WHEN...RETURN 行，但必须
                    # 保留原块的 END;（block_lines 最后一行），否则块不闭合。
                    # block_lines = [WHEN 行, 中间行..., END; 行]
                    end_line = lines[j] if re.match(
                        r"^\s*END\s*;", lines[j], re.IGNORECASE
                    ) else f"{indent}END;"
                    inject_block = (
                        f"{indent}WHEN OTHERS THEN\n"
                        f"{indent}    GET STACKED DIAGNOSTICS {_MSG_VAR} = MESSAGE_TEXT;\n"
                        f"{indent}    RAISE EXCEPTION 'ORA: %', {_MSG_VAR};\n"
                        f"{end_line}\n"
                    )
                    new_lines.append(inject_block)
                    i = j + 1
                    continue
            # 有 RAISE 或无 THEN（异常）：原样保留
            for k in range(i, j + 1):
                new_lines.append(lines[k])
            i = j + 1
            continue
        new_lines.append(line)
        i += 1

    return _restore_placeholders("\n".join(new_lines), restore)


# ===========================================================================
# 变量声明注入（修复 P2-5 缺陷：_p25_msg/_p25_sqlstate 必须在 DECLARE 声明）
# ===========================================================================

_DECL_VAR_RE = re.compile(
    r"(\$\$\s*\n)(\s*DECLARE\s*\n)",
    re.IGNORECASE,
)


def _inject_into_one_block(block: str) -> str:
    """对单个函数块（一个 CREATE FUNCTION/PROCEDURE）注入变量声明。

    块内引用了 _p25_msg/_p25_sqlstate 但 DECLARE 段没有声明时才注入。
    """
    if _MSG_VAR not in block and _SQLSTATE_VAR not in block:
        return block
    # 已声明则跳过（避免重复）
    if re.search(rf"\b{re.escape(_MSG_VAR)}\s+TEXT\b", block):
        return block

    decl_line = (
        f"    {_MSG_VAR} TEXT;\n"
        f"    {_SQLSTATE_VAR} TEXT;\n"
    )

    # 情况 A：已有 DECLARE ...（在 $$ 之后）
    # 支持两种格式：'DECLARE\n' 和 'DECLARE <vars>...\n'
    m = re.search(r"(\$\$\s*\n\s*DECLARE\b[^\n]*\n)", block, re.IGNORECASE)
    if m:
        return block[:m.end()] + decl_line + block[m.end():]

    # 情况 B：$$ 后直接 BEGIN（无 DECLARE）
    m = re.search(r"(\$\$\s*\n)(\s*BEGIN\b)", block, re.IGNORECASE)
    if m:
        return (
            block[:m.end(1)]
            + f"DECLARE\n{decl_line}\n"
            + block[m.start(2):]
        )
    return block


def inject_declaration_variables(sql: str) -> str:
    """对每个 CREATE FUNCTION/PROCEDURE 块独立注入变量声明。

    多函数文件必须逐块处理（第一个函数声明了不代表其它函数也有）。
    PL/pgSQL 要求变量先声明再赋值（GET STACKED DIAGNOSTICS 不会隐式声明）。
    """
    if _MSG_VAR not in sql and _SQLSTATE_VAR not in sql:
        return sql

    # 按 CREATE OR REPLACE FUNCTION/PROCEDURE 切块
    positions = [m.start() for m in re.finditer(
        r"(?i)CREATE\s+OR\s+REPLACE\s+(FUNCTION|PROCEDURE)", sql)
    ]
    if not positions:
        # 单块（无 CREATE 头）→ 整体处理
        return _inject_into_one_block(sql)

    # 逐块：positions[i] 到 positions[i+1] 之间
    result_parts: list = []
    prev = 0
    for idx, start in enumerate(positions):
        end = positions[idx + 1] if idx + 1 < len(positions) else len(sql)
        # 块前内容（注释等）
        result_parts.append(sql[prev:start])
        block = sql[start:end]
        result_parts.append(_inject_into_one_block(block))
        prev = end
    return "".join(result_parts)


# ===========================================================================
# 组合入口
# ===========================================================================
def apply_all_exception_maps(sql: str) -> str:
    """按顺序应用 P2-5 所有子函数（顺序重要：P2-5c 最后做，确保其他已替换）。"""
    sql = map_sqlcode(sql)
    sql = map_sqlerrm(sql)
    sql = map_rowcount(sql)
    sql = map_cursor_attrs(sql)
    sql = map_exception_names(sql)
    sql = enforce_when_others_raise(sql)
    # 最后：若引用了 _p25_msg/_p25_sqlstate，确保 DECLARE 段已声明
    sql = inject_declaration_variables(sql)
    return sql
