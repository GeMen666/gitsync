"""同步处理层：规则判定 + 调用 git 操作层。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..ports import (
    BranchState,
    Endpoint,
    GitError,
    GitTransport,
    GitWorkspace,
    Pair,
    SecretVault,
    Settings,
    StateStore,
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
    backup_missing: bool = False
    local_sha: str = ""
    remote_sha: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {"synced", "pending", "cleaned"}


class SyncService:
    """同步用例。依赖全部通过构造注入。"""

    def __init__(
        self,
        settings: Settings,
        store: StateStore,
        vault: SecretVault,
        transport: GitTransport,
    ):
        self.settings = settings
        self.store = store
        self.vault = vault
        self.transport = transport

    # ---------- 对外 ----------

    def compare(self, pair: Pair) -> list[Outcome]:
        """只读比对，结果写回状态表。"""
        outcomes = self._run(pair, apply=False)
        keep = set()
        for item in outcomes:
            if item.branch == "-":
                continue
            keep.add(item.branch)
            self.store.save_branch(
                pair.id, item.branch,
                local_sha=item.local_sha or None,
                remote_sha=item.remote_sha or None,
                status=item.status,
                backup_branch=item.backup_branch,
                backup_side=item.backup_side,
                detail=item.detail,
            )
        self.store.prune_branches(pair.id, keep)
        return outcomes

    def sync_pair(self, pair: Pair) -> list[Outcome]:
        """执行同步。"""
        return self._run(pair, apply=True)

    def resolve(self, pair: Pair, branch: str, prefer: str) -> Outcome:
        """按用户选择的一侧对齐两端，备份保留以便反悔。"""
        if prefer not in {"local", "remote"}:
            raise GitError(f"未知的选择：{prefer}")
        state = self.store.get_branch(pair.id, branch)
        if state is None or not state.backup_branch:
            raise GitError("没有可切换的备份")
        if state.backup_missing:
            raise GitError("备份已被清理，无法再切换")

        local, remote = self._endpoints(pair)
        local_refs = self.transport.list_refs(local)
        remote_refs = self.transport.list_refs(remote)
        lsha = local_refs.get(branch)
        rsha = remote_refs.get(branch)
        label = "本地" if prefer == "local" else "远程"
        detail = f"已使用{label}，备份存在"

        with self.transport.workspace() as ws:
            # 先把两端对象取进来，备份和覆盖都要用
            if lsha:
                ws.fetch(local, branch)
            if rsha:
                ws.fetch(remote, branch)

            if lsha and rsha and lsha != rsha:
                # 比对后还没落地：采用选中的一侧，另一侧存为备份
                target = lsha if prefer == "local" else rsha
                other_side = "remote" if prefer == "local" else "local"
                other_sha = rsha if prefer == "local" else lsha
                backup = _unique_backup(
                    _backup_name(branch, datetime.now(timezone.utc)),
                    set(local_refs) | set(remote_refs),
                )
                backup_ep = local if other_side == "local" else remote
                ws.push(backup_ep, other_sha, backup)
                backup_side = other_side
            else:
                # 已经自动对齐：另一版本只存在于备份里
                other_side = state.backup_side or "remote"
                backup_ep = local if other_side == "local" else remote
                other_sha = ws.fetch(backup_ep, state.backup_branch)
                current = lsha or rsha or other_sha

                if prefer != other_side or other_sha == current:
                    # 选的是当前生效的版本，只更新记录
                    self.store.save_branch(
                        pair.id, branch,
                        local_sha=lsha, remote_sha=rsha, status="synced",
                        backup_branch=state.backup_branch,
                        backup_side=state.backup_side,
                        backup_missing=False, detail=detail,
                    )
                    return Outcome(pair.name, branch, "synced", detail,
                                   backup_branch=state.backup_branch,
                                   backup_side=state.backup_side)

                # 选的是备份里的版本：先把当前版本存成新备份，再对齐
                target = other_sha
                backup = _unique_backup(
                    _backup_name(branch, datetime.now(timezone.utc)),
                    set(local_refs) | set(remote_refs),
                )
                backup_side = "remote" if other_side == "local" else "local"
                ws.push(local if backup_side == "local" else remote, current, backup)

            for ep, cur in ((local, lsha), (remote, rsha)):
                if cur == target:
                    continue
                if cur:
                    ws.overwrite(ep, target, branch, expected=cur)
                else:
                    ws.push(ep, target, branch)

        local_refs = self.transport.list_refs(local)
        remote_refs = self.transport.list_refs(remote)
        self.store.save_branch(
            pair.id, branch,
            local_sha=local_refs.get(branch),
            remote_sha=remote_refs.get(branch),
            status="synced",
            backup_branch=backup,
            backup_side=backup_side,
            backup_missing=False,
            detail=detail,
        )
        return Outcome(pair.name, branch, "synced", detail,
                       backup_branch=backup, backup_side=backup_side)

    def push(self, pair: Pair, branch: str) -> Outcome:
        """单分支快进推送。"""
        return self._manual_move(pair, branch, "push")

    def pull(self, pair: Pair, branch: str) -> Outcome:
        """单分支快进拉取。"""
        return self._manual_move(pair, branch, "pull")

    # ---------- 内部 ----------

    def _remote_token(self, pair: Pair) -> str:
        if pair.remote_secret:
            return self.vault.decrypt(pair.remote_secret)
        return self.settings.remote_token

    def _endpoints(self, pair: Pair) -> tuple[Endpoint, Endpoint]:
        local = Endpoint(
            "local",
            f"{self.settings.local_url}/{pair.local_repo}.git",
            self.settings.local_username,
            self.settings.local_token,
        )
        remote = Endpoint(
            "remote",
            pair.remote_url,
            pair.remote_username or self.settings.remote_username,
            self._remote_token(pair),
        )
        return local, remote

    def _run(self, pair: Pair, *, apply: bool) -> list[Outcome]:
        local, remote = self._endpoints(pair)
        try:
            local_refs = self.transport.list_refs(local)
            remote_refs = self.transport.list_refs(remote)
        except GitError as exc:
            return [Outcome(pair.name, "-", "error", f"读取远端失败：{exc}")]

        branches = sorted(b for b in set(local_refs) | set(remote_refs) if not BAK.match(b))
        outcomes: list[Outcome] = []

        with self.transport.workspace() as ws:
            for branch in branches:
                try:
                    outcome = self._sync_branch(
                        pair, ws, branch,
                        local_refs.get(branch), remote_refs.get(branch),
                        local, remote, dry=not apply,
                        taken_local=set(local_refs), taken_remote=set(remote_refs),
                        existing=self.store.get_branch(pair.id, branch),
                    )
                except GitError as exc:
                    outcome = Outcome(pair.name, branch, "error", str(exc))
                if not apply:
                    outcome.local_sha = local_refs.get(branch) or ""
                    outcome.remote_sha = remote_refs.get(branch) or ""
                outcomes.append(outcome)

            if apply:
                self._persist(pair, outcomes, local, remote)
                outcomes.extend(self._cleanup(pair, ws, local, remote))

        return outcomes

    def _sync_branch(
        self,
        pair: Pair,
        ws: GitWorkspace,
        branch: str,
        lsha: str | None,
        rsha: str | None,
        local: Endpoint,
        remote: Endpoint,
        *,
        dry: bool,
        taken_local: set[str],
        taken_remote: set[str],
        existing: BranchState | None = None,
    ) -> Outcome:
        verb = "将" if dry else ""

        if lsha == rsha:
            if existing and existing.backup_branch:
                if existing.backup_missing:
                    return Outcome(pair.name, branch, "synced", "备份已删除",
                                   backup_branch=existing.backup_branch,
                                   backup_side=existing.backup_side,
                                   backup_missing=True)
                if existing.status == "synced":
                    return Outcome(
                        pair.name, branch, "synced", existing.detail,
                        backup_branch=existing.backup_branch,
                        backup_side=existing.backup_side,
                    )
                winner = "本地" if existing.backup_side == "remote" else "远程"
                return Outcome(
                    pair.name, branch, "pending",
                    f"{winner}较新，已使用{winner}，备份存在",
                    backup_branch=existing.backup_branch,
                    backup_side=existing.backup_side,
                )
            return Outcome(pair.name, branch, "synced", (lsha or "")[:8])

        if lsha and not rsha:
            if not dry:
                ws.fetch(local, branch)
                ws.push(remote, lsha, branch)
            status = "need_push" if dry else "synced"
            return Outcome(pair.name, branch, status, f"{verb}创建远程分支 {lsha[:8]}")

        if rsha and not lsha:
            if not dry:
                ws.fetch(remote, branch)
                ws.push(local, rsha, branch)
            status = "need_pull" if dry else "synced"
            return Outcome(pair.name, branch, status, f"{verb}创建本地分支 {rsha[:8]}")

        assert lsha and rsha
        ws.fetch(local, branch)
        ws.fetch(remote, branch)
        local_ct = ws.commit_time(lsha)
        remote_ct = ws.commit_time(rsha)
        now = datetime.now(timezone.utc)

        if local_ct >= remote_ct:
            backup = _unique_backup(_backup_name(branch, now), taken_remote)
            if not dry:
                ws.push(remote, rsha, backup)
                ws.overwrite(remote, lsha, branch, expected=rsha)
            return Outcome(
                pair.name, branch, "pending",
                "本地较新，已使用本地，备份存在",
                backup_branch=backup, backup_side="remote",
            )

        backup = _unique_backup(_backup_name(branch, now), taken_local)
        if not dry:
            ws.push(local, lsha, backup)
            ws.overwrite(local, rsha, branch, expected=lsha)
        return Outcome(
            pair.name, branch, "pending",
            "远程较新，已使用远程，备份存在",
            backup_branch=backup, backup_side="local",
        )

    def _persist(
        self,
        pair: Pair,
        outcomes: list[Outcome],
        local: Endpoint,
        remote: Endpoint,
    ) -> None:
        try:
            local_refs = self.transport.list_refs(local)
            remote_refs = self.transport.list_refs(remote)
        except GitError:
            local_refs, remote_refs = {}, {}
        for outcome in outcomes:
            if outcome.branch == "-":
                continue
            self.store.save_branch(
                pair.id, outcome.branch,
                local_sha=local_refs.get(outcome.branch),
                remote_sha=remote_refs.get(outcome.branch),
                status=outcome.status,
                backup_branch=outcome.backup_branch,
                backup_side=outcome.backup_side,
                backup_missing=outcome.backup_missing,
                detail=outcome.detail,
            )

    def _cleanup(
        self,
        pair: Pair,
        ws: GitWorkspace,
        local: Endpoint,
        remote: Endpoint,
    ) -> list[Outcome]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=pair.backup_retention_days)
        outcomes: list[Outcome] = []
        for side, ep in (("本地", local), ("远程", remote)):
            try:
                refs = self.transport.list_refs(ep)
            except GitError:
                continue
            for branch in refs:
                match = BAK.match(branch)
                if not match:
                    continue
                raw = match.group("ts")
                fmt = "%Y%m%d%H%M%S" if len(raw) == 14 else "%Y%m%d%H%M"
                stamp = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
                if stamp >= cutoff:
                    continue
                try:
                    ws.delete(ep, branch)
                except GitError as exc:
                    outcomes.append(Outcome(pair.name, branch, "error", str(exc)))
                    continue
                base = branch[: branch.rindex(".bak.")]
                state = self.store.get_branch(pair.id, base)
                if state and state.backup_branch == branch:
                    self.store.mark_backup_missing(pair.id, base, "备份已删除")
                outcomes.append(
                    Outcome(pair.name, branch, "cleaned",
                            f"已清理{side}备份（超过 {pair.backup_retention_days} 天）")
                )
        return outcomes

    def _manual_move(self, pair: Pair, branch: str, direction: str) -> Outcome:
        local, remote = self._endpoints(pair)
        local_refs = self.transport.list_refs(local)
        remote_refs = self.transport.list_refs(remote)

        source = local if direction == "push" else remote
        target = remote if direction == "push" else local
        src_sha = (local_refs if direction == "push" else remote_refs).get(branch)
        dst_sha = (remote_refs if direction == "push" else local_refs).get(branch)

        if not src_sha:
            side = "本地" if direction == "push" else "远程"
            raise GitError(f"{side}侧没有分支 {branch}")
        if src_sha == dst_sha:
            return Outcome(pair.name, branch, "synced", "两端已经一致")

        with self.transport.workspace() as ws:
            ws.fetch(source, branch)
            ws.push(target, src_sha, branch)

        status = "synced"
        detail = "已推送到远程" if direction == "push" else "已从远程拉取"
        self.store.save_branch(
            pair.id, branch,
            local_sha=self.transport.list_refs(local).get(branch),
            remote_sha=self.transport.list_refs(remote).get(branch),
            status=status, detail=detail,
        )
        return Outcome(pair.name, branch, status, detail)


def _backup_name(branch: str, now: datetime) -> str:
    return f"{branch}.bak.{now:%Y%m%d%H%M%S}"


def _unique_backup(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    index = 2
    while f"{name}-{index}" in taken:
        index += 1
    return f"{name}-{index}"
