"""P4-4c: L3 装入失败的小修复

策略：
- BEGIN set_config(...) → BEGIN\nPERFORM set_config(...)  (PL/pgSQL 语法)
- FOR rec IN (SELECT ...) → 加 DECLARE rec record;  (PG 15 显式 record)
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


# BEGIN set_config( → BEGIN\nPERFORM set_config(
_BEGIN_SET_CONFIG_RE = re.compile(
    r"^(\s*)BEGIN\s+set_config\s*\(",
    re.IGNORECASE | re.MULTILINE,
)


def fix_begin_set_config(sql: str) -> str:
    """行首 `BEGIN set_config(...)` → 拆为 BEGIN 行 + PERFORM set_config(...)。

    PL/pgSQL 中 `BEGIN` 后面必须跟 ; 或新语句，不能直接跟函数调用。
    """
    def _replace(m: re.Match) -> str:
        indent = m.group(1)
        return f"{indent}BEGIN\n{indent}    PERFORM set_config("
    return _BEGIN_SET_CONFIG_RE.sub(_replace, sql)


# FOR rec IN (SELECT ...) 块之前需 DECLARE rec record;
# 简化策略：扫描每段 DECLARE...BEGIN，如果 BEGIN 内有 FOR rec IN (SELECT ...)，
# 且 DECLARE 内没有 "rec record" 声明，就在 DECLARE 段末尾追加。
def fix_for_rec_select(sql: str) -> str:
    """为 FOR rec IN (SELECT ...) 形式的循环补充 DECLARE rec record; 声明。"""
    stripped, restore = _strip_strings_and_comments(sql)
    lines = stripped.split("\n")
    out: list = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        # 找 DECLARE 段
        m = re.match(r"^(\s*)DECLARE\s*$", line, re.IGNORECASE)
        if m:
            indent = m.group(1)
            out.append(line)
            i += 1
            # 扫描 DECLARE 段直到 BEGIN 或函数体 END
            decl_lines: list = []
            while i < n and not re.match(r"^\s*BEGIN\b", lines[i], re.IGNORECASE):
                decl_lines.append(lines[i])
                i += 1
            # 看后续 BEGIN 段里是否有 FOR rec IN (SELECT
            body_tail: list = []
            body_i = i
            while body_i < n and not re.match(r"^\s*(?:END|EXCEPTION)\s*;", lines[body_i], re.IGNORECASE):
                body_tail.append(lines[body_i])
                body_i += 1
            body_text = "\n".join(body_tail)
            decl_text = "\n".join(decl_lines)
            if (re.search(r"\bFOR\s+rec\s+IN\s*\(\s*SELECT", body_text, re.IGNORECASE)
                    and "rec record" not in decl_text):
                # 在 DECLARE 段末尾加 rec record;（保持缩进一致）
                # 用 indent + 4 空格 与 _p25 声明对齐
                decl_indent = indent + "    "
                # 找 decl_lines 里最后一个非空行的缩进
                last_indent = decl_indent
                for dl in reversed(decl_lines):
                    if dl.strip():
                        sm = re.match(r"^(\s*)", dl)
                        if sm:
                            last_indent = sm.group(1)
                        break
                decl_lines.append(f"{last_indent}rec record;")
            out.extend(decl_lines)
            continue
        out.append(line)
        i += 1
    return _restore_placeholders("\n".join(out), restore)


def apply_p44c_fixes(sql: str) -> str:
    """P4-4c 第一阶段：set_config + FOR rec record + PERFORM set_config。"""
    sql = fix_begin_set_config(sql)
    sql = fix_for_rec_select(sql)
    sql = _stub_oracle_nested_functions(sql)
    sql = _fix_perform_set_config(sql)
    return sql


# ---------------------------------------------------------------------------
# Oracle 风格嵌套函数 stub
# ---------------------------------------------------------------------------
def _stub_oracle_nested_functions(sql: str) -> str:
    """删除 DECLARE 段中 Oracle 风格的嵌套函数定义。

    特征:
      1. `FUNCTION name(IN/OUT param) ...`（可能跨多行）→ 删整个块
      2. `IS` 单独一行 → 删 IS + BEGIN...END block

    删嵌套函数后若 DECLARE 后无 BEGIN 则补 BEGIN...END。
    """
    lines = sql.split("\n")
    out: list = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        # 类型1: 行首 FUNCTION（可能跨多行），检测 Oracle 嵌套函数特征
        if re.match(r"FUNCTION\s+[A-Za-z_][\w.]*\s*\(", stripped, re.IGNORECASE):
            fn_lines = [stripped]
            j = i + 1
            paren_depth = stripped.count("(") - stripped.count(")")
            while j < n and paren_depth > 0:
                next_line = lines[j].strip()
                fn_lines.append(next_line)
                paren_depth += next_line.count("(") - next_line.count(")")
                j += 1
            full_sig = " ".join(fn_lines)
            if (re.search(r"\bIN\s+\w|\bOUT\s+\w|\bINOUT\s+\w", full_sig, re.IGNORECASE)
                    or re.search(r"\bRETURN\s+\w|\bIS\b", full_sig, re.IGNORECASE)):
                # 跳过整个嵌套函数块: 从当前行到 END
                depth = 0
                while j < n:
                    cur = lines[j].strip()
                    if re.match(r"\bBEGIN\b", cur, re.IGNORECASE):
                        depth += 1
                        j += 1
                    elif re.match(r"\bEND\s+\S+\s*;|^\s*END\s*;\s*$", cur, re.IGNORECASE):
                        depth -= 1
                        j += 1
                        if depth <= 0:
                            break
                        j += 1
                    else:
                        j += 1
                i = j
                continue

        # 类型2: `IS` 单独一行
        if re.match(r"^\s*IS\s*$", stripped, re.IGNORECASE):
            j = i + 1
            # 跳过声明行（到 BEGIN 之前）
            while j < n:
                nxt = lines[j].strip()
                if re.match(r"^\s*BEGIN\b", nxt, re.IGNORECASE):
                    break
                j += 1
            j += 1  # skip BEGIN
            depth = 1
            while j < n:
                cur = lines[j].strip()
                if re.match(r"\bBEGIN\b", cur, re.IGNORECASE):
                    depth += 1
                    j += 1
                elif re.match(r"\bEND\s+\S+\s*;|^\s*END\s*;\s*$", cur, re.IGNORECASE):
                    depth -= 1
                    j += 1
                    if depth <= 0:
                        break
                else:
                    j += 1
            i = j
            continue

        out.append(line)
        i += 1

    # 后处理: 修复缺少 BEGIN 的 DECLARE 块
    sql = "\n".join(out)
    out = []
    lines = sql.split("\n")
    i = 0
    n = len(lines)
    while i < n:
        stripped = lines[i].strip()
        if re.match(r"^DECLARE\s*$", stripped, re.IGNORECASE):
            out.append(lines[i])
            i += 1
            while i < n:
                cur = lines[i].strip()
                if cur.startswith("$$;"):
                    out.append("BEGIN")
                    out.append("END;")
                    out.append(lines[i])
                    i += 1
                    break
                elif re.match(r"\bBEGIN\b", cur, re.IGNORECASE):
                    out.append(lines[i])
                    i += 1
                    break
                else:
                    out.append(lines[i])
                    i += 1
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def _fix_perform_set_config(sql: str) -> str:
    """裸 set_config(...) 语句加 PERFORM 前缀。

    PG 的 set_config() 返回 TEXT，在 PL/pgSQL 中作为语句必须 PERFORM。
    模式：THEN set_config(...)  →  THEN PERFORM set_config(...)
    或独立行 set_config(...)  →  PERFORM set_config(...)
    排除：:= set_config(...) / INTO ... set_config(...) — 赋值形式不需要
    """
    # 修复 SELECT ... INTO current_setting(...) FROM ... → PERFORM set_config(...)
    # 必须在 _strip_strings_and_comments 之前做，否则字符串被替换为占位符
    sql = _fix_into_current_setting(sql)
    stripped, restore = _strip_strings_and_comments(sql)
    # 匹配 set_config 前不是 :=/INTO/PERFORM 的情况
    new = re.sub(
        r"(?<!PERFORM\s)(?<!:=\s)(?<!INTO\s)\bset_config\s*\(",
        "PERFORM set_config(",
        stripped,
    )
    new = '\n'.join(new.split('\n'))
    return _restore_placeholders(new, restore)


def _fix_into_current_setting(sql: str) -> str:
    """修复 SELECT ... INTO current_setting('key') FROM <table> → PERFORM set_config(...)

    根因：Oracle 嵌套函数体被 stub 时，其内 SELECT INTO :var 被错翻为 INTO current_setting(...)。
    PG: current_setting() 是读配置函数，不能作为 INTO 目标；应内联为 PERFORM set_config(...)。
    """
    # 兼容跨行格式：FROM <table>; 可能被 line-wrap 切断
    lines = sql.split('\n')
    out_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if re.search(r'\bINTO\s+current_setting\s*\(', line, re.IGNORECASE):
            # 提取 current_setting('namespace.key') 中的 key
            m_key = re.search(r"current_setting\s*\(\s*'([^']+)'\s*\)", line, re.IGNORECASE)
            if not m_key:
                out_lines.append(line)
                i += 1
                continue
            key = m_key.group(1)
            # 向上找 SELECT 行
            j = i
            while j >= 0 and 'SELECT' not in lines[j].upper():
                j -= 1
            if j < 0:
                out_lines.append(line)
                i += 1
                continue
            select_line = lines[j]
            m_expr = re.search(r'\bSELECT\s+(.+?)\s+INTO\s+current_setting\s*\(', select_line, re.IGNORECASE | re.DOTALL)
            if not m_expr:
                out_lines.append(line)
                i += 1
                continue
            expr = m_expr.group(1).strip()
            # 向下找 FROM <table>;
            table = None
            for k in range(i, min(i + 3, len(lines))):
                lk = lines[k]
                m_from = re.search(r'\bFROM\s+([\w.]+)\s*;', lk, re.IGNORECASE)
                if m_from:
                    table = m_from.group(1)
                    replaced_line = f"PERFORM set_config('{key}', (SELECT {expr} FROM {table})::text, false);"
                    for q in range(j, k):
                        out_lines.append(lines[q])
                    out_lines.append(replaced_line)
                    i = k + 1
                    break
                if re.search(r'\bFROM\s*$', lk, re.IGNORECASE) and k + 1 < len(lines):
                    nxt = lines[k + 1]
                    m_cont = re.search(r'^([\w.]+)\s*;', nxt, re.IGNORECASE)
                    if m_cont:
                        table = m_cont.group(1)
                        replaced_line = f"PERFORM set_config('{key}', (SELECT {expr} FROM {table})::text, false);"
                        for q in range(j, k):
                            out_lines.append(lines[q])
                        out_lines.append(replaced_line)
                        i = k + 2
                        break
            else:
                out_lines.append(line)
                i += 1
            continue
        out_lines.append(line)
        i += 1
    return '\n'.join(out_lines)



