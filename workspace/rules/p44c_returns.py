"""P4-4c 阶段 2：RETURNS TABLE(IN/OUT o_xxx) 错误修复

Oracle 源 `RETURN BOOLEAN` / `RETURN NUMBER` / `RETURN VARCHAR2`
被引擎错误地转成 `RETURNS TABLE(IN ...)` 或 `RETURNS TABLE(o_xxx ...)`。

策略（保守修改，仅处理"明显错误"的形态）：
- `RETURNS TABLE(IN <type>)` → `RETURNS <type>`（单一字段，且字段名是 IN）
- `RETURNS TABLE(o_<name> ...)` → `RETURNS <first_field_type>`（OUT 参数伪表）
- 不处理正常的 `RETURNS TABLE(real_name ...)`
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


# 匹配整个 RETURNS TABLE(...) 句（含括号内容）
# 组1: 整个 RETURNS TABLE(...)，组2: 第一个字段名，组3: 第一个字段类型
_RETURNS_TABLE_BAD_RE = re.compile(
    r"RETURNS\s+TABLE\s*\(\s*"
    r"((?:IN)\s+([\w.]+(?:\s*\(\s*\d+\s*,\s*\d+\s*\))?)"
    r"|(?:o_\w+)\s+([\w.]+(?:\s*\(\s*\d+\s*,\s*\d+\s*\))?))"
    r"(?:\s*,\s*(?:o_)?\w+\s+[\w.]+(?:\s*\(\s*\d+\s*,\s*\d+\s*\))?)*"
    r"\s*\)",
    re.IGNORECASE,
)


def fix_returns_table_out(sql: str) -> str:
    """将错误的 RETURNS TABLE(IN/o_xxx) 改为正常 RETURNS <type> + 补回 o_xxx INOUT 参数。

    策略：
    1. 匹配 RETURNS TABLE(IN/o_xxx, ...) 句
    2. 收集所有 o_xxx 字段 (col_name, type)
    3. RETURNS 改为 第一个字段类型（或 BOOLEAN）
    4. 在函数签名补回 o_xxx INOUT 参数
    5. 函数体里的 RETURN <num> 改为 RETURN 第一个值或 TRUE
    """
    stripped, restore = _strip_strings_and_comments(sql)

    # 逐函数处理
    lines = stripped.split("\n")
    new_lines: list = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        # 找 CREATE OR REPLACE FUNCTION 头
        m = re.match(
            r"^(\s*CREATE\s+OR\s+REPLACE\s+FUNCTION\s+\w+\s*\()([^)]*)(\))\s*",
            line, re.IGNORECASE
        )
        if m:
            prefix = m.group(1)
            params = m.group(2)
            suffix = m.group(3)
            # 找下一行 RETURNS TABLE(...)
            for j in range(i + 1, min(i + 5, n)):
                rt_m = re.search(
                    r"RETURNS\s+TABLE\s*\(\s*((?:IN\s+[\w.]+)|o_\w+\s+[\w.]+)(?:\s*,\s*(?:o_)?\w+\s+[\w.]+)*\s*\)",
                    lines[j], re.IGNORECASE
                )
                if rt_m:
                    # 提取所有 o_xxx TYPE 字段
                    fields_str = rt_m.group(1)
                    # 解析字段：支持 IN type 或 o_name type, o_name type, ...
                    fields = re.findall(r"(?:o_(\w+)\s+([\w.]+))|IN\s+([\w.]+)", fields_str, re.IGNORECASE)
                    out_fields = []  # (name, type)
                    for n_, t_, in_t in fields:
                        if n_:
                            out_fields.append((n_, t_))
                    # 决定 RETURNS 类型（优先 BOOLEAN）
                    ret_type = "BOOLEAN"
                    new_ret_line = re.sub(
                        r"RETURNS\s+TABLE\s*\([^)]*\)",
                        f"RETURNS {ret_type}",
                        lines[j], flags=re.IGNORECASE
                    )
                    # 在参数中补 o_xxx INOUT
                    extra_params = ", ".join(
                        f"INOUT o_{name} {tp}" for name, tp in out_fields
                    )
                    if extra_params:
                        if params.strip():
                            new_params_line = prefix + params + ", " + extra_params + suffix
                        else:
                            new_params_line = prefix + extra_params + suffix
                    else:
                        new_params_line = line
                    new_lines.append(new_params_line)
                    new_lines.append(new_ret_line)
                    i = j + 1
                    break
            else:
                new_lines.append(line)
                i += 1
            continue
        new_lines.append(line)
        i += 1

    return _restore_placeholders("\n".join(new_lines), restore)


def apply_p44c_returns(sql: str) -> str:
    return fix_returns_table_out(sql)
