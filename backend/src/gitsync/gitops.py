"""git 调用层。

token 不写进 URL、不进 argv、不进 git config，只通过环境变量交给 GIT_ASKPASS。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ASKPASS = """#!/bin/sh
case "$1" in
  *[Uu]sername*) printf '%s\\n' "$GITSYNC_ASKPASS_USER" ;;
  *) printf '%s\\n' "$GITSYNC_ASKPASS_PASS" ;;
esac
"""


class GitError(Exception):
    def __init__(self, message: str, stderr: str = "", side: str = ""):
        super().__init__(message if not stderr else f"{message}: {' | '.join(stderr.splitlines()[:3])}")
        self.stderr = stderr
        self.side = side


def env_for(username: str, token: str, runtime_dir: Path) -> dict[str, str]:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    script = runtime_dir / "askpass.sh"
    script.write_text(ASKPASS)
    script.chmod(0o700)

    env = os.environ.copy()
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": str(script),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GITSYNC_ASKPASS_USER": username,
            "GITSYNC_ASKPASS_PASS": token,
        }
    )
    return env


def _run(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-c", "credential.helper=", *args],
        env=env,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if proc.returncode != 0:
        raise GitError(f"git {args[0]} 失败", proc.stderr.strip()[:500])
    return proc


def init_bare(path: Path) -> None:
    if not (path / "HEAD").exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        _run(["init", "--bare", "--quiet", str(path)])


def ls_remote(url: str, env: dict[str, str]) -> dict[str, str]:
    proc = _run(["ls-remote", "--heads", url], env=env)
    heads: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.startswith("refs/heads/") and sha:
            heads[ref[len("refs/heads/"):]] = sha
    return heads


def fetch_branch(
    cache: Path, url: str, side: str, branch: str, env: dict[str, str]
) -> None:
    refspec = f"+refs/heads/{branch}:refs/gitsync/{side}/{branch}"
    _run(["fetch", "--no-tags", "--quiet", url, refspec], env=env, cwd=cache)


def commit_time(cache: Path, sha: str) -> int:
    proc = _run(["log", "-1", "--format=%ct", sha], cwd=cache)
    return int(proc.stdout.strip())


def push_sha(
    cache: Path,
    url: str,
    sha: str,
    branch: str,
    env: dict[str, str],
    *,
    force: bool = False,
    expected: str | None = None,
) -> None:
    args = ["push", "--quiet"]
    if force:
        if expected is None:
            raise GitError("强制推送必须提供 expected")
        args.append(f"--force-with-lease=refs/heads/{branch}:{expected}")
    args.extend([url, f"{sha}:refs/heads/{branch}"])
    _run(args, env=env, cwd=cache)


def delete_branch(cache: Path, url: str, branch: str, env: dict[str, str]) -> None:
    _run(["push", "--quiet", url, "--delete", f"refs/heads/{branch}"], env=env, cwd=cache)
