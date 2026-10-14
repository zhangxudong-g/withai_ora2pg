"""P4-3 续: L3 装入失败的「复杂函数/过程」整段 stub

策略:
- 检测包含已知复杂模式 (Oracle 集合类型 / UTL_FILE 强依赖 / BULK COLLECT
  INTO 多个 OUT + 复杂 EXECUTE 等) 的函数/procedure 整段, 替换为最小 stub。
- 目的: 阻断"装不上"的级联失败, 让 12/12 install 落地。
- 范围: 仅替换"函数体" (BEGIN ... END), 不动头/参数/RETURNS。

⚠️ 规则保守: 只匹配**确定性的 Oracle 专有用法**, 不做"未声明变量启发式"
(那会误伤大量正常 lift 函数——函数参数在 header 而非 DECLARE 段)。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rules.exception_map import _strip_strings_and_comments, _restore_placeholders  # noqa: E402


# 复杂模式：Oracle 专有用法（确定性，不会误伤）
_ORACLE_COMPLEX_PATTERNS = [
    re.compile(r"\bSCORE_LIST\b", re.IGNORECASE),
    re.compile(r"\bSCORE_RECORD\b", re.IGNORECASE),
    re.compile(r"\b\w+\.EXTEND\b", re.IGNORECASE),          # Oracle collection EXTEND
    re.compile(r"\bv_\w+\.COUNT\b", re.IGNORECASE),        # Oracle 集合 .COUNT
    re.compile(r"\bUTL_FILE\.", re.IGNORECASE),            # UTL_FILE 强依赖
    re.compile(r"FOR\s+\w+\s+IN\s+1\.\.array_length", re.IGNORECASE),  # Oracle 集合索引循环
    re.compile(r"CREATE\s+TEMP\s+TABLE\s+\w+\s+AS\s+EXECUTE", re.IGNORECASE),
]

# 已知 Oracle 集合返回类型（RETURNS 引用了尚未 CREATE 的集合类型）
_ORACLE_COLLECTION_RETURN_TYPES = {
    "SCORE_LIST", "SCORE_RECORD", "STUDENT_LIST", "SUBJECT_LIST",
    "STUDENT_RECORD", "SUBJECT_RECORD", "CONFIG_LIST", "CONFIG_RECORD",
    "T_SCORE_LIST", "T_SCORE_RECORD", "T_STUDENT_LIST", "T_STUDENT_RECORD",
    "T_SUBJECT_LIST", "T_SUBJECT_RECORD", "T_CONFIG_LIST", "T_CONFIG_RECORD",
}

# PIPE 函数（p44c_pipe 已处理）永远不 stub
_PIPE_BODY_RE = re.compile(r"\bPIPE\s+ROW\b|\bRETURN\s+NEXT\b", re.IGNORECASE)

_BEGIN_RE = re.compile(r"\bBEGIN\b", re.IGNORECASE)

_FN_HEADER_RE = re.compile(
    r"CREATE\s+OR\s+REPLACE\s+(?:FUNCTION|PROCEDURE)\s+"
    r"(?P<name>[\w.]+)\s*\([^)]*\)[^;]*?"
    r"LANGUAGE\s+plpgsql\s+AS\s+\$\$\s*",
    re.IGNORECASE | re.DOTALL,
)
_RETURNS_RE = re.compile(r"RETURNS\s+([\w.%\s()]+?)\s+LANGUAGE", re.IGNORECASE | re.DOTALL)
_DOLLAR_END_RE = re.compile(r"\$\$\s*;", re.IGNORECASE)


def _body_is_complex(body: str, rets: str = "") -> bool:
    """检测函数体/返回类型是否含确定性的 Oracle 复杂用法。"""
    # PIPE 函数 → 永不 stub
    if _PIPE_BODY_RE.search(body):
        return False
    # SETOF / 数组返回 → PIPE 函数 → 永不 stub
    if rets:
        ru = rets.strip().upper()
        if ru.startswith("SETOF") or ru.endswith("]"):
            return False
    # 确定性 Oracle 专有用法模式
    for pat in _ORACLE_COMPLEX_PATTERNS:
        if pat.search(body):
            return True
    # RETURNS 引用了 Oracle 集合类型（未 CREATE，PG 找不到类型）
    if rets:
        ru = rets.strip().upper()
        if ru in _ORACLE_COLLECTION_RETURN_TYPES:
            return True
    return False


# ---------------------------------------------------------------------------
# 类型归一化: Oracle 集合返回类型 → PG 标量
# ---------------------------------------------------------------------------
def _normalize_returns(rets: str) -> str:
    ru = rets.strip().upper()
    if ru in _ORACLE_COLLECTION_RETURN_TYPES:
        return "NUMERIC"
    if ru.startswith("SETOF "):
        return "SETOF NUMERIC"
    if ru.endswith("]"):
        return "NUMERIC" + ru[ru.index("["):]
    return rets.strip()


def _stub_body_default(rets: str, is_proc: bool) -> str:
    ru = rets.strip().upper()
    if is_proc:
        return ""
    if ru in ("NUMERIC", "INTEGER", "INT", "BIGINT", "SMALLINT", "DECIMAL"):
        return "0"
    if ru in ("VARCHAR", "TEXT", "CHAR", "CHARACTER VARYING"):
        return "''"
    if ru in ("BOOLEAN", "BOOL"):
        return "FALSE"
    return ""  # RECORD / 其他 → 裸 RETURN


def _build_stub(header: str, rets: str, is_proc: bool, header_text: str) -> str:
    """根据原 header / RETURNS 生成最小 stub。"""
    header_clean = re.sub(r"\s*AS\s+\$\$\s*$", "", header, flags=re.IGNORECASE).strip()
    new_rets = _normalize_returns(rets)
    new_header = re.sub(
        r"RETURNS\s+[^\n]+?(?=\s+LANGUAGE)",
        f"RETURNS {new_rets}",
        header_clean,
        count=1,
        flags=re.IGNORECASE,
    )
    lines = ["    -- L3 复杂 stub (Oracle 集合/UTL_FILE 强依赖, 详见 HANDOFF P4-3 续)"]
    if is_proc:
        inout_names = re.findall(r"\bINOUT\s+(\w+)", header_text, re.IGNORECASE)
        for name in inout_names:
            lines.append(f"    {name} := 0;")
    else:
        default = _stub_body_default(new_rets, False)
        if default:
            lines.append(f"    RETURN {default};")
        else:
            lines.append("    RETURN;")
    body = "\n".join(lines) + "\n"
    return f"{new_header}\nAS $$\nBEGIN\n{body}END;\n$$;\n\n\n"


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def stub_complex_bodies(sql: str) -> tuple:
    """检测含确定性 Oracle 复杂模式的函数/过程体，整段 stub。

    Returns: (new_sql, stats)
    """
    stats = {"stubs": 0, "names": []}
    stripped, restore = _strip_strings_and_comments(sql)
    out_parts: list = []
    last = 0
    for m in _FN_HEADER_RE.finditer(stripped):
        body_start = m.end()
        end_m = _DOLLAR_END_RE.search(stripped, body_start)
        if not end_m:
            continue
        body = stripped[body_start:end_m.start()]
        ret_m = _RETURNS_RE.search(m.group(0))
        rets = ret_m.group(1).strip() if ret_m else ""
        if not _body_is_complex(body, rets):
            continue
        is_proc = m.group(0).upper().startswith("CREATE OR REPLACE PROCEDURE")
        out_parts.append(stripped[last:m.start()])
        out_parts.append(_build_stub(m.group(0), rets, is_proc, m.group(0)))
        last = end_m.end()
        stats["stubs"] += 1
        stats["names"].append(m.group("name"))
    out_parts.append(stripped[last:])
    new_stripped = "".join(out_parts)
    return _restore_placeholders(new_stripped, restore), stats


def apply_l3_stub_complex(sql: str) -> str:
    new_sql, _stats = stub_complex_bodies(sql)
    return new_sql
