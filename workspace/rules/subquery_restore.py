"""
P2-1: %SUBQUERY%d% 占位符回填

设计：
- 抽取时（plsql_subs.extract_subqueries）将 (SELECT|WITH ...) 整体替换为 %SUBQUERY<n>%，
  原 SQL 缓存到 state.subquery_map[n]。
- 所有转换完成后，由本模块负责把占位符还原为原始子查询文本。
- 严格策略：占位符出现但 map 中无对应条目 → 抛 SubqueryRestoreError，
  **绝不静默跳过**（与 AGENTS.md §2 数据丢失零容忍一致）。

实现要点：
- 用 dict[int, str] 索引 (从 0 开始递增)
- 一次扫描，列出所有缺失 idx，一次性报告（不首次缺失即抛）
- 还原文本用 lambda 闭包替换，避免正则反向引用带来的转义问题
"""

from __future__ import annotations

import re
from typing import Iterable, List, Mapping, Tuple, Union


class SubqueryRestoreError(RuntimeError):
    """占位符出现在 SQL 中但 state.subquery_map 缺对应条目。
    这种情况 = 数据丢失，绝不静默。"""


# 严格匹配 %SUBQUERY%d%（忽略大小写）
_PLACEHOLDER_RE = re.compile(r"%SUBQUERY(\d+)%", re.IGNORECASE)


def _coerce_map(subquery_map) -> dict:
    """把 state.subquery_map 统一成 dict[int, str]，便于后续查找。"""
    if subquery_map is None:
        return {}
    # ConversionState.subquery_map: Dict[str, str]（perlstyle_engine.py:61）
    # 也兼容 plsql_subs 的 Dict[int, str]
    return {int(k): v for k, v in subquery_map.items()}


def restore_subqueries(
    sql: str,
    state: Union[Mapping, object],
) -> Tuple[str, List[int]]:
    """把 SQL 中所有 %SUBQUERY<n>% 还原为 state.subquery_map[n] 缓存的原文。

    Args:
        sql: 转换流水线最终产出的 SQL
        state: ConversionState 或任意具有 subquery_map 属性的对象
               （duck-typed：直接取 state.subquery_map）

    Returns:
        (restored_sql, used_indices)
        - restored_sql: 还原后的 SQL
        - used_indices: 实际还原的 idx 列表（去重，按出现顺序）

    Raises:
        SubqueryRestoreError: 任意一个占位符 idx 不在 map 中（数据丢失，零容忍）
    """
    # duck-type 拿 map（兼容 dict 直接传入 / 对象带 subquery_map / ConversionState）
    if hasattr(state, "subquery_map"):
        m = _coerce_map(state.subquery_map)
    elif isinstance(state, Mapping):
        m = _coerce_map(state)
    else:
        raise TypeError(
            f"state must expose .subquery_map or be a Mapping; got {type(state).__name__}"
        )

    # 先扫一遍找出所有出现的 idx；缺失则一次性报告
    found: List[int] = [int(x) for x in _PLACEHOLDER_RE.findall(sql)]
    if found:
        missing = sorted({i for i in found if i not in m})
        if missing:
            preview = _extract_context(sql, found)
            raise SubqueryRestoreError(
                "%SUBQUERY%d% 占位符出现但 state.subquery_map 缺对应条目，"
                "数据丢失零容忍，禁止静默跳过。\n"
                f"  缺失 idx: {missing}\n"
                f"  出现位置（每处一行）:\n{preview}"
            )

    # 全部就位，逐个替换（用 lambda 避免 body 中的 \\1 等反向引用被 re 解析）
    used: List[int] = []
    seen: set = set()
    def _sub(match: re.Match) -> str:
        idx = int(match.group(1))
        used.append(idx)
        seen.add(idx)
        return m[idx]

    restored = _PLACEHOLDER_RE.sub(_sub, sql)
    return restored, used


def _extract_context(sql: str, indices: Iterable[int]) -> str:
    """为错误消息提供每个占位符的上下文（前 40 字符 / 后 40 字符）"""
    lines = []
    for m in _PLACEHOLDER_RE.finditer(sql):
        start = max(0, m.start() - 40)
        end = min(len(sql), m.end() + 40)
        ctx = sql[start:end].replace("\n", " ")
        lines.append(f"    ...{ctx}...  (idx={m.group(1)})")
    return "\n".join(lines) if lines else "    (无)"
