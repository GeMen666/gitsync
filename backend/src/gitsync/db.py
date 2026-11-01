"""SQLite 状态存储（线程安全）。"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    owner                  TEXT NOT NULL DEFAULT '',
    user_id                INTEGER,
    name                   TEXT UNIQUE NOT NULL,
    local_repo             TEXT NOT NULL,
    remote_url             TEXT NOT NULL,
    remote_username        TEXT NOT NULL DEFAULT '',
    remote_secret          TEXT NOT NULL DEFAULT '',
    push_enabled           INTEGER NOT NULL DEFAULT 1,
    pull_enabled           INTEGER NOT NULL DEFAULT 1,
    backup_retention_days  INTEGER NOT NULL DEFAULT 7,
    created_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    username   TEXT NOT NULL,
    token      TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS branch_status (
    repo_id       INTEGER NOT NULL,
    branch        TEXT NOT NULL,
    local_sha     TEXT,
    remote_sha    TEXT,
    status        TEXT NOT NULL,
    backup_branch TEXT NOT NULL DEFAULT '',
    backup_side   TEXT NOT NULL DEFAULT '',
    frozen        INTEGER NOT NULL DEFAULT 0,
    detail        TEXT,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (repo_id, branch)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Repo:
    id: int
    owner: str
    name: str
    local_repo: str
    remote_url: str
    remote_username: str
    remote_secret: str
    push_enabled: bool
    pull_enabled: bool
    backup_retention_days: int


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock, self.conn:
            self.conn.executescript(SCHEMA)
            columns = [r[1] for r in self.conn.execute("PRAGMA table_info(repos)")]
            if "owner" not in columns:
                self.conn.execute(
                    "ALTER TABLE repos ADD COLUMN owner TEXT NOT NULL DEFAULT ''"
                )
            status_cols = [r[1] for r in self.conn.execute("PRAGMA table_info(branch_status)")]
            for column, ddl in (
                ("backup_branch", "TEXT NOT NULL DEFAULT ''"),
                ("backup_side", "TEXT NOT NULL DEFAULT ''"),
                ("frozen", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if column not in status_cols:
                    self.conn.execute(
                        f"ALTER TABLE branch_status ADD COLUMN {column} {ddl}"
                    )
            for column in ("remote_username", "remote_secret"):
                if column not in columns:
                    self.conn.execute(
                        f"ALTER TABLE repos ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                    )

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # ---------- 仓库 ----------

    def add_repo(
        self,
        name: str,
        local_repo: str,
        remote_url: str,
        *,
        owner: str = "",
        remote_username: str = "",
        remote_secret: str = "",
        push: bool = True,
        pull: bool = True,
        retention_days: int = 7,
    ) -> Repo:
        owner, _, repo = local_repo.partition("/")
        if not owner or not repo:
            raise ValueError(f"本地仓库应写成 owner/name：{local_repo!r}")
        with self._lock, self.conn:
            self.conn.execute(
                """INSERT INTO repos
                   (owner, name, local_repo, remote_url, remote_username, remote_secret,
                    push_enabled, pull_enabled, backup_retention_days, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    owner, name, local_repo, remote_url, remote_username, remote_secret,
                    int(push), int(pull), retention_days, _now(),
                ),
            )
        return self.get_repo(name)

    def get_repo(self, name: str) -> Repo:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM repos WHERE name = ?", (name,)
            ).fetchone()
        if row is None:
            raise KeyError(f"没有名为 {name!r} 的仓库")
        return _row(row)

    def list_repos(self, owner: str | None = None) -> list[Repo]:
        with self._lock:
            if owner is None:
                rows = self.conn.execute("SELECT * FROM repos ORDER BY name").fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM repos WHERE owner = ? ORDER BY name", (owner,)
                ).fetchall()
        return [_row(r) for r in rows]

    def update_repo(
        self,
        name: str,
        *,
        remote_url: str | None = None,
        remote_username: str | None = None,
        remote_secret: str | None = None,
        push: bool | None = None,
        pull: bool | None = None,
        retention_days: int | None = None,
    ) -> Repo:
        repo = self.get_repo(name)
        with self._lock, self.conn:
            if remote_url is not None:
                self.conn.execute("UPDATE repos SET remote_url = ? WHERE id = ?", (remote_url, repo.id))
            if remote_username is not None:
                self.conn.execute(
                    "UPDATE repos SET remote_username = ? WHERE id = ?", (remote_username, repo.id)
                )
            if remote_secret is not None:
                self.conn.execute(
                    "UPDATE repos SET remote_secret = ? WHERE id = ?", (remote_secret, repo.id)
                )
            if push is not None:
                self.conn.execute("UPDATE repos SET push_enabled = ? WHERE id = ?", (int(push), repo.id))
            if pull is not None:
                self.conn.execute("UPDATE repos SET pull_enabled = ? WHERE id = ?", (int(pull), repo.id))
            if retention_days is not None:
                self.conn.execute(
                    "UPDATE repos SET backup_retention_days = ? WHERE id = ?",
                    (retention_days, repo.id),
                )
        return self.get_repo(name)

    def remove_repo(self, name: str) -> None:
        repo = self.get_repo(name)
        with self._lock, self.conn:
            self.conn.execute("DELETE FROM branch_status WHERE repo_id = ?", (repo.id,))
            self.conn.execute("DELETE FROM repos WHERE id = ?", (repo.id,))

    # ---------- 会话 ----------

    def create_session(self, sid: str, username: str, token: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO sessions (id, username, token, created_at) VALUES (?, ?, ?, ?)",
                (sid, username, token, _now()),
            )

    def get_session(self, sid: str) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (sid,)
            ).fetchone()

    def delete_session(self, sid: str) -> None:
        with self._lock, self.conn:
            self.conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))

    # ---------- 分支状态 ----------

    def save_status(
        self,
        repo_id: int,
        branch: str,
        *,
        local_sha: str | None,
        remote_sha: str | None,
        status: str,
        backup_branch: str | None = None,
        backup_side: str | None = None,
        detail: str = "",
    ) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                """INSERT INTO branch_status
                   (repo_id, branch, local_sha, remote_sha, status, backup_branch, backup_side, detail, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(repo_id, branch) DO UPDATE SET
                     local_sha = excluded.local_sha,
                     remote_sha = excluded.remote_sha,
                     status = excluded.status,
                     backup_branch = excluded.backup_branch,
                     backup_side = excluded.backup_side,
                     detail = excluded.detail,
                     updated_at = excluded.updated_at""",
                (repo_id, branch, local_sha, remote_sha, status,
                 backup_branch or "", backup_side or "", detail, _now()),
            )

    def get_branch_status(self, repo_id: int, branch: str) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM branch_status WHERE repo_id = ? AND branch = ?",
                (repo_id, branch),
            ).fetchone()

    def prune_status(self, repo_id: int, keep: set[str]) -> None:
        with self._lock, self.conn:
            rows = self.conn.execute(
                "SELECT branch FROM branch_status WHERE repo_id = ?", (repo_id,)
            ).fetchall()
            for row in rows:
                if row["branch"] not in keep:
                    self.conn.execute(
                        "DELETE FROM branch_status WHERE repo_id = ? AND branch = ?",
                        (repo_id, row["branch"]),
                    )

    def list_status(self, repo_id: int) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM branch_status WHERE repo_id = ? ORDER BY branch", (repo_id,)
            ).fetchall()


def _row(row: sqlite3.Row) -> Repo:
    return Repo(
        id=row["id"],
        owner=row["owner"],
        name=row["name"],
        local_repo=row["local_repo"],
        remote_url=row["remote_url"],
        remote_username=row["remote_username"],
        remote_secret=row["remote_secret"],
        push_enabled=bool(row["push_enabled"]),
        pull_enabled=bool(row["pull_enabled"]),
        backup_retention_days=row["backup_retention_days"],
    )
