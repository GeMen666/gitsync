import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gitsync.config import Config
from gitsync.db import Store
from gitsync.sync import sync_repo


def git(*args, cwd=None, env=None):
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd) if cwd else None,
        capture_output=True, text=True, env=env,
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} 失败:\n{proc.stderr}")
    return proc


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gitsync-test-"))
        self.local_root = self.root / "gitea"
        self.remote_root = self.root / "remote"
        self.local_repo = self.local_root / "owner" / "demo.git"
        self.remote_repo = self.remote_root / "owner" / "demo.git"
        for path in (self.local_repo, self.remote_repo):
            path.parent.mkdir(parents=True, exist_ok=True)
            git("init", "--bare", "--quiet", str(path))

        self.cfg = Config(
            path=self.root / "config.toml",
            local_url=f"file://{self.local_root}",
            local_username="u",
            local_token="t",
            remote_username="u",
            remote_token="t",
            db_path=self.root / "db.sqlite",
            temp_dir=self.root / "temp",
            log_dir=self.root / "logs",
        )
        self.store = Store(self.cfg.db_path)
        self.repo = self.store.add_repo("demo", "owner/demo", f"file://{self.remote_repo}")

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.root, ignore_errors=True)

    # ---------- 工具 ----------

    def commit(self, bare: Path, branch: str, text: str, when: str, new: bool = False):
        work = Path(tempfile.mkdtemp(dir=self.root))
        git("clone", "--quiet", str(bare), str(work))
        if new:
            git("checkout", "--quiet", "-b", branch, cwd=work)
        else:
            git("checkout", "--quiet", "-B", branch, f"origin/{branch}", cwd=work)
        (work / f"{text}.txt").write_text(text)
        git("add", "-A", cwd=work)
        env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet",
            "-m", text, cwd=work, env=env)
        git("push", "--quiet", "origin", f"{branch}:{branch}", cwd=work)

    def push_as(self, bare: Path, src: str, dest: str) -> None:
        work = Path(tempfile.mkdtemp(dir=self.root))
        git("clone", "--quiet", str(bare), str(work))
        git("push", "--quiet", "origin", f"refs/remotes/origin/{src}:{dest}", cwd=work)

    def heads(self, bare: Path) -> dict[str, str]:
        out = git("ls-remote", "--heads", str(bare)).stdout
        result = {}
        for line in out.splitlines():
            sha, _, ref = line.partition("\t")
            result[ref[len("refs/heads/"):]] = sha
        return result

    def run_sync(self):
        return {o.branch: o for o in sync_repo(self.repo, self.cfg, self.store)}

    # ---------- 用例 ----------

    def test_creates_branch_on_remote(self):
        self.commit(self.local_repo, "main", "a", "2026-01-01T10:00:00+0000", new=True)
        result = self.run_sync()
        self.assertEqual(result["main"].status, "pushed")
        self.assertEqual(self.heads(self.remote_repo), self.heads(self.local_repo))

    def test_creates_branch_on_local(self):
        self.commit(self.remote_repo, "feature", "b", "2026-01-01T10:00:00+0000", new=True)
        result = self.run_sync()
        self.assertEqual(result["feature"].status, "pulled")
        self.assertEqual(self.heads(self.local_repo), self.heads(self.remote_repo))

    def test_identical_branches_are_synced(self):
        self.commit(self.local_repo, "main", "a", "2026-01-01T10:00:00+0000", new=True)
        self.run_sync()
        result = self.run_sync()
        self.assertEqual(result["main"].status, "synced")

    def test_local_newer_pushes_and_archives_remote(self):
        self.commit(self.local_repo, "main", "a", "2026-01-01T10:00:00+0000", new=True)
        self.commit(self.remote_repo, "main", "b", "2026-01-02T10:00:00+0000", new=True)
        self.commit(self.local_repo, "main", "c", "2026-01-03T10:00:00+0000")

        remote_before = self.heads(self.remote_repo)["main"]
        result = self.run_sync()

        self.assertEqual(result["main"].status, "pending")
        heads = self.heads(self.remote_repo)
        self.assertEqual(heads["main"], self.heads(self.local_repo)["main"])
        backups = [b for b in heads if b.startswith("main.bak.")]
        self.assertEqual(len(backups), 1)
        self.assertEqual(heads[backups[0]], remote_before)

    def test_remote_newer_pulls_and_archives_local(self):
        self.commit(self.local_repo, "main", "a", "2026-01-01T10:00:00+0000", new=True)
        self.commit(self.remote_repo, "main", "b", "2026-01-02T10:00:00+0000", new=True)
        self.commit(self.remote_repo, "main", "c", "2026-01-03T10:00:00+0000")

        local_before = self.heads(self.local_repo)["main"]
        result = self.run_sync()

        self.assertEqual(result["main"].status, "pending")
        heads = self.heads(self.local_repo)
        self.assertEqual(heads["main"], self.heads(self.remote_repo)["main"])
        backups = [b for b in heads if b.startswith("main.bak.")]
        self.assertEqual(len(backups), 1)
        self.assertEqual(heads[backups[0]], local_before)

    def test_push_disabled_skips_missing_remote_branch(self):
        self.store.remove_repo("demo")
        self.repo = self.store.add_repo(
            "demo", "owner/demo", f"file://{self.remote_repo}", push=False
        )
        self.commit(self.local_repo, "main", "a", "2026-01-01T10:00:00+0000", new=True)
        result = self.run_sync()
        self.assertEqual(result["main"].status, "skipped")
        self.assertEqual(self.heads(self.remote_repo), {})

    def test_expired_backup_is_cleaned(self):
        self.commit(self.local_repo, "main", "a", "2026-01-01T10:00:00+0000", new=True)
        self.push_as(self.local_repo, "main", "refs/heads/main.bak.202001010000")
        self.assertIn("main.bak.202001010000", self.heads(self.local_repo))

        result = self.run_sync()
        self.assertIn("main.bak.202001010000", result)
        self.assertEqual(result["main.bak.202001010000"].status, "cleaned")
        self.assertNotIn("main.bak.202001010000", self.heads(self.local_repo))

    def test_backup_branches_are_not_synced(self):
        self.commit(self.local_repo, "main", "a", "2026-01-01T10:00:00+0000", new=True)
        self.push_as(self.local_repo, "main", "refs/heads/main.bak.202601010000")
        self.run_sync()
        self.assertNotIn("main.bak.202601010000", self.heads(self.remote_repo))


if __name__ == "__main__":
    unittest.main()
