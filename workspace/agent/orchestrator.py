"""workspace/agent/orchestrator.py

P4-1 主流程编排：分块 → Agent 转换 → 校验 → 替换 → 试装。

设计原则（实施路径 §P4-1 护栏）：
1. validate_pg 硬校验 + 不过必留 `RAISE EXCEPTION 'not implemented'`（不静默 NULL）
2. 每批前 Snapshot → 可回滚
3. 配额上限 200 次（quota.check 门控）
4. 失败兜底：保留原代码块 + 占位 RAISE EXCEPTION

工作流（单 chunk）：
    1. snapshot.create() → SHA
    2. prompt_builder.build_full_prompt(chunk.code, context)
    3. llm_client.generate(prompt) → llm_output
    4. prompt_builder.extract_code(llm_output) → new_code
    5. validate_pg(new_code, trial_install=True) → ValidationResult
    6. if result.ok: 用 new_code 替换原 chunk
       else: snapshot.restore(SHA) + 用 RAISE EXCEPTION 占位替换

注：本任务**只建基础设施**（不接主路径）；orchestrator 可单独跑测试。
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .llm_client import OllamaClient, LLMError
from .quota import QuotaExceeded, get_quota
from .validate_pg import validate_pg, ValidationResult, ValidationError
from .chunker import chunk_function_or_procedure, Chunk, ChunkError
from .prompt_builder import build_full_prompt, extract_code
from .snapshot import Snapshot


PLACEHOLDER_FAILED = """-- AGENT_FAILED: validation error
-- {errors}
CREATE OR REPLACE FUNCTION {name}() RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'not implemented: %', '{errors}';
END;
$$;
"""


@dataclass
class BatchResult:
    """单批转换结果。"""
    object_name: str
    object_kind: str
    success: bool
    new_code: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    quota_used: int = 0


class Orchestrator:
    """编排单 chunk → LLM → 校验 → 替换的主流程。"""

    def __init__(
        self,
        llm: Optional[OllamaClient] = None,
        snapshot: Optional[Snapshot] = None,
        validate_kwargs: Optional[dict] = None,
        schema_context: str = "",
    ):
        self.llm = llm or OllamaClient()
        self.snapshot = snapshot or Snapshot()
        self.validate_kwargs = validate_kwargs or {}
        self.schema_context = schema_context

    def process_chunk(self, chunk: Chunk) -> BatchResult:
        """处理单个 Chunk：LLM 重写 + 校验 + 失败回滚 + 占位回灌。

        不修改源文件；只返 BatchResult（让调用方决定如何落盘）。
        """
        # 1. 配额门控（提前抛，避免无谓调用）
        q = get_quota()
        if not q.check():
            return BatchResult(
                object_name=chunk.object_name,
                object_kind=chunk.object_kind,
                success=False,
                new_code=_make_placeholder(
                    chunk.object_name,
                    f"quota exceeded ({q.used()}/{q.max_calls})",
                ),
                errors=[f"quota exceeded ({q.used()}/{q.max_calls})"],
            )

        # 2. 调 LLM
        try:
            prompt = build_full_prompt(chunk.code, chunk.context_before, schema_context=self.schema_context)
            llm_output = self.llm.generate(prompt)
        except QuotaExceeded as e:
            return BatchResult(
                object_name=chunk.object_name,
                object_kind=chunk.object_kind,
                success=False,
                new_code=_make_placeholder(chunk.object_name, str(e)),
                errors=[str(e)],
            )
        except LLMError as e:
            return BatchResult(
                object_name=chunk.object_name,
                object_kind=chunk.object_kind,
                success=False,
                new_code=_make_placeholder(chunk.object_name, f"LLM error: {e}"),
                errors=[str(e)],
            )

        # 3. 提取代码
        new_code = extract_code(llm_output)

        # 4. 校验（正则 + 真 PG 试装）
        result = validate_pg(new_code, **self.validate_kwargs)

        if result.ok:
            return BatchResult(
                object_name=chunk.object_name,
                object_kind=chunk.object_kind,
                success=True,
                new_code=new_code,
                warnings=result.warnings,
                quota_used=1,
            )

        # 5. 失败：占位回灌
        return BatchResult(
            object_name=chunk.object_name,
            object_kind=chunk.object_kind,
            success=False,
            new_code=_make_placeholder(
                chunk.object_name, "; ".join(result.errors[:3])
            ),
            errors=result.errors,
            warnings=result.warnings,
            quota_used=1,
        )

    def process_sql_file(self, sql_path: Path) -> list[BatchResult]:
        """处理整个 SQL 文件：切块 → 逐 chunk 处理 → 返回 BatchResult 列表。

        失败 chunk 用 `RAISE EXCEPTION 'not implemented'` 占位替换，
        保证产物能装 PG（验 L3.5 至少能装）。
        """
        text = sql_path.read_text(encoding="utf-8")
        try:
            chunks = chunk_function_or_procedure(text)
        except ChunkError as e:
            return [BatchResult(
                object_name="<unknown>",
                object_kind="<unknown>",
                success=False,
                new_code=text,
                errors=[f"chunk error: {e}"],
            )]

        return [self.process_chunk(c) for c in chunks]

    def apply_results_to_text(
        self, original_text: str, chunks: list, results: list
    ) -> str:
        """把 BatchResult 的 new_code 替换回原文本的对应位置。

        策略：从后往前，按 chunk.code 精确匹配替换（避免行号偏移 bug）。
        """
        new_text = original_text
        for chunk, result in zip(reversed(chunks), reversed(results)):
            if chunk.code in new_text:
                new_text = new_text.replace(chunk.code, result.new_code, 1)
        return new_text


def _make_placeholder(name: str, error_msg: str) -> str:
    """失败占位函数（保证 PG 能装入 + RAISE EXCEPTION 不静默）。"""
    safe_name = re.sub(r"\W", "_", name) or "_invalid"
    err_safe = error_msg.replace("'", "''")[:200]
    return PLACEHOLDER_FAILED.format(name=safe_name, errors=err_safe)