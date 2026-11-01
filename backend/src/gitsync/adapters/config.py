"""配置加载：连接信息与路径，仓库配对存数据库。"""

from __future__ import annotations

import os
import stat
import tomllib
from pathlib import Path

from ..ports.settings import Settings


class ConfigError(Exception):
    """配置文件本身有问题。"""


def _secret(section: dict, name: str) -> str:
    env_name = section.get(f"{name}_env")
    if env_name:
        value = os.environ.get(str(env_name))
        if not value:
            raise ConfigError(f"环境变量 {env_name} 未设置")
        return value
    value = section.get(name)
    if not value:
        raise ConfigError(f"缺少 {name} 或 {name}_env")
    return str(value)


def _resolve(base: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else base / path


def load_config(explicit: str | os.PathLike[str] | None = None) -> Settings:
    """读取并校验配置文件。"""
    raw = explicit or os.environ.get("GITSYNC_CONFIG") or "config.toml"
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise ConfigError(f"找不到配置文件：{path}（可复制 config.example.toml）")

    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise ConfigError(f"{path} 权限过于宽松（{oct(mode)}），请 chmod 600")

    with path.open("rb") as fh:
        data = tomllib.load(fh)

    local = data.get("local") or {}
    remote = data.get("remote") or {}
    paths = data.get("paths") or {}
    base = path.parent

    local_url = str(local.get("url") or "").rstrip("/")
    if not local_url:
        raise ConfigError("[local] 缺少 url")

    return Settings(
        path=path,
        local_url=local_url,
        local_username=str(local.get("username") or ""),
        local_token=_secret(local, "token"),
        remote_username=str(remote.get("username") or ""),
        remote_token=_secret(remote, "token") if remote else "",
        db_path=_resolve(base, str(paths.get("db") or "data/gitsync.db")),
        temp_dir=_resolve(base, str(paths.get("temp") or "temp")),
        log_dir=_resolve(base, str(paths.get("logs") or "logs")),
    )
