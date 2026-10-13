"""workspace/agent/prompt_builder.py

P4-1 LLM prompt 构造。

参考 pipeline/stages/stage3_ai/tools/convert_chunk.py 的 SYSTEM_PROMPT，
但**重写 + 简化**：
- 中文适配（项目主要中文）
- 注入 28 处 TODO 桩的特征（INLINED / SELF_xxx / EXECUTE IMMEDIATE）
- 强制 JSON 输出（避免自由文本解析崩）

设计原则：
- SYSTEM_PROMPT 与用户 prompt 分离（与 pipeline 一致）
- 关键约束放 SYSTEM（避免被长 prompt 冲掉）
- 单 chunk 输入 → 单 chunk 输出（避免 token 超限）
"""
from __future__ import annotations


SYSTEM_PROMPT = """你是一个 Oracle PL/SQL → PostgreSQL PL/pgSQL 转换专家。

## 严格约束（不遵守将被识别为失败）

1. **只输出 PG 代码**，不要任何解释、注释、Markdown
2. 第一行必须是 `CREATE OR REPLACE FUNCTION` 或 `CREATE OR REPLACE PROCEDURE`
3. 最后一行必须是 `$$;`
5. 倒数第二行必须是 `LANGUAGE plpgsql;`
6. 完整函数体，不要截断
7. 函数参数/返回值/MUSTODY 对齐 chunk 输入

## Oracle → PG 关键映射

| Oracle | PG |
|---|---|
| `NUMBER` | `DECIMAL` 或 `BIGINT` |
| `VARCHAR2` | `VARCHAR` |
| `DATE` | `TIMESTAMP` |
| `NVL(a,b)` | `COALESCE(a,b)` |
| `SYSDATE` | `CURRENT_TIMESTAMP` |
| `DBMS_OUTPUT.PUT_LINE(...)` | `RAISE NOTICE ...` |
| `EXECUTE IMMEDIATE 'sql'` | `EXECUTE 'sql'` |
| `OUT` 参数 | 函数多返回值 / 复合类型 |
| `IN OUT` | `INOUT` |
| `TABLE(...).COLUMN%TYPE` | 查表展开 → PG 类型 |
| `SELF.MEMBER` | PG 中没有 SELF；改用独立函数 + 参数传递 |
| `RAISE_APPLICATION_ERROR(-20001, msg)` | `RAISE EXCEPTION '-20001: %', msg` |

## Cursor / Loop 转换规则（Oracle → PL/pgSQL）

| Oracle | PG 正确写法 | 常见错误（禁止）|
|---|---|---|
| `CURSOR c IS SELECT ...;` | `c REFCURSOR;` 或隐式 `FOR rec IN SELECT ... LOOP` | 不要写裸 `LOOP` 无 cursor |
| `OPEN c; FETCH c INTO v; EXIT WHEN c%NOTFOUND;` | `FOR rec IN SELECT ... LOOP` 或 `FETCH c INTO v; EXIT WHEN NOT FOUND;` | 不要写 `FOR v_cursor LOOP`（这不是 PG 语法）|
| `FOR v_cursor LOOP`（Oracle cursor FOR loop） | `FOR rec IN SELECT ... LOOP` | **禁止写裸 `LOOP`** |
| `CLOSE c;` | 不需要（PG FOR 自动关） | 不需要显式 CLOSE |
| `%ISOPEN` | 不需要（PG FOR 自动管理） | 不要写 `v_cursor%ISOPEN` |

**关键**：如果你要遍历查询结果，用这个模式：
```plpgsql
FOR rec IN SELECT col1, col2 FROM table WHERE ...
LOOP
  -- 用 rec.col1 访问
END LOOP;
```

## 当前产物特征（针对 oracle_poc）

转换产物里有 28 处 TODO 占位 + 8 处 INLINED 残骸 + 6 处 SELF_xxx 伪标识符。
你的工作是把这些**占位实现成真实可调的 PL/pgSQL 函数体**。

## 必须失败提示

如果 chunk 内容无法转换（如严重截断、无函数声明），输出：
```
-- AGENT_FAILED: <原因>
CREATE OR REPLACE FUNCTION _invalid_placeholder() RETURNS void
LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'AGENT_FAILED: <reason>'; END; $$;
```
让 validate_pg 抓到失败。

---

## 输入 chunk
"""


USER_PROMPT_TEMPLATE = """## 前置上下文（上一个函数末尾）
{context}

## 本 chunk 源码
{source}

请只输出 PG PL/pgSQL 函数/过程代码。"""


def build_user_prompt(source: str, context: str = "", schema_context: str = "") -> str:
    """构造用户 prompt（context 可为空字符串表示首个 chunk）。

    Args:
        source: 要转换的函数体
        context: 前一个 chunk 的尾部代码
        schema_context: 可用函数字典（来自 schema_dict.build_schema_context）
    """
    parts = [USER_PROMPT_TEMPLATE.format(context=context or "（无）", source=source)]
    if schema_context:
        parts.append("\n" + schema_context)
    return "\n\n".join(parts)


def build_full_prompt(source: str, context: str = "", schema_context: str = "") -> str:
    """构造完整 prompt（SYSTEM + USER），传给 llm_client.generate。"""
    return SYSTEM_PROMPT + "\n\n" + build_user_prompt(source, context, schema_context)


def extract_code(llm_output: str) -> str:
    """从 LLM 输出提取 PG 代码（处理 ```pgsql ... ``` 围栏）。"""
    text = llm_output.strip()

    # 移除 markdown ```pgsql ... ``` 围栏（含首尾）
    import re
    # 先找围栏
    pattern = r"```(?:pgsql|sql)?\s*\n(.*?)\n```"
    matches = re.findall(pattern, text, re.DOTALL)
    if matches:
        return matches[0].strip()

    # 没有完整围栏，清理残留的 ```
    text = re.sub(r"```(?:pgsql|sql)?\s*", "", text)
    text = re.sub(r"\s*```", "", text)
    return text.strip()


__all__ = ["SYSTEM_PROMPT", "build_user_prompt", "build_full_prompt", "extract_code"]