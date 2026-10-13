# AI Agent 大文件 PL/SQL 转换设计

**日期**: 2026-09-21
**状态**: 设计中
**技术栈**: Strands Agents SDK + Ollama

---

## 1. 背景与目标

### 1.1 问题描述

当前项目使用规则式 `plsql_converter.py` 转换 Oracle PL/SQL 到 PostgreSQL，但：
- **大文件处理能力有限**：单文件超过 1000 行时，上下文窗口溢出风险高
- **稳定性不足**：复杂嵌套结构可能导致转换失败
- **无法保证输出质量**：缺少自动验证机制

### 1.2 目标

1. **支持大文件转换**：通过分块处理 + 上下文管理，支持单文件数千行的转换
2. **保证稳定性**：
   - AI 转换失败时自动降级到规则式
   - 转换结果自动验证
   - 显式标记转换策略（AI 成功 / AI 失败+规则式兜底）
3. **可被 Java 直接调用**：输出格式符合 Java JDBC 调用规范

---

## 2. 技术方案

### 2.1 架构概览

```
┌─────────────────────────────────────────────────────────────────────┐
│  Stage4Generator (Pipeline 集成)                                    │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  Stage1 Metadata (已解析)                                     │   │
│  │  - AST 结构, chunk_hints, complexity_score                 │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                              ↓                                      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  ChunkPlanner (利用 Stage1 的 AST)                           │   │
│  │  - 按 BEGIN/END 对、嵌套层级分块                            │   │
│  │  - 确保关键结构完整（不切断 IF/LOOP/游标）                   │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                              ↓                                      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  PLSQLAgent (基于 Strands SDK)                               │   │
│  │                                                               │   │
│  │  Tools:                                                       │   │
│  │  ┌──────────────────┐  ┌──────────────────┐                │   │
│  │  │ convert_chunk    │  │ validate_pg      │                │   │
│  │  │ - LLM 单块转换   │  │ - PG 语法验证    │                │   │
│  │  │ - 重试机制      │  │ - 返回错误      │                │   │
│  │  └──────────────────┘  └──────────────────┘                │   │
│  │                                                               │   │
│  │  ┌──────────────────┐  ┌──────────────────┐                │   │
│  │  │ assemble_chunks  │  │ fallback_rules   │                │   │
│  │  │ - 合并分块结果  │  │ - 降级规则式    │                │   │
│  │  └──────────────────┘  └──────────────────┘                │   │
│  │                                                               │   │
│  │  重试策略: max_attempts=3, initial_delay=4s                 │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                              ↓                                      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  输出元数据                                                  │   │
│  │  {                                                           │   │
│  │    "strategy": "ai_agent" | "plsql_converter_fallback",   │   │
│  │    "chunks_count": 3,                                       │   │
│  │    "validation_passed": true,                              │   │
│  │    "fallback_used": false,                                 │   │
│  │    "retry_count": 0,                                        │   │
│  │    "error_message": null                                   │   │
│  │  }                                                           │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 核心组件

#### 2.2.1 PLSQLAgent

基于 Strands SDK 的 Agent，负责：
- 调用 LLM 进行分块转换
- 管理工具调用和重试
- 累积上下文（已转换的 chunks）

```python
from strands import Agent, tool
from strands.models import BedrockModel

# 工具定义
@tool
def convert_chunk(source: str, context: str = "") -> dict:
    """将一个 PL/SQL 代码块转换为 PostgreSQL"""
    ...

@tool
def validate_pg(code: str) -> dict:
    """验证 PostgreSQL 代码语法"""
    ...

# Agent 初始化
agent = Agent(
    model="ollama/qwen3.8:27b",  # Ollama 模型
    tools=[convert_chunk, validate_pg, assemble_chunks, fallback_rules],
    system_prompt=SYSTEM_PROMPT,
    retry_strategy=ModelRetryStrategy(max_attempts=3),
)
```

#### 2.2.2 ChunkPlanner

利用 Stage1 输出的 AST 进行智能分块：
- **分块策略**：按 BEGIN/END 配对、嵌套层级分界
- **边界保护**：不切断 IF/LOOP/CURSOR 等结构
- **重叠保留**：相邻 chunk 保留必要上下文

```python
class ChunkPlan:
    chunks: list[Chunk]
    total_lines: int
    max_chunk_size: int = 500  # 行数阈值

class Chunk:
    start_line: int
    end_line: int
    source: str
    context_before: str  # 前一个 chunk 的尾
    context_after: str    # 后一个 chunk 的头
    can_split: bool      # False=必须完整保留
```

#### 2.2.3 工具定义

| 工具 | 功能 | 重试 |
|------|------|------|
| `convert_chunk` | 调用 LLM 转换单个 chunk | ✅ |
| `validate_pg` | 语法验证（PG 执行 `psql --help` 或解析） | ❌ |
| `assemble_chunks` | 合并已验证的 chunks | ❌ |
| `fallback_rules` | 降级到规则式转换器 | ❌ |

---

## 3. 数据流

### 3.1 完整流程

```
1. Pipeline.run(file_path)
   │
2. Stage1 Parser → Metadata (含 AST, chunk_hints)
   │
3. Stage4 Generator
   │
   ├─→ ChunkPlanner.analyze(metadata)
   │      └→ ChunkPlan (分块方案)
   │
   ├─→ PLSQLAgent.run(ChunkPlan)
   │      │
   │      ├─→ Tool: convert_chunk × N (分块转换)
   │      │      └→ 重试机制 (最多 3 次)
   │      │
   │      ├─→ Tool: validate_pg (验证)
   │      │      └─→ 成功 → 继续
   │      │      └─→ 失败 → Tool: fallback_rules
   │      │
   │      └─→ Tool: assemble_chunks
   │             └→ 输出完整 PG 代码
   │
   └─→ 输出 {code, metadata, strategy}
```

### 3.2 降级策略

```
AI 转换失败 (重试 3 次后)
        │
        ↓
┌─────────────────────────┐
│ fallback_rules 工具      │
│ (调用 plsql_converter)  │
└─────────────────────────┘
        │
        ↓
输出 strategy = "plsql_converter_fallback"
     fallback_used = true
     error_message = "AI failed after 3 retries: <reason>"
```

---

## 4. 稳定性保障

### 4.1 显式标记

每次转换都返回完整的元数据，Java 端可据此判断：

```python
class ConversionResult:
    code: str                  # 转换后的 PG 代码
    strategy: str             # "ai_agent" | "plsql_converter_fallback"
    chunks_count: int         # 分块数量
    validation_passed: bool   # 是否通过验证
    fallback_used: bool        # 是否降级
    retry_count: int          # 重试次数
    error_message: str | None # 错误信息（如有）
    elapsed_ms: int           # 耗时
```

### 4.2 验证机制

| 验证级别 | 时机 | 失败处理 |
|----------|------|----------|
| 语法验证 | 每个 chunk 转换后 | 重试该 chunk |
| 组装验证 | chunks 合并后 | 整体重试 |
| 最终验证 | 输出前 | 降级规则式 |

### 4.3 重试机制

```python
retry_strategy=ModelRetryStrategy(
    max_attempts=3,
    initial_delay=4.0,    # 首次重试等待 4 秒
    max_delay=240.0,     # 最长等待 240 秒
    multiplier=2.0,      # 指数退避
)
```

---

## 5. Pipeline 返回格式

与当前 `Stage4Generator._format_output()` 输出格式保持一致，AI Agent 仅增强代码生成部分：

```python
# Stage4Generator 返回的格式
result = {
    "code": "...",           # 生成的 PG 代码
    "strategy": "ai_agent" | "plsql_converter_fallback",
    "complexity": 85,
    "is_simple": False,
    "chunks_count": 3,       # AI 分块数量
    "validation_passed": True,
    "fallback_used": False,
    "retry_count": 0,
    "error_message": None,
    "elapsed_ms": 1523,
    "warnings": [],
    "applied_rules": [],
}
```

### 5.1 关键差异

| 字段 | 规则式 | AI Agent |
|------|--------|----------|
| `strategy` | `plsql_converter` | `ai_agent` |
| `chunks_count` | 1 | N (分块数) |
| `is_simple` | True | False |
| `validation_passed` | 由 Stage5 决定 | 转换时初步验证 |

---

## 6. 文件结构

```
pipeline/
├── stages/
│   ├── stage4_generator.py      # 生成器主入口
│   └── stage4/
│       ├── __init__.py
│       ├── agent.py              # PLSQLAgent (Strands)
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── convert_chunk.py # 转换工具
│       │   ├── validate_pg.py   # 验证工具
│       │   ├── assemble.py      # 组装工具
│       │   └── fallback.py      # 降级工具
│       ├── chunk_planner.py     # 分块规划器
│       └── models.py            # 数据模型
└── ...
```

---

## 7. 依赖

```txt
# requirements.txt
strands-agents>=1.0.0
ollama>=0.1.0
psycopg2-binary>=2.9
```

---

## 8. 待确认事项

- [ ] Ollama 服务地址和认证方式
- [ ] 最大 chunk 大小阈值（行数，默认 500 行）
- [ ] 是否需要持久化转换历史（会话日志）

---

## 9. 参考

- [Strands Agents SDK](https://strandsagents.com/)
- [Strands @tool 装饰器](strands/tools/decorator.py)
- [Strands Agent 类](strands/agent/agent.py)
- [Strands ModelRetryStrategy](strands/event_loop/_retry.py)
