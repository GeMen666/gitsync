"""FastAPI 应用：登录、仓库列表。"""

from __future__ import annotations

import os
import secrets

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from dataclasses import asdict, replace

from .config import load_config
from .db import Store
from .gitops import GitError
from .gitea import Client, GiteaError, create_token, verify_login
from . import secretbox
from .sync import pull_branch, push_branch, sync_repo

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
    push_enabled: bool = True
    pull_enabled: bool = True
    retention_days: int = 7


class BranchAction(BaseModel):
    name: str
    branch: str


class PairPatch(BaseModel):
    remote_url: str | None = None
    remote_username: str | None = None
    remote_token: str | None = None
    push_enabled: bool | None = None
    pull_enabled: bool | None = None
    retention_days: int | None = None


def create_app(config_path: str | None = None) -> FastAPI:
    cfg = load_config(config_path)
    store = Store(cfg.db_path)
    app = FastAPI(title="gitsync", version="0.1.0")

    @app.middleware("http")
    async def no_cache(request: Request, call_next):
        # 状态随时在变，禁止浏览器缓存 API 响应
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def session_of(request: Request):
        sid = request.cookies.get(COOKIE)
        if not sid:
            raise HTTPException(status_code=401, detail="未登录")
        row = store.get_session(sid)
        if row is None:
            raise HTTPException(status_code=401, detail="登录已失效")
        return row

    @app.post("/api/login")
    def login(body: LoginBody, response: Response):
        try:
            user = verify_login(cfg.local_url, body.username, body.password)
        except GiteaError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

        name = f"gitsync-web-{secrets.token_hex(4)}"
        try:
            token = create_token(cfg.local_url, body.username, body.password, name)
        except GiteaError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        sid = secrets.token_urlsafe(32)
        store.create_session(sid, user["login"], token)
        response.set_cookie(
            COOKIE, sid, httponly=True, samesite="lax", max_age=MAX_AGE
        )
        return {"login": user["login"], "full_name": user.get("full_name")}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        sid = request.cookies.get(COOKIE)
        if sid:
            store.delete_session(sid)
        response.delete_cookie(COOKIE)
        return {"ok": True}

    @app.get("/api/me")
    def me(request: Request):
        session = session_of(request)
        client = Client(cfg.local_url, session["token"])
        try:
            user = client.get_user()
        except GiteaError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {
            "login": user["login"],
            "full_name": user.get("full_name"),
            "avatar_url": user.get("avatar_url"),
        }

    @app.get("/api/repos")
    def repos(request: Request):
        session = session_of(request)
        client = Client(cfg.local_url, session["token"])
        try:
            items = client.list_repos()
        except GiteaError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return [item.__dict__ for item in items]

    def client_of(request: Request) -> Client:
        session = session_of(request)
        return Client(cfg.local_url, session["token"])

    def require_push(client: Client, local_repo: str) -> None:
        try:
            info = client.get_repo(local_repo)
        except GiteaError as exc:
            raise HTTPException(status_code=404, detail=f"仓库不存在或无权访问：{exc}") from exc
        if not info.push:
            raise HTTPException(status_code=403, detail="你没有这个仓库的写权限")

    def public_repo(repo) -> dict:
        item = asdict(repo)
        item.pop("remote_secret", None)
        item["has_remote_secret"] = bool(repo.remote_secret)
        return item

    def owned_repo(request: Request, name: str):
        session = session_of(request)
        try:
            repo = store.get_repo(name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if repo.owner != session["username"]:
            raise HTTPException(status_code=404, detail="没有这个配对")
        return repo, session

    @app.get("/api/pairs")
    def list_pairs(request: Request):
        session = session_of(request)
        result = []
        for repo in store.list_repos(owner=session["username"]):
            item = public_repo(repo)
            item["status"] = [dict(row) for row in store.list_status(repo.id)]
            result.append(item)
        return result

    @app.post("/api/pairs")
    def add_pair(body: PairBody, request: Request):
        client = client_of(request)
        require_push(client, body.local_repo)
        session = session_of(request)
        try:
            repo = store.add_repo(
                body.local_repo,
                body.local_repo,
                body.remote_url,
                owner=session["username"],
                remote_username=body.remote_username,
                remote_secret=secretbox.encrypt(body.remote_token, cfg.db_path.parent),
                push=body.push_enabled,
                pull=body.pull_enabled,
                retention_days=body.retention_days,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return public_repo(repo)

    @app.patch("/api/pairs/{name:path}")
    def edit_pair(name: str, body: PairPatch, request: Request):
        owned_repo(request, name)
        secret = None
        if body.remote_token is not None and body.remote_token != "":
            secret = secretbox.encrypt(body.remote_token, cfg.db_path.parent)
        repo = store.update_repo(
            name,
            remote_url=body.remote_url,
            remote_username=body.remote_username,
            remote_secret=secret,
            push=body.push_enabled,
            pull=body.pull_enabled,
            retention_days=body.retention_days,
        )
        return public_repo(repo)

    @app.delete("/api/pairs/{name:path}")
    def delete_pair(name: str, request: Request):
        owned_repo(request, name)
        store.remove_repo(name)
        return {"ok": True}

    def user_cfg(session):
        # 同步用当前登录用户自己的令牌，权限和 Gitea 保持一致
        return replace(
            cfg, local_username=session["username"], local_token=session["token"]
        )

    @app.get("/api/pairs/{name:path}/live")
    def live_status(name: str, request: Request):
        repo, session = owned_repo(request, name)
        outcomes = sync_repo(repo, user_cfg(session), store, apply=False)
        keep = set()
        for item in outcomes:
            if item.branch == "-":
                continue
            keep.add(item.branch)
            store.save_status(
                repo.id, item.branch,
                local_sha=item.local_sha or None,
                remote_sha=item.remote_sha or None,
                status=item.status,
                backup_branch=item.backup_branch,
                backup_side=item.backup_side,
                detail=item.detail,
            )
        store.prune_status(repo.id, keep)
        return [asdict(o) for o in outcomes]

    @app.post("/api/pairs/{name:path}/sync")
    def run_sync(name: str, request: Request):
        repo, session = owned_repo(request, name)
        return [asdict(o) for o in sync_repo(repo, user_cfg(session), store)]

    @app.post("/api/branch/push")
    def branch_push(body: BranchAction, request: Request):
        repo, session = owned_repo(request, body.name)
        try:
            return asdict(push_branch(repo, user_cfg(session), store, body.branch))
        except GitError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/branch/pull")
    def branch_pull(body: BranchAction, request: Request):
        repo, session = owned_repo(request, body.name)
        try:
            return asdict(pull_branch(repo, user_cfg(session), store, body.branch))
        except GitError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app


app = create_app(os.environ.get("GITSYNC_CONFIG"))
