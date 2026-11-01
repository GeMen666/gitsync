"""git 操作层的实现：通过临时裸仓库中转。"""

from __future__ import annotations

import os
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..ports import Endpoint, GitError, LeaseMismatch, NonFastForward

ASKPASS = """#!/bin/sh
case "$1" in
  *[Uu]sername*) printf '%s\\n' "$GITSYNC_ASKPASS_USER" ;;
  *) printf '%s\\n' "$GITSYNC_ASKPASS_PASS" ;;
esac
"""


class CliGitWorkspace:
    """临时工作区，退出时删除。"""
    def __init__(self, root: Path, askpass: Path):
        self.root = root
        self.askpass = askpass
        self.cache = root / "repo.git"
        self._run(["init", "--bare", "--quiet", str(self.cache)], cwd=root, env=os.environ.copy())

    # ---------- 内部 ----------

    def _env(self, ep: Endpoint) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": str(self.askpass),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GITSYNC_ASKPASS_USER": ep.username,
                "GITSYNC_ASKPASS_PASS": ep.token,
            }
        )
        return env

    def _run(self, args: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            ["git", "-c", "credential.helper=", *args],
            cwd=str(cwd), env=env, capture_output=True, text=True, timeout=1800,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.strip()
            if "non-fast-forward" in stderr or "fetch first" in stderr:
                raise NonFastForward(f"git {args[0]} 被拒绝（非快进）", stderr)
            if "stale info" in stderr or "cannot lock ref" in stderr:
                raise LeaseMismatch(f"git {args[0]} 被拒绝（远端已变化）", stderr)
            raise GitError(f"git {args[0]} 失败", stderr)
        return proc

    # ---------- GitWorkspace ----------

    def fetch(self, ep: Endpoint, branch: str) -> str:
        """取分支到工作区。"""
        refspec = f"+refs/heads/{branch}:refs/sync/{branch}"
        self._run(["fetch", "--no-tags", "--quiet", ep.url, refspec],
                  cwd=self.cache, env=self._env(ep))
        proc = self._run(["rev-parse", f"refs/sync/{branch}"],
                         cwd=self.cache, env=self._env(ep))
        return proc.stdout.strip()

    def commit_time(self, sha: str) -> int:
        """提交时间。"""
        proc = self._run(["log", "-1", "--format=%ct", sha],
                         cwd=self.cache, env=os.environ.copy())
        return int(proc.stdout.strip())

    def push(self, ep: Endpoint, sha: str, branch: str) -> None:
        """快进推送。"""
        self._run(["push", "--quiet", ep.url, f"{sha}:refs/heads/{branch}"],
                  cwd=self.cache, env=self._env(ep))

    def overwrite(self, ep: Endpoint, sha: str, branch: str, *, expected: str) -> None:
        """带 lease 的强制覆盖。"""
        if not expected:
            raise GitError("覆盖必须提供 expected")
        self._run(
            ["push", "--quiet",
             f"--force-with-lease=refs/heads/{branch}:{expected}",
             ep.url, f"{sha}:refs/heads/{branch}"],
            cwd=self.cache, env=self._env(ep),
        )

    def delete(self, ep: Endpoint, branch: str) -> None:
        """删除分支。"""
        self._run(["push", "--quiet", ep.url, "--delete", f"refs/heads/{branch}"],
                  cwd=self.cache, env=self._env(ep))


class CliGitTransport:
    """用系统 git 实现 GitTransport。"""

    def __init__(self, temp_dir: Path):
        self.temp_dir = temp_dir
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        runtime = temp_dir / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        runtime.chmod(0o700)
        self._askpass = runtime / "askpass.sh"
        self._askpass.write_text(ASKPASS)
        self._askpass.chmod(0o700)

    def list_refs(self, ep: Endpoint) -> dict[str, str]:
        """拉取整体状态。"""
        with tempfile.TemporaryDirectory(dir=self.temp_dir, prefix="ls-") as tmp:
            env = os.environ.copy()
            env.update(
                {
                    "GIT_TERMINAL_PROMPT": "0",
                    "GIT_ASKPASS": str(self._askpass),
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GITSYNC_ASKPASS_USER": ep.username,
                    "GITSYNC_ASKPASS_PASS": ep.token,
                }
            )
            proc = subprocess.run(
                ["git", "-c", "credential.helper=", "ls-remote", "--heads", ep.url],
                cwd=tmp, env=env, capture_output=True, text=True, timeout=1800,
            )
        if proc.returncode != 0:
            raise GitError("读取分支列表失败", proc.stderr.strip())
        refs: dict[str, str] = {}
        for line in proc.stdout.splitlines():
            sha, _, ref = line.partition("\t")
            if ref.startswith("refs/heads/") and sha:
                refs[ref[len("refs/heads/"):]] = sha
        return refs

    @contextmanager
    def workspace(self) -> Iterator[CliGitWorkspace]:
        with tempfile.TemporaryDirectory(dir=self.temp_dir, prefix="ws-") as tmp:
            yield CliGitWorkspace(Path(tmp), self._askpass)
