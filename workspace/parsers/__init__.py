"""
workspace/parsers —— AST 层（只读校验，不参与产物生成）

定位
----
给转换器做"结构体检"：数对象、看嵌套深度、查空壳桩、提元数据。
**不参与产物生成** —— ANTLR 解析比规则引擎慢约 2500 倍
（4.7ms → 11700ms/文件），进主路径会拖垮日常转换。

模块
----
- grammar_adapter : P3-1  ANTLR 适配器，统一异常处理 + 降级策略
- structure_check : P3-2  对象数 / 嵌套深度 / 空壳桩 / %TYPE 溯源
- metadata        : P3-3  触发器 / 类型 / 函数元数据提取
- grammar/         ANTLR 4.13.2 生成物副本（禁止手工编辑，见其 README.md）

用法
----
    from parsers.metadata import extract_all
    res = extract_all(open("oracle_poc/01.SQL/TRIGGER/00_TRIGGER.sql",
                          encoding="utf-8").read())
    print(res.summary())
"""

from __future__ import annotations

__all__ = [
    "grammar_adapter",
    "structure_check",
    "metadata",
]
