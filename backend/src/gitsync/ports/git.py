"""接口定义（ports）。

处理层只依赖这里的 Protocol，具体实现放在 adapters 里，可替换、可伪造。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class GitError(Exception):
    """git 操作失败。"""

    def __init__(self, message: str, stderr: str = ""):
        text = message if not stderr else f"{message}: {stderr.splitlines()[0]}"
        super().__init__(text)
        self.stderr = stderr


class NonFastForward(GitError):
    """快进推送被拒绝。"""


class LeaseMismatch(GitError):
    """覆盖时远端已经变化。"""


@dataclass(frozen=True)
class Endpoint:
    """一个 git 远端：地址 + 凭据。"""

    name: str
    url: str
    username: str = ""
    token: str = ""


class GitWorkspace(Protocol):
    """一次同步过程中共享的临时工作区，避免重复拉取对象。"""

    def fetch(self, ep: Endpoint, branch: str) -> str:
        """把分支取到工作区，返回提交 sha。"""
        ...

    def commit_time(self, sha: str) -> int:
        """提交时间（unix 秒）。"""
        ...

    def push(self, ep: Endpoint, sha: str, branch: str) -> None:
        """快进推送；非快进抛 NonFastForward。"""
        ...

    def overwrite(self, ep: Endpoint, sha: str, branch: str, *, expected: str) -> None:
        """force-with-lease 覆盖；lease 不匹配抛 LeaseMismatch。仅供对齐/恢复使用。"""
        ...

    def delete(self, ep: Endpoint, branch: str) -> None:
        """删除分支；仅用于清理过期备份。"""
        ...


class GitTransport(Protocol):
    """git 操作层入口。"""

    def list_refs(self, ep: Endpoint) -> dict[str, str]:
        """拉取整体状态：{分支名: sha}。"""
        ...

    def workspace(self) -> GitWorkspace:
        """打开一个工作区，退出时清理。"""
        ...
