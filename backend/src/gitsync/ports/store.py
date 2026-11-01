"""存储层接口：会话、配对、分支状态。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class Session:
    id: str
    username: str
    token: str
    created_at: str


@dataclass
class Pair:
    id: int
    owner: str
    name: str
    local_repo: str
    remote_url: str
    remote_username: str
    remote_secret: str
    push_enabled: bool
    pull_enabled: bool
    backup_retention_days: int


@dataclass
class BranchState:
    pair_id: int
    branch: str
    local_sha: str | None
    remote_sha: str | None
    status: str
    backup_branch: str
    backup_side: str
    detail: str
    updated_at: str


class StateStore(Protocol):
    """持久化接口。实现见 adapters/store.py。"""

    def close(self) -> None: ...

    # 会话
    def create_session(self, sid: str, username: str, token: str) -> None: ...
    def get_session(self, sid: str) -> Session | None: ...
    def delete_session(self, sid: str) -> None: ...

    # 配对
    def add_pair(
        self,
        name: str,
        local_repo: str,
        remote_url: str,
        *,
        owner: str = "",
        remote_username: str = "",
        remote_secret: str = "",
        push: bool = True,
        pull: bool = True,
        retention_days: int = 7,
    ) -> Pair: ...
    def get_pair(self, name: str) -> Pair: ...
    def get_pair_by_id(self, pair_id: int) -> Pair | None: ...
    def list_pairs(self, owner: str | None = None) -> list[Pair]: ...
    def update_pair(
        self,
        name: str,
        *,
        remote_url: str | None = None,
        remote_username: str | None = None,
        remote_secret: str | None = None,
        push: bool | None = None,
        pull: bool | None = None,
        retention_days: int | None = None,
    ) -> Pair: ...
    def delete_pair(self, name: str) -> None: ...

    # 分支状态
    def save_branch(
        self,
        pair_id: int,
        branch: str,
        *,
        local_sha: str | None,
        remote_sha: str | None,
        status: str,
        backup_branch: str | None = None,
        backup_side: str | None = None,
        detail: str = "",
    ) -> None: ...
    def get_branch(self, pair_id: int, branch: str) -> BranchState | None: ...
    def list_branches(self, pair_id: int) -> list[BranchState]: ...
    def prune_branches(self, pair_id: int, keep: set[str]) -> None: ...
