"""代码托管平台的接口：身份、仓库、权限。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ProviderError(Exception):
    """平台接口调用失败。"""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class User:
    login: str
    full_name: str = ""
    avatar_url: str = ""


@dataclass(frozen=True)
class RemoteRepo:
    full_name: str
    private: bool
    empty: bool
    mirror: bool
    push: bool
    pull: bool


class RepoProvider(Protocol):
    """当前由 Gitea 实现。方法都接收访问令牌。"""

    def verify_login(self, username: str, password: str) -> User: ...

    def issue_token(self, username: str, password: str, name: str) -> str: ...

    def get_user(self, token: str) -> User: ...

    def list_repos(self, token: str) -> list[RemoteRepo]: ...

    def get_repo(self, token: str, full_name: str) -> RemoteRepo: ...
