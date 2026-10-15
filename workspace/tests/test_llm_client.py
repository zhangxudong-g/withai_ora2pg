"""workspace/tests/test_llm_client.py

P4-1 单测：llm_client.OllamaClient

覆盖：
- 配额熔断（check/inc/QuotaExceeded）
- 缓存命中（第二次不调 ollama）
- 重试机制（连接错重试，业务错不重试）
- ollama mock 调用参数（model / temperature / think / num_ctx）

不需要真 ollama 服务（用 mock）。
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# 让 workspace/agent 可 import
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pytest

from workspace.agent.llm_client import OllamaClient, LLMError
from workspace.agent.quota import QuotaExceeded, reset_for_tests
from workspace.agent import quota as quota_mod  # 用模块引用，避免 from-import 锁定
from workspace.agent.config import AgentConfig


# ---------- 测试 fixtures ----------

@pytest.fixture
def tmp_cache(tmp_path):
    """独立缓存目录，避免污染。"""
    cache_dir = tmp_path / ".agent_cache"
    cache_dir.mkdir()
    return cache_dir


@pytest.fixture
def cfg(tmp_path, tmp_cache) -> AgentConfig:
    """构造测试用配置：cache 落到 tmp_path，配额小（5）。"""
    return AgentConfig(
        base_url="http://test-host:11434",
        model="test-model:7b",
        api_key="test-key",
        temperature=0.0,
        num_ctx=4096,
        think=False,
        max_retries=3,
        retry_base_backoff=1.0,  # 测试用 1 秒退避
        quota_limit=5,
        cache_dir=str(tmp_cache),
    )


@pytest.fixture(autouse=True)
def _reset_quota(tmp_path, monkeypatch):
    """每个测试重置配额单例；counter 文件指向 tmp。"""
    import workspace.agent.quota as qmod
    qmod._instance = None
    counter_path = tmp_path / ".quota_counter"
    # 让真 get_quota() 用 tmp_path 路径构造（不依赖默认 oracle_poc/output/...）
    def fake_get_quota():
        return qmod._QuotaManager(counter_path, max_calls=5)
    monkeypatch.setattr(qmod, "get_quota", fake_get_quota)
    # 让 llm_client 里的 get_quota 也指向 fake（避免 llm_client 导入自己的引用）
    import workspace.agent.llm_client as lc
    monkeypatch.setattr(lc, "get_quota", fake_get_quota)


# ---------- 配额测试 ----------

def test_quota_init_zero(_reset_quota, cfg):
    """初始配额 = 0 used, 5 limit"""
    q = quota_mod.get_quota()
    assert q.used() == 0
    assert q.remaining() == 5
    assert q.check() is True


def test_quota_inc_and_check(_reset_quota, cfg):
    """inc 一次后 used+1，到 limit 时 check=False"""
    q = quota_mod.get_quota()
    q.inc()
    q.inc()
    assert q.used() == 2
    assert q.remaining() == 3
    assert q.check() is True


def test_quota_reset(_reset_quota, cfg):
    """reset 清零"""
    q = quota_mod.get_quota()
    q.inc()
    q.inc()
    q.reset()
    assert q.used() == 0


def test_quota_persists_across_instances(_reset_quota, cfg):
    """配额持久化到文件"""
    q1 = quota_mod.get_quota()
    q1.inc()
    q1.inc()
    # 模拟"新进程"——重置单例，再读
    quota_mod._instance = None
    # 重置 fixture 让 get_quota 读文件
    counter_path = Path(cfg.cache_dir).parent.parent / ".quota_counter"
    quota_mod._instance = quota_mod._QuotaManager(counter_path, max_calls=5)
    q2 = quota_mod.get_quota()
    assert q2.used() == 2


# ---------- LLM 客户端测试 ----------

def _mock_ollama_response(content: str = "CREATE OR REPLACE FUNCTION x() RETURNS void ...") -> MagicMock:
    """构造假 ollama 响应。"""
    resp = MagicMock()
    resp.get.return_value = {"content": content}
    return resp


def test_generate_calls_ollama_with_expected_params(cfg):
    """generate 应把 config 字段传到 ollama.client.chat"""
    client = OllamaClient(cfg, cache_enabled=False)
    with patch("workspace.agent.llm_client.ollama.Client") as MockClient:
        instance = MagicMock()
        instance.chat.return_value = _mock_ollama_response("OK")
        MockClient.return_value = instance

        out = client.generate("hello world")

    assert out == "OK"
    # 校验调用参数
    call_kwargs = instance.chat.call_args.kwargs
    assert call_kwargs["model"] == "test-model:7b"
    assert call_kwargs["think"] is False
    opts = call_kwargs["options"]
    assert opts["temperature"] == 0.0
    assert opts["num_ctx"] == 4096
    # messages 结构
    msgs = call_kwargs["messages"]
    assert msgs[0]["role"] == "system"
    assert "migration expert" in msgs[0]["content"]
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"] == "hello world"


def test_generate_inc_counter(cfg):
    """每次成功 generate 应扣配额"""
    client = OllamaClient(cfg, cache_enabled=False)
    q = quota_mod.get_quota()
    initial = q.used()

    with patch("workspace.agent.llm_client.ollama.Client") as MockClient:
        instance = MagicMock()
        instance.chat.return_value = _mock_ollama_response("OK")
        MockClient.return_value = instance
        client.generate("prompt 1")
        client.generate("prompt 2")

    assert q.used() == initial + 2


def test_generate_quota_exceeded_raises(cfg):
    """超额抛 QuotaExceeded（不调 ollama）"""
    client = OllamaClient(cfg, cache_enabled=False)
    q = quota_mod.get_quota()
    # 用满
    for _ in range(cfg.quota_limit):
        q.inc()
    assert not q.check()

    with patch("workspace.agent.llm_client.ollama.Client") as MockClient:
        instance = MagicMock()
        instance.chat.return_value = _mock_ollama_response("OK")
        MockClient.return_value = instance

        with pytest.raises(QuotaExceeded):
            client.generate("should not call")


# ---------- 缓存测试 ----------

def test_cache_hit_avoids_second_call(cfg):
    """同 prompt 第二次应命中缓存，不调 ollama"""
    client = OllamaClient(cfg, cache_enabled=True)
    with patch("workspace.agent.llm_client.ollama.Client") as MockClient:
        instance = MagicMock()
        instance.chat.return_value = _mock_ollama_response("cached output")
        MockClient.return_value = instance

        # 第一次：实际调 ollama
        out1 = client.generate("same prompt")
        # 第二次：缓存命中
        out2 = client.generate("same prompt")

    assert out1 == out2 == "cached output"
    # ollama 应只调一次
    assert instance.chat.call_count == 1


def test_cache_disabled_calls_each_time(cfg):
    """cache_enabled=False 时不缓存"""
    client = OllamaClient(cfg, cache_enabled=False)
    with patch("workspace.agent.llm_client.ollama.Client") as MockClient:
        instance = MagicMock()
        instance.chat.return_value = _mock_ollama_response("v")
        MockClient.return_value = instance

        client.generate("p")
        client.generate("p")

    assert instance.chat.call_count == 2


def test_cache_different_model_invalidates(cfg, tmp_cache):
    """改 model 后同 prompt 应重新调（model 参与 hash）"""
    client_a = OllamaClient(cfg, cache_enabled=True)
    from workspace.agent.llm_client import OllamaClient as OC
    with patch("workspace.agent.llm_client.ollama.Client") as MockClient:
        instance = MagicMock()
        instance.chat.return_value = _mock_ollama_response("a")
        MockClient.return_value = instance

        client_a.generate("same")

        # 改 model + 创建新 client
        cfg2 = AgentConfig(**{**cfg.__dict__, "model": "other-model:7b"})
        client_b = OC(cfg2, cache_enabled=True)
        instance.chat.return_value = _mock_ollama_response("b")
        out_b = client_b.generate("same")

    assert out_b == "b"
    assert instance.chat.call_count == 2


# ---------- 重试测试 ----------

def test_retry_on_connection_error(cfg):
    """连接错重试 3 次（max_retries=3）"""
    client = OllamaClient(cfg, cache_enabled=False)
    with patch("workspace.agent.llm_client.ollama.Client") as MockClient:
        instance = MagicMock()
        instance.chat.side_effect = ConnectionError("connection refused: test")
        MockClient.return_value = instance

        with pytest.raises(LLMError) as exc_info:
            client.generate("prompt")

    # 3 次重试都失败
    assert instance.chat.call_count == 3
    assert "connection refused" in str(exc_info.value).lower()


def test_no_retry_on_business_error(cfg):
    """业务错（如 ValueError）不重试，直接抛"""
    client = OllamaClient(cfg, cache_enabled=False)
    with patch("workspace.agent.llm_client.ollama.Client") as MockClient:
        instance = MagicMock()
        instance.chat.side_effect = ValueError("bad arg")
        MockClient.return_value = instance

        with pytest.raises(LLMError):
            client.generate("prompt")

    # 只调一次（不重试）
    assert instance.chat.call_count == 1


def test_retry_succeeds_after_transient_failure(cfg):
    """瞬时失败后第 2 次成功"""
    client = OllamaClient(cfg, cache_enabled=False)
    q = quota_mod.get_quota()

    call_count = {"n": 0}
    def chat_side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ConnectionError("temporary unreachable")
        return _mock_ollama_response("recovered")

    with patch("workspace.agent.llm_client.ollama.Client") as MockClient:
        instance = MagicMock()
        instance.chat.side_effect = chat_side_effect
        MockClient.return_value = instance

        out = client.generate("retry me")

    assert out == "recovered"
    assert call_count["n"] == 2
    # 只成功一次 → 扣 1 次配额
    assert q.used() == 1


def test_empty_response_raises(cfg):
    """ollama 返回空 content → LLMError"""
    client = OllamaClient(cfg, cache_enabled=False)
    with patch("workspace.agent.llm_client.ollama.Client") as MockClient:
        instance = MagicMock()
        instance.chat.return_value = MagicMock(get=MagicMock(return_value={"content": ""}))
        MockClient.return_value = instance

        with pytest.raises(LLMError) as exc_info:
            client.generate("prompt")
    assert "empty content" in str(exc_info.value).lower()


# ---------- Config 测试 ----------

def test_get_config_defaults(monkeypatch):
    """默认配置对齐 pipeline/ config.yaml"""
    # 清空所有相关 env
    for k in ["OLLAMA_BASE_URL", "OLLAMA_MODEL", "OLLAMA_AUTH_TOKEN",
              "OLLAMA_TEMPERATURE", "OLLAMA_NUM_CTX", "OLLAMA_THINK",
              "AGENT_MAX_RETRIES", "AGENT_RETRY_BACKOFF",
              "AGENT_QUOTA_LIMIT", "AGENT_CACHE_DIR"]:
        monkeypatch.delenv(k, raising=False)

    cfg = AgentConfig.__class_getitem__ if hasattr(AgentConfig, "__class_getitem__") else None
    from workspace.agent.config import get_config
    c = get_config()
    assert c.model == "qwen3.8:27b"
    assert c.base_url == "http://localhost:11434"
    assert c.api_key == "ollama"
    assert c.temperature == 0.0
    assert c.think is False
    assert c.quota_limit == 200
    assert c.cache_dir == "oracle_poc/output/.agent_cache"


def test_get_config_env_override(monkeypatch, tmp_path):
    """环境变量覆盖"""
    monkeypatch.setenv("OLLAMA_MODEL", "llama3:8b")
    monkeypatch.setenv("AGENT_QUOTA_LIMIT", "100")
    monkeypatch.setenv("OLLAMA_TEMPERATURE", "0.5")
    monkeypatch.setenv("AGENT_CACHE_DIR", str(tmp_path / "custom_cache"))

    from workspace.agent.config import get_config
    c = get_config()
    assert c.model == "llama3:8b"
    assert c.quota_limit == 100
    assert c.temperature == 0.5
    assert c.cache_dir == str(tmp_path / "custom_cache")