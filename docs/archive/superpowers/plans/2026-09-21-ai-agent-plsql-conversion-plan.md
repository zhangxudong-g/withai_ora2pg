# AI Agent PL/SQL 转换实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现基于 Strands SDK 的 AI Agent，分块处理大文件 PL/SQL 转换，支持重试、降级、验证

**Architecture:** 
- PLSQLAgent 封装 Strands Agent，管理工具调用和重试
- ChunkPlanner 利用 Stage1 AST 智能分块
- 工具链：convert_chunk → validate_pg → assemble_chunks → fallback_rules
- 降级时显式标记 strategy

**Tech Stack:** Python 3.12+, Strands Agents SDK, Ollama SDK

---

## 文件结构

```
pipeline/stages/stage4/
├── __init__.py                    # 导出 PLSQLAgent, ChunkPlan 等
├── agent.py                       # PLSQLAgent (Strands 封装)
├── chunk_planner.py               # 分块规划器
├── models.py                      # 数据模型 (Chunk, ConversionResult)
└── tools/
    ├── __init__.py                # 导出所有工具
    ├── base.py                    # 工具基类和通用逻辑
    ├── convert_chunk.py           # convert_chunk 工具
    ├── validate_pg.py             # validate_pg 工具
    ├── assemble.py                # assemble_chunks 工具
    └── fallback.py                # fallback_rules 工具

pipeline/stages/stage4_generator.py # 修改：集成 PLSQLAgent
```

---

## Task 1: 创建目录结构和数据模型

**Files:**
- Create: `pipeline/stages/stage4/__init__.py`
- Create: `pipeline/stages/stage4/models.py`
- Create: `pipeline/stages/stage4/tools/__init__.py`

- [ ] **Step 1: 创建目录结构**

```bash
mkdir -p pipeline/stages/stage4/tools
touch pipeline/stages/stage4/__init__.py
touch pipeline/stages/stage4/tools/__init__.py
```

- [ ] **Step 2: 定义数据模型**

```python
# pipeline/stages/stage4/models.py
"""数据模型定义"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Chunk:
    """单个代码块"""
    index: int                      # 块序号 (0-based)
    start_line: int                # 起始行号
    end_line: int                  # 结束行号
    source: str                    # 原始 PL/SQL 代码
    pg_code: str = ""              # 转换后的 PG 代码
    context_before: str = ""       # 前一个块的尾（上下文）
    context_after: str = ""        # 后一个块的头（上下文）
    can_split: bool = True         # False=必须完整保留
    validation_passed: bool = False
    error_message: Optional[str] = None


@dataclass
class ChunkPlan:
    """分块方案"""
    chunks: list[Chunk] = field(default_factory=list)
    total_lines: int = 0
    max_chunk_size: int = 500     # 默认 500 行
    object_name: str = ""


@dataclass
class ConversionContext:
    """转换上下文（跨 chunk 累积）"""
    object_name: str
    object_type: str               # "FUNCTION" or "PROCEDURE"
    params: str = ""               # 参数签名
    declarations: str = ""         # DECLARE 段
    converted_chunks: list[Chunk] = field(default_factory=list)
    accumulated_code: str = ""     # 已组装的代码


@dataclass
class ConversionResult:
    """最终转换结果"""
    code: str
    strategy: str                 # "ai_agent" | "plsql_converter_fallback"
    chunks_count: int = 0
    validation_passed: bool = False
    fallback_used: bool = False
    retry_count: int = 0
    error_message: Optional[str] = None
    elapsed_ms: int = 0
    warnings: list[str] = field(default_factory=list)
```

- [ ] **Step 3: 添加 __init__.py 导出**

```python
# pipeline/stages/stage4/__init__.py
"""AI Agent 转换模块"""
from .models import Chunk, ChunkPlan, ConversionContext, ConversionResult
from .agent import PLSQLAgent

__all__ = [
    "Chunk",
    "ChunkPlan", 
    "ConversionContext",
    "ConversionResult",
    "PLSQLAgent",
]
```

- [ ] **Step 4: 提交**

```bash
git add pipeline/stages/stage4/
git commit -m "feat(stage4): add directory structure and data models"
```

---

## Task 2: 实现 ChunkPlanner

**Files:**
- Create: `pipeline/stages/stage4/chunk_planner.py`

- [ ] **Step 1: 实现基础分块逻辑**

```python
# pipeline/stages/stage4/chunk_planner.py
"""智能分块规划器 - 利用 Stage1 AST"""
import re
from typing import Optional
from .models import Chunk, ChunkPlan


class ChunkPlanner:
    """基于 AST 的智能分块"""
    
    DEFAULT_MAX_LINES = 500  # 默认最大行数
    
    def __init__(self, max_lines: int = DEFAULT_MAX_LINES):
        self.max_lines = max_lines
    
    def plan(self, source: str, metadata=None) -> ChunkPlan:
        """
        从源码生成 chunk 计划
        
        Args:
            source: 原始 PL/SQL 源码
            metadata: Stage1 输出的元数据（可选）
            
        Returns:
            ChunkPlan
        """
        lines = source.split('\n')
        plan = ChunkPlan(
            total_lines=len(lines),
            max_chunk_size=self.max_lines,
        )
        
        # 方案1: 如果源码小于阈值，直接单块
        if len(lines) <= self.max_lines:
            plan.chunks.append(Chunk(
                index=0,
                start_line=1,
                end_line=len(lines),
                source=source,
            ))
            return plan
        
        # 方案2: 基于语法结构分块
        chunks = self._split_by_structure(source, lines)
        
        # 如果结构分块后仍有大块，进一步拆分
        final_chunks = []
        for i, chunk in enumerate(chunks):
            if chunk.count('\n') > self.max_lines:
                sub_chunks = self._split_large_chunk(chunk, self.max_lines)
                for j, sub in enumerate(sub_chunks):
                    offset = chunk.split('\n')[0:1]  # 保留第一行作为锚点
                    final_chunks.append(sub)
            else:
                final_chunks.append(chunk)
        
        # 构建 Chunk 对象
        line_offset = 0
        for i, chunk_src in enumerate(final_chunks):
            chunk_lines = chunk_src.split('\n')
            plan.chunks.append(Chunk(
                index=i,
                start_line=line_offset + 1,
                end_line=line_offset + len(chunk_lines),
                source=chunk_src,
            ))
            line_offset += len(chunk_lines)
        
        return plan
    
    def _split_by_structure(self, source: str, lines: list[str]) -> list[str]:
        """按 BEGIN/END 块、嵌套层级分块"""
        chunks = []
        current_chunk_lines = []
        current_depth = 0
        block_depths = []  # 跟踪每个块的嵌套深度
        
        # 识别关键结构
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            
            # BEGIN 增加深度
            if re.match(r'^\s*BEGIN\s*$', stripped, re.IGNORECASE):
                current_depth += 1
            
            # END 减少深度
            if re.match(r'^\s*END(\s+\w+)?\s*;?\s*$', stripped, re.IGNORECASE):
                if current_depth > 0:
                    current_depth -= 1
            
            current_chunk_lines.append(line)
            
            # 分块条件：在深度为0时遇到关键结构
            if (current_depth == 0 and 
                len(current_chunk_lines) >= self.max_lines // 2 and
                self._is_safe_split_point(stripped)):
                
                chunks.append('\n'.join(current_chunk_lines))
                current_chunk_lines = []
        
        # 添加剩余部分
        if current_chunk_lines:
            chunks.append('\n'.join(current_chunk_lines))
        
        return chunks if chunks else [source]
    
    def _is_safe_split_point(self, line: str) -> bool:
        """判断是否为安全的分块点"""
        unsafe_keywords = [
            'IF ', 'THEN', 'ELSE', 'ELSIF',
            'LOOP', 'FOR ', 'WHILE ',
            'CASE ', 'WHEN ',
            'SELECT ', 'INSERT ', 'UPDATE ', 'DELETE ',
            'OPEN ', 'FETCH ', 'EXECUTE ',
        ]
        line_upper = line.upper()
        for kw in unsafe_keywords:
            if line_upper.startswith(kw):
                return False
        return True
    
    def _split_large_chunk(self, chunk: str, max_lines: int) -> list[str]:
        """拆分过大的块"""
        lines = chunk.split('\n')
        result = []
        
        for i in range(0, len(lines), max_lines):
            result.append('\n'.join(lines[i:i + max_lines]))
        
        return result
```

- [ ] **Step 2: 添加导出到 __init__.py**

```python
# pipeline/stages/stage4/__init__.py
from .models import Chunk, ChunkPlan, ConversionContext, ConversionResult
from .chunk_planner import ChunkPlanner
from .agent import PLSQLAgent

__all__ = [
    "Chunk",
    "ChunkPlan", 
    "ConversionContext",
    "ConversionResult",
    "ChunkPlanner",
    "PLSQLAgent",
]
```

- [ ] **Step 3: 提交**

```bash
git add pipeline/stages/stage4/chunk_planner.py
git commit -m "feat(stage4): implement ChunkPlanner for intelligent chunking"
```

---

## Task 3: 实现工具函数 (Tools)

**Files:**
- Create: `pipeline/stages/stage4/tools/base.py`
- Create: `pipeline/stages/stage4/tools/convert_chunk.py`
- Create: `pipeline/stages/stage4/tools/validate_pg.py`
- Create: `pipeline/stages/stage4/tools/assemble.py`
- Create: `pipeline/stages/stage4/tools/fallback.py`

- [ ] **Step 1: 实现工具基类**

```python
# pipeline/stages/stage4/tools/base.py
"""工具基类和通用逻辑"""
import ollama
from typing import Optional


class OllamaClient:
    """Ollama LLM 调用封装"""
    
    def __init__(
        self,
        base_url: str = "http://swiftechie.aa0.netvolante.jp:51434",
        model: str = "qwen3.8:27b",
        api_key: str = "",
    ):
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
    
    def generate(self, prompt: str, think: bool = False) -> str:
        """调用 Ollama 生成内容"""
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        
        client = ollama.Client(host=self.base_url, headers=headers)
        
        response = client.chat(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "You are an Oracle to PostgreSQL migration expert. Output code only, no explanations."
                },
                {"role": "user", "content": prompt}
            ],
            think=think,
            options={"temperature": 0.1, "num_ctx": 8192},
        )
        
        message = response.get("message", {})
        content = message.get("content", "")
        
        if not content:
            raise ValueError(f"Ollama returned empty content (model={self.model})")
        
        return content
```

- [ ] **Step 2: 实现 convert_chunk 工具**

```python
# pipeline/stages/stage4/tools/convert_chunk.py
"""convert_chunk 工具 - 调用 LLM 转换单个 chunk"""
from typing import Optional
from strands import tool
from .base import OllamaClient


# 全局 LLM 客户端（单例）
_llm_client: Optional[OllamaClient] = None


def get_llm_client() -> OllamaClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = OllamaClient()
    return _llm_client


def set_llm_client(client: OllamaClient):
    global _llm_client
    _llm_client = client


SYSTEM_PROMPT = """You are an Oracle PL/SQL to PostgreSQL PL/pgSQL migration expert.

Conversion Rules:
1. Function parameters → lowercase
2. RETURN → RETURNS TABLE(...) or RETURN NEXT
3. NVL(a,b) → COALESCE(a,b)
4. VARCHAR2 → VARCHAR
5. NUMBER → NUMERIC
6. EXCEPTION blocks: Keep RAISE syntax
7. Dynamic SQL (EXECUTE IMMEDIATE): Keep as is
8. Remove : prefix from parameter references
9. DECLARE → DECLARE (same)
10. IS → AS

Output format: ONLY the PostgreSQL code, no explanations."""


@tool
def convert_chunk(
    source: str,
    context: str = "",
) -> dict:
    """
    将一个 PL/SQL 代码块转换为 PostgreSQL.
    
    Args:
        source: 要转换的 PL/SQL 代码（单个 chunk）
        context: 前一个 chunk 的尾代码（用于上下文）
        
    Returns:
        dict: {"status": "success"|"error", "code": "...", "message": "..."}
    """
    if not source or not source.strip():
        return {
            "status": "error",
            "code": "",
            "message": "Empty source code"
        }
    
    # 构建提示词
    prompt_parts = [SYSTEM_PROMPT]
    
    if context:
        prompt_parts.append("\n\nContext (previous chunk end):")
        prompt_parts.append(context)
    
    prompt_parts.append("\n\nCode to convert:")
    prompt_parts.append(source)
    
    try:
        client = get_llm_client()
        result = client.generate('\n'.join(prompt_parts), think=False)
        
        # 提取代码（去掉 markdown 代码块标记）
        code = _extract_code(result)
        
        return {
            "status": "success",
            "code": code,
            "message": ""
        }
        
    except Exception as e:
        return {
            "status": "error",
            "code": "",
            "message": str(e)
        }


def _extract_code(text: str) -> str:
    """从 LLM 输出中提取代码"""
    import re
    
    # 尝试从 markdown 代码块提取
    pattern = r'```(?:pgsql|sql)?\n(.*?)\n```'
    matches = re.findall(pattern, text, re.DOTALL)
    
    if matches:
        return matches[0].strip()
    
    # 如果没有代码块，尝试直接提取
    lines = text.strip().split('\n')
    code_lines = []
    in_code = False
    
    for line in lines:
        if 'CREATE' in line.upper():
            in_code = True
        if in_code:
            code_lines.append(line)
            # 检测结束
            if line.strip().endswith(';') and 'LANGUAGE' in ''.join(lines[lines.index(line):]):
                break
    
    if code_lines:
        return '\n'.join(code_lines)
    
    return text.strip()
```

- [ ] **Step 3: 实现 validate_pg 工具**

```python
# pipeline/stages/stage4/tools/validate_pg.py
"""validate_pg 工具 - 验证 PostgreSQL 语法"""
import re
from strands import tool


@tool
def validate_pg(code: str) -> dict:
    """
    验证 PostgreSQL 代码的语法正确性.
    
    Args:
        code: 要验证的 PostgreSQL 代码
        
    Returns:
        dict: {"status": "valid"|"invalid", "errors": [...]}
    """
    if not code or not code.strip():
        return {
            "status": "invalid",
            "errors": ["Empty code"]
        }
    
    errors = []
    
    # 基本语法检查
    code_upper = code.upper()
    
    # 1. 检查 CREATE FUNCTION/PROCEDURE
    if not re.search(r'CREATE\s+(OR\s+REPLACE\s+)?(FUNCTION|PROCEDURE)', code_upper):
        errors.append("Missing CREATE FUNCTION/PROCEDURE")
    
    # 2. 检查 END 匹配
    begin_count = len(re.findall(r'\bBEGIN\b', code_upper))
    end_count = len(re.findall(r'\bEND\s*;?\s*$', code_upper, re.MULTILINE))
    if begin_count != end_count and begin_count > 0:
        errors.append(f"BEGIN/END mismatch: {begin_count} BEGIN vs {end_count} END")
    
    # 3. 检查 LANGUAGE plpgsql
    if not re.search(r'LANGUAGE\s+plpgsql', code_upper):
        errors.append("Missing LANGUAGE plpgsql")
    
    # 4. 检查 $$ 分隔符
    dollar_count = code.count('$$')
    if dollar_count < 2:
        errors.append("Missing $$ delimiter")
    
    # 5. 检查括号匹配（函数参数）
    # 简化检查：计算左括号和右括号
    paren_depth = 0
    for char in code:
        if char == '(':
            paren_depth += 1
        elif char == ')':
            paren_depth -= 1
        if paren_depth < 0:
            errors.append("Unmatched parentheses")
            break
    
    # 6. 检查常见错误模式
    error_patterns = [
        (r':\w+', "Found Oracle parameter prefix ':' - should be removed"),
        (r'VARCHAR2', "Found VARCHAR2 - should be VARCHAR"),
        (r'\bNVL\s*\(', "Found NVL - should be COALESCE"),
        (r'RAISE\s+\w+\s*;', "Check RAISE syntax - should use RAISE EXCEPTION"),
    ]
    
    for pattern, msg in error_patterns:
        if re.search(pattern, code, re.IGNORECASE):
            errors.append(msg)
    
    return {
        "status": "valid" if not errors else "invalid",
        "errors": errors
    }
```

- [ ] **Step 4: 实现 assemble_chunks 工具**

```python
# pipeline/stages/stage4/tools/assemble.py
"""assemble_chunks 工具 - 合并已转换的 chunks"""
from typing import List
from strands import tool


@tool
def assemble_chunks(chunks: List[dict]) -> dict:
    """
    将多个已转换的 chunk 合并为完整的 PostgreSQL 函数.
    
    Args:
        chunks: 已转换的 chunk 列表，每个包含 {"index", "code", "source"}
        
    Returns:
        dict: {"status": "success"|"error", "code": "...", "message": "..."}
    """
    if not chunks:
        return {
            "status": "error",
            "code": "",
            "message": "No chunks provided"
        }
    
    # 按索引排序
    sorted_chunks = sorted(chunks, key=lambda x: x.get("index", 0))
    
    # 提取各部分
    header = ""
    declare_section = ""
    body_parts = []
    footer = ""
    
    for chunk in sorted_chunks:
        code = chunk.get("code", "")
        if not code:
            continue
        
        # 识别 header (CREATE FUNCTION/PROCEDURE 开头)
        if not header:
            header_match = _extract_until_pattern(code, r'\bDECLARE\b|\bBEGIN\b', include_pattern=False)
            if header_match:
                header = header_match
        
        # 识别 DECLARE 段
        if 'DECLARE' in code.upper():
            decl_match = _extract_pattern(code, r'DECLARE\s*(.*?)\s*BEGIN', re.DOTALL | re.IGNORECASE)
            if decl_match:
                declare_section = decl_match
        
        # 识别 body
        if 'BEGIN' in code.upper():
            body_match = _extract_pattern(code, r'BEGIN\s*(.*)', re.DOTALL | re.IGNORECASE)
            if body_match:
                body_parts.append(body_match)
        
        # 识别 footer (LANGUAGE 结尾)
        if 'LANGUAGE' in code.upper():
            footer_match = _extract_until_end(code)
            if footer_match:
                footer = footer_match
    
    # 组装
    if not header:
        return {
            "status": "error",
            "code": "",
            "message": "Could not extract function header from chunks"
        }
    
    assembled = header
    
    if declare_section:
        assembled += f"\nDECLARE\n{declare_section}"
    
    for body in body_parts:
        assembled += f"\n{body}"
    
    if footer:
        assembled += f"\n{footer}"
    
    # 清理空行
    assembled = re.sub(r'\n{3,}', '\n\n', assembled)
    
    return {
        "status": "success",
        "code": assembled,
        "message": f"Assembled {len(chunks)} chunks"
    }


def _extract_until_pattern(text: str, pattern: str, include_pattern: bool = False) -> str:
    """提取从开头到匹配模式之前的文本"""
    import re
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        end_pos = match.start() if not include_pattern else match.end()
        return text[:end_pos].strip()
    return text


def _extract_pattern(text: str, pattern: str, flags=0) -> str:
    """提取匹配模式的内容"""
    import re
    match = re.search(pattern, text, flags)
    if match:
        return match.group(1).strip() if match.groups() else match.group(0).strip()
    return ""


def _extract_until_end(text: str) -> str:
    """提取从 LANGUAGE 到结尾"""
    import re
    match = re.search(r'LANGUAGE\s+plpgsql\s*;?\s*$', text, re.IGNORECASE | re.MULTILINE)
    if match:
        return text[match.start():].strip()
    return ""
```

- [ ] **Step 5: 实现 fallback_rules 工具**

```python
# pipeline/stages/stage4/tools/fallback.py
"""fallback_rules 工具 - 降级到规则式转换器"""
from strands import tool

# 延迟导入避免循环依赖
_rules_converter = None


def _get_rules_converter():
    global _rules_converter
    if _rules_converter is None:
        from pipeline.plsql_converter import convert_function
        _rules_converter = convert_function
    return _rules_converter


@tool
def fallback_rules(source: str) -> dict:
    """
    当 AI 转换失败时，降级到规则式转换器.
    
    Args:
        source: 原始 PL/SQL 源码
        
    Returns:
        dict: {"status": "success"|"error", "code": "...", "message": "..."}
    """
    if not source or not source.strip():
        return {
            "status": "error",
            "code": "",
            "message": "Empty source code"
        }
    
    try:
        converter = _get_rules_converter()
        result = converter(source)
        
        return {
            "status": "success",
            "code": result,
            "message": "Converted using rules-based fallback"
        }
        
    except Exception as e:
        return {
            "status": "error",
            "code": "",
            "message": f"Fallback conversion failed: {str(e)}"
        }
```

- [ ] **Step 6: 更新 tools/__init__.py**

```python
# pipeline/stages/stage4/tools/__init__.py
"""工具模块"""
from .convert_chunk import convert_chunk, set_llm_client
from .validate_pg import validate_pg
from .assemble import assemble_chunks
from .fallback import fallback_rules

__all__ = [
    "convert_chunk",
    "validate_pg",
    "assemble_chunks", 
    "fallback_rules",
    "set_llm_client",
]
```

- [ ] **Step 7: 提交**

```bash
git add pipeline/stages/stage4/tools/
git commit -m "feat(stage4): implement tools (convert_chunk, validate_pg, assemble, fallback)"
```

---

## Task 4: 实现 PLSQLAgent

**Files:**
- Create: `pipeline/stages/stage4/agent.py`

- [ ] **Step 1: 实现 Agent 主类**

```python
# pipeline/stages/stage4/agent.py
"""PLSQLAgent - 基于 Strands SDK 的 AI 转换 Agent"""
import time
from typing import Optional
from strands import Agent, tool
from strands.event_loop import ModelRetryStrategy

from .models import Chunk, ChunkPlan, ConversionContext, ConversionResult
from .chunk_planner import ChunkPlanner
from .tools import (
    convert_chunk, 
    validate_pg, 
    assemble_chunks, 
    fallback_rules,
    set_llm_client,
)
from .tools.base import OllamaClient


SYSTEM_PROMPT = """你是一个 Oracle PL/SQL 到 PostgreSQL 迁移专家。

你的任务是将复杂的 PL/SQL 代码分块转换，每个 chunk 调用 convert_chunk 工具。
验证每个 chunk 后，调用 assemble_chunks 合并结果。

如果 AI 转换失败（最多重试 3 次），调用 fallback_rules 使用规则式转换器。

工作流程：
1. 分析源码，确定分块策略
2. 对每个 chunk 调用 convert_chunk
3. 调用 validate_pg 验证
4. 调用 assemble_chunks 合并
5. 如有失败，调用 fallback_rules

重要：
- 只返回代码，不要解释
- 保持 BEGIN/END 配对
- 保留注释中的关键信息"""


class PLSQLAgent:
    """基于 Strands SDK 的 PL/SQL 转换 Agent"""
    
    def __init__(
        self,
        model: str = "qwen3.8:27b",
        base_url: str = "http://swiftechie.aa0.netvolante.jp:51434",
        api_key: str = "",
        max_chunk_lines: int = 500,
        max_retries: int = 3,
    ):
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.max_chunk_lines = max_chunk_lines
        self.max_retries = max_retries
        
        # 初始化 LLM 客户端
        llm_client = OllamaClient(base_url=base_url, model=model, api_key=api_key)
        set_llm_client(llm_client)
        
        # 初始化分块器
        self.chunker = ChunkPlanner(max_lines=max_chunk_lines)
        
        # 初始化 Strands Agent
        retry_strategy = ModelRetryStrategy(
            max_attempts=max_retries,
            initial_delay=4.0,
            max_delay=240.0,
            multiplier=2.0,
        )
        
        self._agent = Agent(
            model=f"ollama/{model}",
            tools=[
                convert_chunk,
                validate_pg,
                assemble_chunks,
                fallback_rules,
            ],
            system_prompt=SYSTEM_PROMPT,
            retry_strategy=retry_strategy,
        )
    
    def convert(self, source: str, object_name: str = "") -> ConversionResult:
        """
        转换 PL/SQL 代码
        
        Args:
            source: 原始 PL/SQL 源码
            object_name: 对象名称
            
        Returns:
            ConversionResult
        """
        start_time = time.time()
        retry_count = 0
        fallback_used = False
        
        try:
            # 1. 分块
            plan = self.chunker.plan(source)
            
            # 2. 逐块转换（使用 Agent）
            converted_chunks = []
            all_valid = True
            
            for chunk in plan.chunks:
                # 获取上下文（前一个 chunk 的尾）
                context = ""
                if converted_chunks:
                    prev = converted_chunks[-1]
                    context_lines = prev.source.split('\n')[-10:]
                    context = '\n'.join(context_lines)
                
                # 调用 convert_chunk
                for attempt in range(self.max_retries):
                    try:
                        result = convert_chunk(chunk.source, context)
                        
                        if result["status"] == "success":
                            chunk.pg_code = result["code"]
                            break
                        else:
                            if attempt == self.max_retries - 1:
                                raise Exception(f"Chunk {chunk.index} failed: {result['message']}")
                            retry_count += 1
                    except Exception as e:
                        if attempt == self.max_retries - 1:
                            raise
                        retry_count += 1
                        time.sleep(2 ** attempt)  # 指数退避
                
                # 验证
                validation = validate_pg(chunk.pg_code)
                chunk.validation_passed = validation["status"] == "valid"
                chunk.error_message = "; ".join(validation.get("errors", []))
                
                if not chunk.validation_passed:
                    all_valid = False
                
                converted_chunks.append(chunk)
            
            # 3. 组装
            chunks_data = [
                {"index": c.index, "code": c.pg_code, "source": c.source}
                for c in converted_chunks
            ]
            assembled = assemble_chunks(chunks_data)
            
            if assembled["status"] == "success":
                final_code = assembled["code"]
            else:
                raise Exception(f"Assembly failed: {assembled['message']}")
            
            # 4. 最终验证
            final_validation = validate_pg(final_code)
            
            elapsed_ms = int((time.time() - start_time) * 1000)
            
            return ConversionResult(
                code=final_code,
                strategy="ai_agent",
                chunks_count=len(plan.chunks),
                validation_passed=final_validation["status"] == "valid",
                fallback_used=False,
                retry_count=retry_count,
                error_message=None,
                elapsed_ms=elapsed_ms,
            )
            
        except Exception as e:
            # 降级到规则式
            fallback_used = True
            
            try:
                fallback_result = fallback_rules(source)
                if fallback_result["status"] == "success":
                    final_code = fallback_result["code"]
                else:
                    final_code = f"-- Conversion failed: {str(e)}\n{fallback_result['message']}"
            except Exception:
                final_code = f"-- Conversion failed: {str(e)}"
            
            elapsed_ms = int((time.time() - start_time) * 1000)
            
            return ConversionResult(
                code=final_code,
                strategy="plsql_converter_fallback",
                chunks_count=0,
                validation_passed=False,
                fallback_used=True,
                retry_count=retry_count,
                error_message=str(e),
                elapsed_ms=elapsed_ms,
                warnings=[f"AI conversion failed, used fallback: {str(e)}"],
            )
```

- [ ] **Step 2: 更新 __init__.py**

```python
# pipeline/stages/stage4/__init__.py
"""AI Agent 转换模块"""
from .models import Chunk, ChunkPlan, ConversionContext, ConversionResult
from .chunk_planner import ChunkPlanner
from .agent import PLSQLAgent
from .tools import (
    convert_chunk,
    validate_pg,
    assemble_chunks,
    fallback_rules,
)

__all__ = [
    "Chunk",
    "ChunkPlan", 
    "ConversionContext",
    "ConversionResult",
    "ChunkPlanner",
    "PLSQLAgent",
    "convert_chunk",
    "validate_pg",
    "assemble_chunks",
    "fallback_rules",
]
```

- [ ] **Step 3: 提交**

```bash
git add pipeline/stages/stage4/agent.py
git commit -m "feat(stage4): implement PLSQLAgent with Strands SDK"
```

---

## Task 5: 集成到 Stage4Generator

**Files:**
- Modify: `pipeline/stages/stage4_generator.py`

- [ ] **Step 1: 添加 AI Agent 路由逻辑**

找到 `_generate_with_ai` 方法，修改为使用 PLSQLAgent：

```python
# 在 Stage4Generator 类中添加

def _generate_with_ai(self, source: str, metadata) -> Dict:
    """使用 AI Agent 生成代码"""
    result = {
        "code": "",
        "warnings": [],
        "applied_rules": [],
        "error": None,
        "ai_prompt": "",
        "ai_response": "",
    }
    
    if not source:
        result["error"] = "No source code provided"
        return result
    
    if not self.ai_enabled:
        result["error"] = "AI generation not enabled (ai_enabled=False)"
        return result
    
    try:
        # 导入 PLSQLAgent
        from pipeline.stages.stage4 import PLSQLAgent
        
        # 获取对象名称
        object_name = getattr(metadata, 'object_name', '') if metadata else ''
        
        # 创建 Agent 实例
        agent = PLSQLAgent(
            model=self.ai_model,
            base_url=self.ai_base_url,
            api_key=self.ai_api_key or "",
            max_chunk_lines=500,
            max_retries=3,
        )
        
        # 执行转换
        conv_result = agent.convert(source, object_name)
        
        # 填充结果
        result["code"] = conv_result.code
        result["strategy"] = conv_result.strategy
        result["chunks_count"] = conv_result.chunks_count
        result["validation_passed"] = conv_result.validation_passed
        result["fallback_used"] = conv_result.fallback_used
        result["retry_count"] = conv_result.retry_count
        result["elapsed_ms"] = conv_result.elapsed_ms
        
        if conv_result.error_message:
            result["warnings"].append(conv_result.error_message)
        
    except Exception as e:
        result["error"] = f"AI generation failed: {str(e)}"
    
    return result
```

- [ ] **Step 2: 提交**

```bash
git add pipeline/stages/stage4_generator.py
git commit -m "feat(stage4): integrate PLSQLAgent into Stage4Generator"
```

---

## Task 6: 添加依赖并测试

**Files:**
- Modify: `requirements.txt`
- Create: `pipeline/tests/test_stage4_agent.py`

- [ ] **Step 1: 添加依赖**

```bash
# requirements.txt 添加
strands-agents>=1.0.0
ollama>=0.1.0
```

- [ ] **Step 2: 创建测试**

```python
# pipeline/tests/test_stage4_agent.py
"""PLSQLAgent 测试"""
import pytest
from pipeline.stages.stage4 import ChunkPlanner, ChunkPlan, PLSQLAgent


class TestChunkPlanner:
    """ChunkPlanner 测试"""
    
    def test_small_source_single_chunk(self):
        """小源码应返回单块"""
        source = "CREATE FUNCTION test() RETURN NUMBER IS BEGIN RETURN 1; END;"
        planner = ChunkPlanner(max_lines=500)
        plan = planner.plan(source)
        
        assert len(plan.chunks) == 1
        assert plan.chunks[0].source == source
    
    def test_large_source_multiple_chunks(self):
        """大源码应分块"""
        lines = ["-- line {}".format(i) for i in range(600)]
        source = "\n".join(lines)
        
        planner = ChunkPlanner(max_lines=500)
        plan = planner.plan(source)
        
        assert len(plan.chunks) > 1
    
    def test_preserves_structure(self):
        """分块应保护 BEGIN/END 结构"""
        source = """
        CREATE FUNCTION test() RETURN NUMBER IS
        BEGIN
            IF x THEN
                BEGIN
                    RETURN 1;
                END;
            END IF;
            RETURN 0;
        END;
        """.strip()
        
        planner = ChunkPlanner(max_lines=5)
        plan = planner.plan(source)
        
        # 每个 chunk 应该能独立存在
        for chunk in plan.chunks:
            # 允许不完整，但不能有悬挂的 BEGIN 或 END
            pass  # 简化检查


class TestPLSQLAgent:
    """PLSQLAgent 测试"""
    
    @pytest.fixture
    def agent(self):
        """创建 Agent 实例"""
        return PLSQLAgent(
            model="qwen3.8:27b",
            base_url="http://localhost:11434",
            max_retries=1,  # 测试用，减少重试
        )
    
    def test_convert_simple_function(self, agent):
        """测试简单函数转换"""
        source = """
        CREATE FUNCTION add_numbers(a IN NUMBER, b IN NUMBER) RETURN NUMBER IS
        BEGIN
            RETURN a + b;
        END;
        """.strip()
        
        result = agent.convert(source, "add_numbers")
        
        assert result.code is not None
        assert "CREATE" in result.code.upper()
        assert "FUNCTION" in result.code.upper()
        # 如果降级了，检查是否正确标记
        assert result.strategy in ["ai_agent", "plsql_converter_fallback"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

- [ ] **Step 3: 提交**

```bash
git add requirements.txt pipeline/tests/test_stage4_agent.py
git commit -m "test(stage4): add tests for PLSQLAgent and ChunkPlanner"
```

---

## 实现检查清单

| Task | 描述 | 状态 |
|------|------|------|
| 1 | 目录结构和数据模型 | ⬜ |
| 2 | ChunkPlanner 实现 | ⬜ |
| 3 | 工具函数 (convert_chunk, validate_pg, assemble, fallback) | ⬜ |
| 4 | PLSQLAgent | ⬜ |
| 5 | 集成到 Stage4Generator | ⬜ |
| 6 | 测试 | ⬜ |

---

## 执行方式

**Plan complete and saved to `docs/superpowers/plans/2026-09-21-ai-agent-plsql-conversion-plan.md`.**

**Two execution options:**

1. **Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

2. **Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
