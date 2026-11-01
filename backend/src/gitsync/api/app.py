"""FastAPI 应用：对外接口层。"""

from __future__ import annotations

import os
import secrets
from dataclasses import asdict, replace

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from ..adapters.vault import FernetVault
from ..adapters.config import load_config
from ..adapters.git_cli import CliGitTransport
from ..adapters.gitea import GiteaProvider
from ..adapters.store import Store
from ..ports import GitError, ProviderError
from ..services.sync import SyncService

COOKIE = "gitsync_session"
MAX_AGE = 7 * 24 * 3600


class LoginBody(BaseModel):
    username: str
    password: str


class PairBody(BaseModel):
    local_repo: str
    remote_url: str
    remote_username: str = ""
    remote_token: str = ""
    retention_days: int = 7


class ResolveBody(BaseModel):
    prefer: str


class PairPatch(BaseModel):
    remote_url: str | None = None
    remote_username: str | None = None
    remote_token: str | None = None
    retention_days: int | None = None


def create_app(config_path: str | None = None) -> FastAPI:
    """构造应用。"""
    cfg = load_config(config_path)
    store = Store(cfg.db_path)
    provider = GiteaProvider(cfg.local_url)
    vault = FernetVault(cfg.db_path.parent)
    transport = CliGitTransport(cfg.temp_dir)

    app = FastAPI(title="gitsync", version="0.1.0")

    @app.middleware("http")
    async def no_cache(request: Request, call_next):
        # 状态随时在变，禁止浏览器缓存 API 响应
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # ---------- 会话 ----------

    def session_of(request: Request):
        """从 cookie 取会话。"""
        sid = request.cookies.get(COOKIE)
        if not sid:
            raise HTTPException(status_code=401, detail="未登录")
        session = store.get_session(sid)
        if session is None:
            raise HTTPException(status_code=401, detail="登录已失效")
        return session

    def token_of(request: Request) -> str:
        """当前会话的令牌。"""
        return session_of(request).token

    def service_for(session) -> SyncService:
        """用会话令牌装配处理层。"""
        user_settings = replace(cfg, local_username=session.username, local_token=session.token)
        return SyncService(user_settings, store, vault, transport)

    # ---------- 认证 ----------

    @app.post("/api/login")
    def login(body: LoginBody, response: Response):
        """登录。"""
        try:
            user = provider.verify_login(body.username, body.password)
        except ProviderError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

        name = f"gitsync-web-{secrets.token_hex(4)}"
        try:
            token = provider.issue_token(body.username, body.password, name)
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        sid = secrets.token_urlsafe(32)
        store.create_session(sid, user.login, token)
        response.set_cookie(COOKIE, sid, httponly=True, samesite="lax", max_age=MAX_AGE)
        return {"login": user.login, "full_name": user.full_name}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        """登出。"""
        sid = request.cookies.get(COOKIE)
        if sid:
            store.delete_session(sid)
        response.delete_cookie(COOKIE)
        return {"ok": True}

    @app.get("/api/me")
    def me(request: Request):
        """当前登录用户。"""
        session = session_of(request)
        try:
            user = provider.get_user(session.token)
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"login": user.login, "full_name": user.full_name, "avatar_url": user.avatar_url}

    # ---------- 仓库 ----------

    @app.get("/api/repos")
    def repos(request: Request):
        """当前用户的仓库。"""
        session = session_of(request)
        try:
            items = provider.list_repos(session.token)
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return [asdict(item) for item in items]

    # ---------- 配对 ----------

    def pair_view(pair) -> dict:
        """对外的配对表示，不含密文。"""
        branches = store.list_branches(pair.id)
        last = max((item.updated_at for item in branches), default="")
        return {
            "id": pair.id,
            "name": pair.name,
            "owner": pair.owner,
            "local_repo": pair.local_repo,
            "remote_url": pair.remote_url,
            "remote_username": pair.remote_username,
            "has_remote_secret": bool(pair.remote_secret),
            "retention_days": pair.backup_retention_days,
            "last_updated": last,
            "branches": [asdict(item) for item in branches],
        }

    def owned_pair(request: Request, pair_id: int):
        """取配对并校验归属。"""
        session = session_of(request)
        pair = store.get_pair_by_id(pair_id)
        if pair is None or pair.owner != session.username:
            raise HTTPException(status_code=404, detail="没有这个配对")
        return pair, session

    def require_push(token: str, local_repo: str) -> None:
        """校验写权限。"""
        try:
            info = provider.get_repo(token, local_repo)
        except ProviderError as exc:
            raise HTTPException(status_code=404, detail=f"仓库不存在或无权访问：{exc}") from exc
        if not info.push:
            raise HTTPException(status_code=403, detail="你没有这个仓库的写权限")

    @app.get("/api/pairs")
    def list_pairs(request: Request):
        """列出配对。"""
        session = session_of(request)
        return [pair_view(pair) for pair in store.list_pairs(owner=session.username)]

    @app.post("/api/pairs")
    def add_pair(body: PairBody, request: Request):
        """新建配对。"""
        session = session_of(request)
        require_push(session.token, body.local_repo)
        try:
            pair = store.add_pair(
                body.local_repo,
                body.local_repo,
                body.remote_url,
                owner=session.username,
                remote_username=body.remote_username,
                remote_secret=vault.encrypt(body.remote_token),
                retention_days=body.retention_days,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return pair_view(pair)

    @app.patch("/api/pairs/{pair_id}")
    def edit_pair(pair_id: int, body: PairPatch, request: Request):
        """更新配对。"""
        owned_pair(request, pair_id)
        secret = None
        if body.remote_token:
            secret = vault.encrypt(body.remote_token)
        pair = store.update_pair(
            store.get_pair_by_id(pair_id).name,
            remote_url=body.remote_url,
            remote_username=body.remote_username,
            remote_secret=secret,
            retention_days=body.retention_days,
        )
        return pair_view(pair)

    @app.delete("/api/pairs/{pair_id}")
    def delete_pair(pair_id: int, request: Request):
        """删除配对。"""
        pair, _ = owned_pair(request, pair_id)
        store.delete_pair(pair.name)
        return {"ok": True}

    # ---------- 同步 ----------

    @app.post("/api/pairs/{pair_id}/compare")
    def compare(pair_id: int, request: Request):
        """只读比对，写回状态。"""
        pair, session = owned_pair(request, pair_id)
        service_for(session).compare(pair)
        return {"branches": [asdict(b) for b in store.list_branches(pair.id)]}

    @app.post("/api/pairs/{pair_id}/sync")
    def sync(pair_id: int, request: Request):
        """执行同步。"""
        pair, session = owned_pair(request, pair_id)
        service_for(session).sync_pair(pair)
        return {"branches": [asdict(b) for b in store.list_branches(pair.id)]}

    @app.post("/api/pairs/{pair_id}/branches/{branch:path}/push")
    def branch_push(pair_id: int, branch: str, request: Request):
        """单分支快进推送。"""
        pair, session = owned_pair(request, pair_id)
        try:
            outcome = service_for(session).push(pair, branch)
        except GitError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return asdict(outcome)

    @app.post("/api/pairs/{pair_id}/branches/{branch:path}/resolve")
    def branch_resolve(pair_id: int, branch: str, body: ResolveBody, request: Request):
        """冲突时选择使用本地或远程。"""
        pair, session = owned_pair(request, pair_id)
        try:
            outcome = service_for(session).resolve(pair, branch, body.prefer)
        except GitError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return asdict(outcome)

    @app.post("/api/pairs/{pair_id}/branches/{branch:path}/pull")
    def branch_pull(pair_id: int, branch: str, request: Request):
        """单分支快进拉取。"""
        pair, session = owned_pair(request, pair_id)
        try:
            outcome = service_for(session).pull(pair, branch)
        except GitError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return asdict(outcome)

    return app


app = create_app(os.environ.get("GITSYNC_CONFIG"))
