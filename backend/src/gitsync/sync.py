"""同步引擎：四条规则 + 归档 + 过期清理。"""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Config
from .db import Repo, Store
from . import secretbox
from .gitops import (
    GitError,
    commit_time,
    delete_branch,
    env_for,
    fetch_branch,
    init_bare,
    ls_remote,
    push_sha,
)

BAK = re.compile(r"^.+\.bak\.(?P<ts>\d{12,14})(?:-\d+)?$")


@dataclass
class Outcome:
    repo: str
    branch: str
    status: str
    detail: str = ""
    backup_branch: str = ""
    backup_side: str = ""
    local_sha: str = ""
    remote_sha: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {"synced", "pushed", "pulled", "pending", "cleaned"}


def local_url(cfg: Config, repo: Repo) -> str:
    return f"{cfg.local_url}/{repo.local_repo}.git"


def _backup_name(branch: str, now: datetime) -> str:
    return f"{branch}.bak.{now:%Y%m%d%H%M%S}"


def _unique_backup(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    index = 2
    while f"{name}-{index}" in taken:
        index += 1
    return f"{name}-{index}"


def _remote_env(repo: Repo, cfg: Config, runtime: Path) -> dict[str, str]:
    user = repo.remote_username or cfg.remote_username
    if repo.remote_secret:
        password = secretbox.decrypt(repo.remote_secret, cfg.db_path.parent)
    else:
        password = cfg.remote_token
    return env_for(user, password, runtime)


def sync_repo(repo: Repo, cfg: Config, store: Store | None = None, *, apply: bool = True) -> list[Outcome]:
    runtime = cfg.temp_dir / "runtime"
    local_env = env_for(cfg.local_username, cfg.local_token, runtime)
    remote_env = _remote_env(repo, cfg, runtime)
    lurl = local_url(cfg, repo)
    rurl = repo.remote_url

    try:
        local_heads = ls_remote(lurl, local_env)
        remote_heads = ls_remote(rurl, remote_env)
    except GitError as exc:
        return [Outcome(repo.name, "-", "error", f"读取远端失败：{exc}")]

    branches = sorted(b for b in set(local_heads) | set(remote_heads) if not BAK.match(b))

    cfg.temp_dir.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(dir=cfg.temp_dir, prefix="run-"))
    cache = workdir / "repo.git"
    outcomes: list[Outcome] = []
    try:
        init_bare(cache)
        for branch in branches:
            try:
                outcome = _sync_branch(
                    repo, cache, branch,
                    local_heads.get(branch), remote_heads.get(branch),
                    lurl, rurl, local_env, remote_env,
                    dry=not apply,
                    taken_local=set(local_heads),
                    taken_remote=set(remote_heads),
                )
            except GitError as exc:
                outcome = Outcome(repo.name, branch, "error", str(exc))
            if not apply:
                outcome.local_sha = local_heads.get(branch) or ""
                outcome.remote_sha = remote_heads.get(branch) or ""
            outcomes.append(outcome)

        # 同步后重新读取两端，状态里记录的才是最新 SHA
        try:
            local_after = ls_remote(lurl, local_env)
            remote_after = ls_remote(rurl, remote_env)
        except GitError:
            local_after, remote_after = local_heads, remote_heads
        for outcome in outcomes:
            if outcome.branch == "-" or store is None or not apply:
                continue
            store.save_status(
                repo.id, outcome.branch,
                local_sha=local_after.get(outcome.branch),
                remote_sha=remote_after.get(outcome.branch),
                status=outcome.status,
                backup_branch=outcome.backup_branch,
                backup_side=outcome.backup_side,
                detail=outcome.detail,
            )

        outcomes.extend(_cleanup(repo, cfg, cache, lurl, local_env, rurl, remote_env, dry=not apply))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    return outcomes


def _sync_branch(
    repo: Repo,
    cache: Path,
    branch: str,
    lsha: str | None,
    rsha: str | None,
    lurl: str,
    rurl: str,
    local_env: dict[str, str],
    remote_env: dict[str, str],
    dry: bool,
    taken_local: set[str] | None = None,
    taken_remote: set[str] | None = None,
) -> Outcome:
    verb = "将" if dry else ""

    if lsha == rsha:
        return Outcome(repo.name, branch, "synced", (lsha or "")[:8])

    if lsha and not rsha:
        if not repo.push_enabled:
            return Outcome(repo.name, branch, "skipped", "远程缺失，push 已关闭")
        if not dry:
            fetch_branch(cache, lurl, "local", branch, local_env)
            push_sha(cache, rurl, lsha, branch, remote_env)
        return Outcome(repo.name, branch, "pushed", f"{verb}创建远程分支 {lsha[:8]}")

    if rsha and not lsha:
        if not repo.pull_enabled:
            return Outcome(repo.name, branch, "skipped", "本地缺失，pull 已关闭")
        if not dry:
            fetch_branch(cache, rurl, "remote", branch, remote_env)
            push_sha(cache, lurl, rsha, branch, local_env)
        return Outcome(repo.name, branch, "pulled", f"{verb}创建本地分支 {rsha[:8]}")

    assert lsha and rsha
    fetch_branch(cache, lurl, "local", branch, local_env)
    fetch_branch(cache, rurl, "remote", branch, remote_env)
    local_ct = commit_time(cache, lsha)
    remote_ct = commit_time(cache, rsha)
    now = datetime.now(timezone.utc)
    backup_remote = _unique_backup(_backup_name(branch, now), taken_remote or set())
    backup_local = _unique_backup(_backup_name(branch, now), taken_local or set())

    if local_ct >= remote_ct:
        if not repo.push_enabled:
            return Outcome(repo.name, branch, "skipped", "本地较新，push 已关闭")
        if not dry:
            push_sha(cache, rurl, rsha, backup_remote, remote_env)
            push_sha(cache, rurl, lsha, branch, remote_env, force=True, expected=rsha)
        return Outcome(
            repo.name, branch, "pending",
            f"{verb}本地较新，远程旧版存为 {backup_remote}",
            backup_branch=backup_remote, backup_side="remote",
        )

    if not repo.pull_enabled:
        return Outcome(repo.name, branch, "skipped", "远程较新，pull 已关闭")
    if not dry:
        push_sha(cache, lurl, lsha, backup_local, local_env)
        push_sha(cache, lurl, rsha, branch, local_env, force=True, expected=lsha)
    return Outcome(
        repo.name, branch, "pending",
        f"{verb}远程较新，本地旧版存为 {backup_local}",
        backup_branch=backup_local, backup_side="local",
    )


def _cleanup(
    repo: Repo,
    cfg: Config,
    cache: Path,
    lurl: str,
    local_env: dict[str, str],
    rurl: str,
    remote_env: dict[str, str],
    dry: bool,
) -> list[Outcome]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=repo.backup_retention_days)
    outcomes: list[Outcome] = []
    for side, url, env in (("本地", lurl, local_env), ("远程", rurl, remote_env)):
        try:
            heads = ls_remote(url, env)
        except GitError:
            continue
        for branch in heads:
            match = BAK.match(branch)
            if not match:
                continue
            raw_ts = match.group("ts")
            fmt = "%Y%m%d%H%M%S" if len(raw_ts) == 14 else "%Y%m%d%H%M"
            stamp = datetime.strptime(raw_ts, fmt).replace(tzinfo=timezone.utc)
            if stamp >= cutoff:
                continue
            if not dry:
                try:
                    delete_branch(cache, url, branch, env)
                except GitError as exc:
                    outcomes.append(Outcome(repo.name, branch, "error", str(exc)))
                    continue
            verb = "将" if dry else "已"
            outcomes.append(
                Outcome(
                    repo.name, branch, "cleaned",
                    f"{verb}清理{side}归档（超过 {repo.backup_retention_days} 天）",
                )
            )
    return outcomes


def _manual_move(repo: Repo, cfg: Config, store: Store, branch: str, direction: str) -> Outcome:
    """手动把某个分支整体移到另一侧；被覆盖的一侧先归档。"""
    runtime = cfg.temp_dir / "runtime"
    local_env = env_for(cfg.local_username, cfg.local_token, runtime)
    remote_env = _remote_env(repo, cfg, runtime)
    lurl = local_url(cfg, repo)
    rurl = repo.remote_url

    heads = {"local": ls_remote(lurl, local_env), "remote": ls_remote(rurl, remote_env)}
    source, target = ("local", "remote") if direction == "push" else ("remote", "local")
    src_sha = heads[source].get(branch)
    dst_sha = heads[target].get(branch)

    if not src_sha:
        side = "本地" if source == "local" else "远程"
        raise GitError(f"{side}侧没有分支 {branch}")

    sides = {"local": (lurl, local_env), "remote": (rurl, remote_env)}
    src_url, src_env = sides[source]
    dst_url, dst_env = sides[target]

    if src_sha == dst_sha:
        return Outcome(repo.name, branch, "synced", "两端已经一致")

    workdir = Path(tempfile.mkdtemp(dir=cfg.temp_dir, prefix="manual-"))
    cache = workdir / "repo.git"
    backup = ""
    try:
        init_bare(cache)
        fetch_branch(cache, src_url, source, branch, src_env)
        if dst_sha:
            # 归档要推目标侧现有的提交，必须先把对象取到本地
            fetch_branch(cache, dst_url, target, branch, dst_env)
            backup = _unique_backup(
                _backup_name(branch, datetime.now(timezone.utc)), set(heads[target])
            )
            push_sha(cache, dst_url, dst_sha, backup, dst_env)
            push_sha(cache, dst_url, src_sha, branch, dst_env, force=True, expected=dst_sha)
        else:
            push_sha(cache, dst_url, src_sha, branch, dst_env)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    after_local = ls_remote(lurl, local_env)
    after_remote = ls_remote(rurl, remote_env)
    if direction == "push":
        status, detail = "pushed", f"已手动推送到远程（旧版存为 {backup}）" if backup else "已手动推送到远程"
    else:
        status, detail = "pulled", f"已手动从远程拉取（旧版存为 {backup}）" if backup else "已手动从远程拉取"

    store.save_status(
        repo.id, branch,
        local_sha=after_local.get(branch),
        remote_sha=after_remote.get(branch),
        status=status,
        backup_branch=backup,
        backup_side=target if backup else "",
        detail=detail,
    )
    return Outcome(repo.name, branch, status, detail)


def push_branch(repo: Repo, cfg: Config, store: Store, branch: str) -> Outcome:
    return _manual_move(repo, cfg, store, branch, "push")


def pull_branch(repo: Repo, cfg: Config, store: Store, branch: str) -> Outcome:
    return _manual_move(repo, cfg, store, branch, "pull")
