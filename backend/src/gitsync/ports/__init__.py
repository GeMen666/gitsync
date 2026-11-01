"""接口定义（ports）：处理层只依赖这里，实现放在 adapters。"""

from .git import (
    Endpoint,
    GitError,
    GitTransport,
    GitWorkspace,
    LeaseMismatch,
    NonFastForward,
)
from .provider import ProviderError, RemoteRepo, RepoProvider, User
from .settings import Settings
from .store import BranchState, Pair, Session, StateStore
from .vault import SecretVault

__all__ = [
    "Endpoint",
    "GitError",
    "GitTransport",
    "GitWorkspace",
    "LeaseMismatch",
    "NonFastForward",
    "ProviderError",
    "RemoteRepo",
    "RepoProvider",
    "User",
    "Settings",
    "BranchState",
    "Pair",
    "Session",
    "StateStore",
    "SecretVault",
]
