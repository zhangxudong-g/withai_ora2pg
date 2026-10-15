"""workspace/agent/config.py

P4-1 Agent 工具链配置。

参考 pipeline/ config.yaml 的 ai 部分，但**简化 + 默认值友好**：
- ollama 作为 LLM provider（与 pipeline 一致）
- qwen3.8:27b 作为默认模型（pipeline 已验证可用）
- 默认 base_url 占位（pipeline 默认值是 swiftechie 远程实例，本地开发需 override）

使用方式：
    from workspace.agent.config import get_config
    cfg = get_config()
    print(cfg.base_url, cfg.model)
"""
from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentConfig:
    """Agent 工具链配置（immutable）"""

    # LLM 连接
    base_url: str            # ollama 服务地址
    model: str              # 模型名（qwen3.8:27b / 其他）
    api_key: str            # 鉴权 token（本地 ollama 通常为空）

    # 推理参数（与 pipeline/ 一致）
    temperature: float      # 0.0 = 强制确定性（项目硬约束）
    num_ctx: int            # 上下文窗口
    think: bool             # False = 关闭 Qwen 思考模式（直接出代码）

    # 配额 + 重试
    max_retries: int        # 单 chunk 重试次数
    retry_base_backoff: float # 指数退避基数（秒）
    quota_limit: int        # 全局配额上限

    # 缓存
    cache_dir: str          # 响应缓存落盘目录


def _env_or(name: str, default: str) -> str:
    """读环境变量，缺则用 default。"""
    return os.environ.get(name, default)


def get_config() -> AgentConfig:
    """从环境变量构建 AgentConfig；所有字段都有默认值。"""
    return AgentConfig(
        # 远程 ollama 实例（与 pipeline/ config.yaml 一致）
        base_url=_env_or("OLLAMA_BASE_URL", "http://localhost:11434"),
        # 与 pipeline/ config.yaml 默认一致
        model=_env_or("OLLAMA_MODEL", "qwen3.8:27b"),
        # 远程 ollama 用 "ollama" 占位 token（pipeline 默认）
        api_key=_env_or("OLLAMA_AUTH_TOKEN", "ollama"),
        # 关键：temperature=0（项目硬约束：AI 必须可复现）
        temperature=float(_env_or("OLLAMA_TEMPERATURE", "0.0")),
        num_ctx=int(_env_or("OLLAMA_NUM_CTX", "32768")),
        think=_env_or("OLLAMA_THINK", "false").lower() in ("1", "true", "yes"),
        max_retries=int(_env_or("AGENT_MAX_RETRIES", "3")),
        retry_base_backoff=float(_env_or("AGENT_RETRY_BACKOFF", "2.0")),
        # 实施路径 §Pre-7：配额 60–200 上限
        quota_limit=int(_env_or("AGENT_QUOTA_LIMIT", "200")),
        # 缓存：相对 oracle_poc/output/.agent_cache/，避免产物变更清缓存
        cache_dir=_env_or("AGENT_CACHE_DIR", "oracle_poc/output/.agent_cache"),
    )