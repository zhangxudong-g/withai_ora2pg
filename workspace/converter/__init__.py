"""
workspace/converter —— 规则层新增转换能力

模块
----
- perlstyle_converter : 文件级转换包装（薄封装，转调真实引擎）
- compound_trigger    : P3-4 复合触发器（COMPOUND TRIGGER）结构化拆分

约定
----
**新能力一律落在 workspace/**，不改 ``pipeline/``（前期代码，0 处生产引用）。
``oracle_poc`` 的真实转换引擎仍是 ``ora2pg_py_perlstyle/perlstyle_engine.py``，
本目录的模块由引擎按需反向引入（延迟导入，不形成硬依赖）。
"""

from __future__ import annotations

__all__ = [
    "perlstyle_converter",
    "compound_trigger",
]
