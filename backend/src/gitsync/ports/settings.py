"""运行时配置。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    path: Path
    local_url: str
    local_username: str
    local_token: str
    remote_username: str
    remote_token: str
    db_path: Path
    temp_dir: Path
    log_dir: Path
