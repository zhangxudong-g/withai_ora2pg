"""P4-4a: TYPE 文件 SELF 标识符改写

Oracle OBJECT TYPE 的 MEMBER FUNCTION 引用 SELF.x，被引擎提取为顶层函数
GET_xxx_of_TYPE 后，函数体里仍是 SELF_xxx 形式，PL/pgSQL 把它当作列名
查表 → "字段不存在"。

策略：
- 已知 TYPE 表：SCORE_RECORD, STUDENT_RECORD, REF_CURSOR_T
- 函数签名：GET_xxx_of_TYPE() → GET_xxx_of_TYPE(i_self TYPE)
- 函数体：SELF_<TYPE>_<FIELD> → i_self.<FIELD>

只对 TYPE 文件（001_TYPES.sql）做后处理。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# 复用 exception_map 的剥离工具
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rules.exception_map import (  # noqa: E402
    _strip_strings_and_comments,
    _restore_placeholders,
)


# 已知 TYPE 表（来自 P4-4 范围勘测）
_KNOWN_TYPES = ("SCORE_RECORD", "STUDENT_RECORD", "REF_CURSOR_T",
                "CONFIG_RECORD")

# SELF_<TYPE>_<FIELD> → i_self.<FIELD>
# TYPE 必须是已知 TYPE（避免误匹配普通变量名）
_SELF_REF_RE = re.compile(
    r"\bSELF_(" + "|".join(_KNOWN_TYPES) + r")_([A-Z][A-Z0-9_]*)\b"
)

# 函数签名匹配: GET_xxx_of_TYPE(ARGS) → 加 i_self TYPE 前缀
# 统一处理：ARGS 可为空（group 3 捕获空字符串）
_FN_SIG_WITH_ARGS_RE = re.compile(
    r"(CREATE\s+OR\s+REPLACE\s+FUNCTION\s+\w+_of_("
    + "|".join(_KNOWN_TYPES)
    + r")\s*)\(([^)]*)\)",
    re.IGNORECASE,
)


def rewrite_type_self(sql: str) -> str:
    """改写 TYPE 文件：SELF_<TYPE>_<FIELD> → i_self.<FIELD> + 加 i_self 参数。

    1. 一次扫描所有 GET_xxx_of_TYPE(ARGS) 签名：
       - ARGS 为空 → (i_self TYPE)
       - ARGS 非空 → (i_self TYPE, ARGS)
    2. 替换函数体 SELF_xxx。
    """
    stripped, restore = _strip_strings_and_comments(sql)

    # 1. 统一处理：所有已知 TYPE 函数签名都加 i_self 前缀
    def _replace_sig(m: re.Match) -> str:
        prefix = m.group(1)
        type_name = m.group(2).upper()
        args = m.group(3).strip()
        if args:
            return f"{prefix}(i_self {type_name}, {args})"
        return f"{prefix}(i_self {type_name})"

    out = _FN_SIG_WITH_ARGS_RE.sub(_replace_sig, stripped)

    # 2. 函数体 SELF_xxx 替换
    out = _SELF_REF_RE.sub(r"i_self.\2", out)

    return _restore_placeholders(out, restore)
