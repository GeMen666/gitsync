"""远程凭据的对称加密。

密钥来源：优先环境变量 GITSYNC_SECRET，否则在数据目录生成 secret.key（600）。
注意：密钥和数据放一起只能防止“误拷数据库”，挡不住能读整台机器的人。
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretError(Exception):
    """凭据加解密失败。"""


def _key(data_dir: Path) -> bytes:
    raw = os.environ.get("GITSYNC_SECRET")
    if raw:
        return base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
    path = data_dir / "secret.key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(Fernet.generate_key())
        path.chmod(0o600)
    return path.read_bytes()


def encrypt(text: str, data_dir: Path) -> str:
    if not text:
        return ""
    return Fernet(_key(data_dir)).encrypt(text.encode()).decode()


def decrypt(token: str, data_dir: Path) -> str:
    if not token:
        return ""
    try:
        return Fernet(_key(data_dir)).decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise SecretError("远程凭据无法解密（密钥可能变了）") from exc
