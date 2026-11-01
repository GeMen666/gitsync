"""SQLite 存储实现。"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from ..ports.store import BranchState, Pair, Session

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
    backup_missing INTEGER NOT NULL DEFAULT 0,
    frozen        INTEGER NOT NULL DEFAULT 0,
    detail        TEXT,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (repo_id, branch)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    """SQLite 实现。连接跨线程复用，访问串行化。"""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock, self.conn:
            self.conn.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        columns = [row[1] for row in self.conn.execute("PRAGMA table_info(repos)")]
        if "owner" not in columns:
            self.conn.execute("ALTER TABLE repos ADD COLUMN owner TEXT NOT NULL DEFAULT ''")
        for column in ("remote_username", "remote_secret"):
            if column not in columns:
                self.conn.execute(f"ALTER TABLE repos ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")

        status_cols = [row[1] for row in self.conn.execute("PRAGMA table_info(branch_status)")]
        for column, ddl in (
            ("backup_branch", "TEXT NOT NULL DEFAULT ''"),
            ("backup_side", "TEXT NOT NULL DEFAULT ''"),
            ("backup_missing", "INTEGER NOT NULL DEFAULT 0"),
            ("frozen", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if column not in status_cols:
                self.conn.execute(f"ALTER TABLE branch_status ADD COLUMN {column} {ddl}")

    def close(self) -> None:
        """关闭连接。"""
        with self._lock:
            self.conn.close()

    # ---------- 会话 ----------

    def create_session(self, sid: str, username: str, token: str) -> None:
        """保存会话。"""
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO sessions (id, username, token, created_at) VALUES (?, ?, ?, ?)",
                (sid, username, token, _now()),
            )

    def get_session(self, sid: str) -> Session | None:
        """取会话。"""
        with self._lock:
            row = self.conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
        return _session(row) if row else None

    def delete_session(self, sid: str) -> None:
        """删除会话。"""
        with self._lock, self.conn:
            self.conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))

    # ---------- 配对 ----------

    def add_pair(
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
    ) -> Pair:
        """新建配对。"""
        owner_name, _, repo = local_repo.partition("/")
        if not owner_name or not repo:
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
        return self.get_pair(name)

    def get_pair(self, name: str) -> Pair:
        """按名字取配对。"""
        with self._lock:
            row = self.conn.execute("SELECT * FROM repos WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise KeyError(f"没有名为 {name!r} 的配对")
        return _pair(row)

    def get_pair_by_id(self, pair_id: int) -> Pair | None:
        """按 id 取配对。"""
        with self._lock:
            row = self.conn.execute("SELECT * FROM repos WHERE id = ?", (pair_id,)).fetchone()
        return _pair(row) if row else None

    def list_pairs(self, owner: str | None = None) -> list[Pair]:
        """列出配对。"""
        with self._lock:
            if owner is None:
                rows = self.conn.execute("SELECT * FROM repos ORDER BY name").fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM repos WHERE owner = ? ORDER BY name", (owner,)
                ).fetchall()
        return [_pair(row) for row in rows]

    def update_pair(
        self,
        name: str,
        *,
        remote_url: str | None = None,
        remote_username: str | None = None,
        remote_secret: str | None = None,
        push: bool | None = None,
        pull: bool | None = None,
        retention_days: int | None = None,
    ) -> Pair:
        """更新配对。"""
        pair = self.get_pair(name)
        with self._lock, self.conn:
            for column, value in (
                ("remote_url", remote_url),
                ("remote_username", remote_username),
                ("remote_secret", remote_secret),
                ("push_enabled", None if push is None else int(push)),
                ("pull_enabled", None if pull is None else int(pull)),
                ("backup_retention_days", retention_days),
            ):
                if value is not None:
                    self.conn.execute(f"UPDATE repos SET {column} = ? WHERE id = ?", (value, pair.id))
        return self.get_pair(name)

    def delete_pair(self, name: str) -> None:
        """删除配对及其分支状态。"""
        pair = self.get_pair(name)
        with self._lock, self.conn:
            self.conn.execute("DELETE FROM branch_status WHERE repo_id = ?", (pair.id,))
            self.conn.execute("DELETE FROM repos WHERE id = ?", (pair.id,))

    # ---------- 分支状态 ----------

    def save_branch(
        self,
        pair_id: int,
        branch: str,
        *,
        local_sha: str | None,
        remote_sha: str | None,
        status: str,
        backup_branch: str | None = None,
        backup_side: str | None = None,
        backup_missing: bool | None = None,
        detail: str = "",
    ) -> None:
        """写入分支状态。"""
        with self._lock, self.conn:
            self.conn.execute(
                """INSERT INTO branch_status
                   (repo_id, branch, local_sha, remote_sha, status,
                    backup_branch, backup_side, detail, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(repo_id, branch) DO UPDATE SET
                     local_sha = excluded.local_sha,
                     remote_sha = excluded.remote_sha,
                     status = excluded.status,
                     backup_branch = excluded.backup_branch,
                     backup_side = excluded.backup_side,
                     backup_missing = COALESCE(?, branch_status.backup_missing),
                     detail = excluded.detail,
                     updated_at = excluded.updated_at""",
                (
                    pair_id, branch, local_sha, remote_sha, status,
                    backup_branch or "", backup_side or "", detail, _now(),
                    None if backup_missing is None else int(backup_missing),
                ),
            )

    def get_branch(self, pair_id: int, branch: str) -> BranchState | None:
        """取单条分支状态。"""
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM branch_status WHERE repo_id = ? AND branch = ?",
                (pair_id, branch),
            ).fetchone()
        return _branch(row) if row else None

    def list_branches(self, pair_id: int) -> list[BranchState]:
        """列出分支状态。"""
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM branch_status WHERE repo_id = ? ORDER BY branch", (pair_id,)
            ).fetchall()
        return [_branch(row) for row in rows]

    def mark_backup_missing(self, pair_id: int, branch: str, detail: str = "") -> None:
        """备份已被清理时标记。"""
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE branch_status SET backup_missing = 1, status = 'synced', detail = ?"
                " WHERE repo_id = ? AND branch = ?",
                (detail, pair_id, branch),
            )

    def prune_branches(self, pair_id: int, keep: set[str]) -> None:
        """删除已不存在的分支记录。"""
        with self._lock, self.conn:
            rows = self.conn.execute(
                "SELECT branch FROM branch_status WHERE repo_id = ?", (pair_id,)
            ).fetchall()
            for row in rows:
                if row["branch"] not in keep:
                    self.conn.execute(
                        "DELETE FROM branch_status WHERE repo_id = ? AND branch = ?",
                        (pair_id, row["branch"]),
                    )


def _session(row: sqlite3.Row) -> Session:
    return Session(
        id=row["id"],
        username=row["username"],
        token=row["token"],
        created_at=row["created_at"],
    )


def _pair(row: sqlite3.Row) -> Pair:
    return Pair(
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


def _branch(row: sqlite3.Row) -> BranchState:
    return BranchState(
        pair_id=row["repo_id"],
        branch=row["branch"],
        local_sha=row["local_sha"],
        remote_sha=row["remote_sha"],
        status=row["status"],
        backup_branch=row["backup_branch"],
        backup_side=row["backup_side"],
        backup_missing=bool(row["backup_missing"]),
        detail=row["detail"] or "",
        updated_at=row["updated_at"],
    )
