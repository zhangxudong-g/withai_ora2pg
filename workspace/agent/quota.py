"""workspace/agent/quota.py

P4-1 全局配额管理器。

实施路径 §Pre-7：AI 额度 60–200 次（按场景），超额转人工 / 不抛崩。

设计：
- 持久化计数器：写到 .quota_counter 文件，进程重启不丢数
- 全局单例：调用 get_quota() 拿共享 instance
- 线程安全（单测 + orchestrator 串行调用足够；如要并行再加 lock）

使用：
    q = get_quota()
    if not q.check():
        raise QuotaExceeded(q.used(), q.limit())
    q.inc()
"""
from .config import get_config
from pathlib import Path


class QuotaExceeded(Exception):
    """触发上限，应转人工（不重试）"""

    def __init__(self, used: int, max_calls: int):
        self.used = used
        self.max_calls = max_calls
        super().__init__(f"AI quota exceeded ({used}/{max_calls}); 转人工或调 AGENT_QUOTA_LIMIT")


class _QuotaManager:
    """简单持久配额管理（文件计数）"""

    def __init__(self, counter_path: Path, max_calls: int):
        self.path = counter_path
        self.max_calls = max_calls

    def used(self) -> int:
        if not self.path.exists():
            return 0
        try:
            return int(self.path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            return 0

    def remaining(self) -> int:
        return max(0, self.max_calls - self.used())

    def check(self) -> bool:
        """是否还有配额。"""
        return self.used() < self.max_calls

    def inc(self) -> None:
        """用掉一次（不检查上限——调用方先 check）。"""
        new = self.used() + 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(str(new), encoding="utf-8")

    def reset(self) -> None:
        """重置计数器（用于手动跨批重启）。"""
        if self.path.exists():
            self.path.unlink()


# 单例
_instance: _QuotaManager | None = None


def get_quota() -> _QuotaManager:
    """获取全局配额管理器。"""
    global _instance
    if _instance is None:
        cfg = get_config()
        counter_path = Path(cfg.cache_dir).parent / ".quota_counter"
        _instance = _QuotaManager(counter_path, cfg.quota_limit)
    return _instance


def reset_for_tests() -> None:
    """测试钩子：重置单例。"""
    global _instance
    _instance = None