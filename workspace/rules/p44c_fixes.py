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
    """P4-4c 第一阶段：set_config + FOR rec record。"""
    sql = fix_begin_set_config(sql)
    sql = fix_for_rec_select(sql)
    return sql
