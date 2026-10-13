"""workspace/agent/snapshot.py

P4-1 产物快照（实施路径 §四 P4-1 护栏）。

设计：
- 每批转换前 commit 当前产物快照（可回滚点）
- 转换失败时 `git checkout HEAD -- <files>` 还原
- 强制 git 跟踪（产物本来就在 git 里）

使用：
    sm = Snapshot()
    sha = sm.create("P4 batch 1: FUNC_* refill")
    # ... 做转换 ...
    if failed:
        sm.restore(sha, files=["oracle_poc/output/..."])
"""
from __future__ import annotations
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]  # D:/JIP/poc


class SnapshotError(Exception):
    """快照失败——非 git 仓库 / 产物未跟踪。"""


def _git(*args: str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    """跑 git 命令，失败抛 SnapshotError。"""
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise SnapshotError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc


class Snapshot:
    """git 快照封装。"""

    def __init__(self, repo_root: Path = REPO_ROOT):
        self.repo = repo_root

    def create(self, message: str) -> str:
        """创建产物快照，返回 commit SHA。

        注意：会 `git add -A` 把所有变更先暂存；
        如果有未跟踪的新产物文件，下游可正常 commit。
        """
        # 仅 add 已跟踪的产物 + 变更；不 commit 测试脚本
        _git("add", "-A", cwd=self.repo)
        proc = _git("commit", "-m", f"snapshot: {message}", "--allow-empty", cwd=self.repo)
        # 取最新 commit SHA
        head_proc = _git("rev-parse", "HEAD", cwd=self.repo)
        return head_proc.stdout.strip()

    def restore(self, sha: str, files: list[str] | None = None) -> None:
        """还原到指定 SHA。

        Args:
            sha: 快照 SHA
            files: 仅还原指定文件；None = 还原所有（git checkout -- .）
        """
        if files:
            for f in files:
                _git("checkout", sha, "--", f, cwd=self.repo)
        else:
            _git("checkout", sha, "--", ".", cwd=self.repo)

    def current_sha(self) -> str:
        """当前 HEAD SHA。"""
        proc = _git("rev-parse", "HEAD", cwd=self.repo)
        return proc.stdout.strip()