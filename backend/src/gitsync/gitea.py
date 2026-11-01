"""Gitea API 客户端：认证、仓库、分支。"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass


class GiteaError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class GiteaRepo:
    full_name: str
    private: bool
    empty: bool
    mirror: bool
    push: bool
    pull: bool


def _basic(username: str, password: str) -> str:
    raw = f"{username}:{password}".encode()
    return "Basic " + base64.b64encode(raw).decode()


class Client:
    """只读客户端：token 认证。"""

    def __init__(self, base_url: str, token: str):
        self.base = base_url.rstrip("/")
        self.token = token

    def _request(self, path: str, method: str = "GET", body: dict | None = None):
        url = f"{self.base}/api/v1{path}"
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"token {self.token}")
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise GiteaError(
                f"{method} {path} -> {exc.code}: {detail[:200]}", exc.code
            ) from exc
        except urllib.error.URLError as exc:
            raise GiteaError(f"无法连接 {self.base}: {exc.reason}") from exc
        return json.loads(raw) if raw else None

    def get_user(self) -> dict:
        return self._request("/user")

    def list_repos(self) -> list[GiteaRepo]:
        repos: list[GiteaRepo] = []
        page = 1
        while True:
            batch = self._request(f"/user/repos?limit=50&page={page}")
            if not batch:
                break
            repos.extend(_to_repo(item) for item in batch)
            if len(batch) < 50:
                break
            page += 1
        return repos

    def get_repo(self, full_name: str) -> GiteaRepo:
        owner, _, repo = full_name.partition("/")
        return _to_repo(self._request(f"/repos/{owner}/{repo}"))

    def list_branches(self, full_name: str) -> list[tuple[str, str]]:
        owner, _, repo = full_name.partition("/")
        data = self._request(f"/repos/{owner}/{repo}/branches?limit=50")
        return [(item["name"], item["commit"]["id"]) for item in data]


def verify_login(base_url: str, username: str, password: str) -> dict:
    """用账号密码验证一次，返回用户信息；失败抛 GiteaError。"""
    url = f"{base_url.rstrip('/')}/api/v1/user"
    request = urllib.request.Request(url)
    request.add_header("Authorization", _basic(username, password))
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise GiteaError("账号或密码错误", exc.code) from exc
    except urllib.error.URLError as exc:
        raise GiteaError(f"无法连接 {base_url}: {exc.reason}") from exc


def _to_repo(item: dict) -> GiteaRepo:
    permissions = item.get("permissions") or {}
    return GiteaRepo(
        full_name=item["full_name"],
        private=bool(item.get("private")),
        empty=bool(item.get("empty")),
        mirror=bool(item.get("mirror")),
        push=bool(permissions.get("push")),
        pull=bool(permissions.get("pull")),
    )


def create_token(base_url: str, username: str, password: str, name: str) -> str:
    """用账号密码换一个访问令牌。"""
    url = f"{base_url.rstrip('/')}/api/v1/users/{username}/tokens"
    body = {"name": name, "scopes": ["read:user", "write:repository"]}
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST"
    )
    request.add_header("Authorization", _basic(username, password))
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise GiteaError(f"创建令牌失败 -> {exc.code}: {detail[:200]}", exc.code) from exc
    except urllib.error.URLError as exc:
        raise GiteaError(f"无法连接 {base_url}: {exc.reason}") from exc
    token = data.get("token") or data.get("sha1")
    if not token:
        raise GiteaError("服务端没有返回令牌")
    return token
