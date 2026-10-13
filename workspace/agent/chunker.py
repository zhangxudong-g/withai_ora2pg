"""workspace/agent/chunker.py

P4-1 函数边界切块。

参考 pipeline/stages/stage3_ai/chunk_planner.py 的简化版。

策略：
- 按 `CREATE OR REPLACE FUNCTION/PROCEDURE` 起始位置切
- 切到 `LANGUAGE plpgsql`; 结束
- 保留 chunk 间上下文（前 chunk 的尾部 5 行 → 下一个 chunk 的 header）

超长函数处理：
- 单函数 > MAX_LINES → 抛出 ChunkerError（让 orchestrator 决定要不要走 Agent）
"""
from __future__ import annotations
import re
from dataclasses import dataclass


MAX_CHUNK_LINES = 500
CTX_LINES = 5  # 前 chunk 末尾几行作为下个 chunk 的上下文


@dataclass
class Chunk:
    index: int
    code: str
    object_name: str
    object_kind: str  # FUNCTION / PROCEDURE / TRIGGER
    context_before: str = ""


class ChunkError(Exception):
    """切块失败——超长函数、语法不可识别。"""


_HEADER_RE = re.compile(
    r"^\s*CREATE\s+(OR\s+REPLACE\s+)?(FUNCTION|PROCEDURE)\s+(\w+)",
    re.IGNORECASE | re.MULTILINE,
)


def _classify_header(code: str) -> tuple[str, str]:
    """识别首行的对象类型与名字。"""
    m = _HEADER_RE.match(code.upper().strip())
    if not m:
        raise ChunkError(f"Cannot identify function/procedure header: {code[:80]!r}")
    kind = m.group(2).upper()
    name = m.group(3).lower()
    return kind, name


def chunk_function_or_procedure(code: str) -> list[Chunk]:
    """按 FUNCTION/PROCEDURE 边界切块。

    输入：
        整个含一个或多个 CREATE 的源
    返回：
        按出现顺序的 Chunk 列表
    """
    if not code or not code.strip():
        raise ChunkError("Empty code")

    chunks: list[Chunk] = []

    # 找所有 CREATE FUNCTION/PROCEDURE 起止
    starts = list(_HEADER_RE.finditer(code.upper()))
    if not starts:
        raise ChunkError("No CREATE FUNCTION/PROCEDURE found")

    # 单 chunk
    if len(starts) == 1:
        kind, name = _classify_header(code)
        if len(code.splitlines()) > MAX_CHUNK_LINES:
            raise ChunkError(
                f"Function {name} too long ({len(code.splitlines())} > {MAX_CHUNK_LINES})"
            )
        chunks.append(Chunk(index=0, code=code, object_name=name, object_kind=kind))
        return chunks

    # 多 chunk：每个 CREATE ... 到下个 CREATE 之前为止
    for i, m in enumerate(starts):
        start = m.start()
        end = starts[i + 1].start() if i + 1 < len(starts) else len(code)
        body = code[start:end]
        kind, name = _classify_header(body)
        if len(body.splitlines()) > MAX_CHUNK_LINES:
            raise ChunkError(
                f"Function {name} too long ({len(body.splitlines())} > {MAX_CHUNK_LINES})"
            )

        # 上下文 = 前一个 chunk 末尾
        ctx = ""
        if chunks:
            prev_lines = chunks[-1].code.rstrip().splitlines()
            ctx_lines = prev_lines[-CTX_LINES:] if len(prev_lines) >= CTX_LINES else prev_lines
            ctx = "\n".join(ctx_lines)

        chunks.append(Chunk(
            index=len(chunks),
            code=body,
            object_name=name,
            object_kind=kind,
            context_before=ctx,
        ))

    return chunks