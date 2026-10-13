"""workspace/agent/llm_client.py

P4-1 LLM 客户端封装。

参考 pipeline/stages/stage3_ai/tools/base.py 的 OllamaClient，但**升级**：
- 缓存层：输入 hash → 响应（避免重转时重复消耗配额；同函数不调两次）
- 重试：连接类错误才重试（其他直接抛）；指数退避
- 配额熔断：调起 quota，超额抛 QuotaExceeded
- 不依赖 Strands SDK（pipeline/ 强依赖；我们不引入）

使用：
    client = OllamaClient()
    response = client.generate(prompt)
"""
from __future__ import annotations
import hashlib
import json
import time
from pathlib import Path
from typing import Optional

import ollama

from .config import AgentConfig, get_config
from .quota import QuotaExceeded, get_quota


# 重试判定（pipeline/_/ares/utils.py 的简化版）
_RETRYABLE = (
    "connection", "timeout", "refused", "unreachable",
    "network", "temporarily", "service unavailable",
    "failed to connect", "connection reset", "broken pipe", "http",
)


class LLMError(Exception):
    """通用 LLM 错误。"""


class OllamaClient:
    """Ollama LLM 客户端封装 + 缓存 + 重试 + 配额。"""

    def __init__(self, cfg: Optional[AgentConfig] = None, cache_enabled: bool = True):
        self.cfg = cfg or get_config()
        self.cache_enabled = cache_enabled
        self.cache_dir = Path(self.cfg.cache_dir)
        if self.cache_enabled:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 公开方法 ----------

    def generate(self, prompt: str, think: Optional[bool] = None) -> str:
        """调用 Ollama 生成内容。

        Args:
            prompt: 用户提示（含 SYSTEM_PROMPT + context + code）
            think: 覆盖 config.think；None = 用配置

        Returns:
            LLM 返回的 message.content

        Raises:
            QuotaExceeded: 配额用完（实施路径 §Pre-7 60–200）
            LLMError: 不可重试错误 或 重试耗尽
        """
        # 1. 配额门控
        quota = get_quota()
        if not quota.check():
            raise QuotaExceeded(quota.used(), quota.max_calls)

        # 2. 缓存命中 → 直接返回（不消耗配额）
        if self.cache_enabled:
            cached = self._cache_get(prompt)
            if cached is not None:
                return cached

        # 3. 调 ollama（带指数退避）
        use_think = think if think is not None else self.cfg.think
        last_err: Optional[Exception] = None

        for attempt in range(self.cfg.max_retries):
            try:
                content = self._call_ollama(prompt, use_think)
                # 成功：扣配额 + 写缓存
                quota.inc()
                if self.cache_enabled:
                    self._cache_put(prompt, content)
                return content
            except Exception as e:
                last_err = e
                if not _is_retryable(e) or attempt == self.cfg.max_retries - 1:
                    break
                delay = self.cfg.retry_base_backoff ** (attempt + 1)
                time.sleep(delay)

        raise LLMError(f"ollama call failed after {self.cfg.max_retries} attempts: {last_err}")

    # ---------- 内部方法 ----------

    def _call_ollama(self, prompt: str, think: bool) -> str:
        """底层 ollama 调用，无重试无缓存。"""
        headers = {"Authorization": f"Bearer {self.cfg.api_key}"} if self.cfg.api_key else {}
        client = ollama.Client(host=self.cfg.base_url, headers=headers)
        response = client.chat(
            model=self.cfg.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an Oracle to PostgreSQL migration expert. "
                        "Output code only, no explanations."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            think=think,
            options={"temperature": self.cfg.temperature, "num_ctx": self.cfg.num_ctx},
        )
        message_obj = response.get("message", {})
        content = message_obj.get("content", "")
        if not content:
            raise LLMError(f"ollama returned empty content (model={self.cfg.model})")
        return content

    def _cache_key(self, prompt: str) -> str:
        """含 model + prompt 的稳定 hash（model 也参与，模型换了缓存失效）。"""
        payload = json.dumps({"model": self.cfg.model, "prompt": prompt}, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def _cache_get(self, prompt: str) -> Optional[str]:
        key = self._cache_key(prompt)
        path = self.cache_dir / f"{key}.txt"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def _cache_put(self, prompt: str, content: str) -> None:
        key = self._cache_key(prompt)
        path = self.cache_dir / f"{key}.txt"
        path.write_text(content, encoding="utf-8")


# ---------- 辅助 ----------

def _is_retryable(error: Exception) -> bool:
    """判断错误是否可重试（连接类）；其他（4xx、解析错）不重试。"""
    msg = str(error).lower()
    return any(kw in msg for kw in _RETRYABLE)