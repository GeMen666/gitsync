"""凭据加密接口。"""

from __future__ import annotations

from typing import Protocol


class SecretVault(Protocol):
    def encrypt(self, plain: str) -> str: ...

    def decrypt(self, cipher: str) -> str: ...
