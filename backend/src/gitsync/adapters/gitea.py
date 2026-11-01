"""Gitea 实现：身份、仓库、令牌。"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

from ..ports.provider import ProviderError, RemoteRepo, User


def _basic(username: str, password: str) -> str:
    raw = f"{username}:{password}".encode()
    return "Basic " + base64.b64encode(raw).decode()


class GiteaProvider:
    """用 Gitea 的 /api/v1 实现 RepoProvider。"""

    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")

    # ---------- 内部 ----------

    def _request(self, path: str, *, token: str, method: str = "GET", body: dict | None = None):
        url = f"{self.base}/api/v1{path}"
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"token {token}")
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        return self._send(request, path)

    def _send(self, request: urllib.request.Request, path: str):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise ProviderError(f"{request.method} {path} -> {exc.code}: {detail[:200]}", exc.code) from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"无法连接 {self.base}: {exc.reason}") from exc
        return json.loads(raw) if raw else None

    # ---------- RepoProvider ----------

    def verify_login(self, username: str, password: str) -> User:
        """用账号密码验证身份。"""
        request = urllib.request.Request(f"{self.base}/api/v1/user")
        request.add_header("Authorization", _basic(username, password))
        request.add_header("Accept", "application/json")
        return _user(self._send(request, "/user"))

    def issue_token(self, username: str, password: str, name: str) -> str:
        """用账号密码换一个访问令牌。"""
        url = f"{self.base}/api/v1/users/{username}/tokens"
        body = {"name": name, "scopes": ["read:user", "write:repository"]}
        request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
        request.add_header("Authorization", _basic(username, password))
        request.add_header("Content-Type", "application/json")
        data = self._send(request, f"/users/{username}/tokens")
        token = data.get("token") or data.get("sha1") if data else None
        if not token:
            raise ProviderError("服务端没有返回令牌")
        return token

    def get_user(self, token: str) -> User:
        """按令牌取当前用户。"""
        return _user(self._request("/user", token=token))

    def list_repos(self, token: str) -> list[RemoteRepo]:
        """列出当前用户可访问的仓库。"""
        repos: list[RemoteRepo] = []
        page = 1
        while True:
            batch = self._request(f"/user/repos?limit=50&page={page}", token=token)
            if not batch:
                break
            repos.extend(_repo(item) for item in batch)
            if len(batch) < 50:
                break
            page += 1
        return repos

    def get_repo(self, token: str, full_name: str) -> RemoteRepo:
        """取单个仓库。"""
        owner, _, repo = full_name.partition("/")
        return _repo(self._request(f"/repos/{owner}/{repo}", token=token))


def _user(data: dict) -> User:
    return User(
        login=data["login"],
        full_name=data.get("full_name") or "",
        avatar_url=data.get("avatar_url") or "",
    )


def _repo(data: dict) -> RemoteRepo:
    permissions = data.get("permissions") or {}
    return RemoteRepo(
        full_name=data["full_name"],
        private=bool(data.get("private")),
        empty=bool(data.get("empty")),
        mirror=bool(data.get("mirror")),
        push=bool(permissions.get("push")),
        pull=bool(permissions.get("pull")),
    )
