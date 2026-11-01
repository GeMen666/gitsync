import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gitsync.ports import Endpoint, LeaseMismatch, NonFastForward
from gitsync.adapters.git_cli import CliGitTransport


def git(*args, cwd=None):
    proc = subprocess.run(["git", *args], cwd=str(cwd) if cwd else None,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} 失败:\n{proc.stderr}")
    return proc


class TransportTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="transport-"))
        self.a = self.root / "a" / "demo.git"
        self.b = self.root / "b" / "demo.git"
        for path in (self.a, self.b):
            path.parent.mkdir(parents=True, exist_ok=True)
            git("init", "--bare", "--quiet", str(path))
        self.transport = CliGitTransport(self.root / "temp")
        self.ep_a = Endpoint("a", f"file://{self.a}")
        self.ep_b = Endpoint("b", f"file://{self.b}")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def commit(self, bare, branch, text):
        work = Path(tempfile.mkdtemp(dir=self.root))
        git("clone", "--quiet", str(bare), str(work))
        exists = subprocess.run(["git", "rev-parse", "--verify", f"origin/{branch}"],
                                cwd=str(work), capture_output=True).returncode == 0
        if exists:
            git("checkout", "--quiet", "-B", branch, f"origin/{branch}", cwd=work)
        else:
            git("checkout", "--quiet", "-B", branch, cwd=work)
        (work / f"{text}.txt").write_text(text)
        git("add", "-A", cwd=work)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", text, cwd=work)
        git("push", "--quiet", "origin", f"{branch}:{branch}", cwd=work)

    def test_list_refs(self):
        self.commit(self.a, "main", "one")
        self.assertEqual(set(self.transport.list_refs(self.ep_a)), {"main"})

    def test_fetch_returns_sha(self):
        self.commit(self.a, "main", "one")
        expected = self.transport.list_refs(self.ep_a)["main"]
        with self.transport.workspace() as ws:
            self.assertEqual(ws.fetch(self.ep_a, "main"), expected)

    def test_push_creates_branch(self):
        self.commit(self.a, "main", "one")
        with self.transport.workspace() as ws:
            sha = ws.fetch(self.ep_a, "main")
            ws.push(self.ep_b, sha, "main")
        self.assertEqual(self.transport.list_refs(self.ep_b), self.transport.list_refs(self.ep_a))

    def test_push_rejects_non_fast_forward(self):
        self.commit(self.a, "main", "one")
        with self.transport.workspace() as ws:
            ws.push(self.ep_b, ws.fetch(self.ep_a, "main"), "main")
        self.commit(self.a, "main", "two")
        self.commit(self.b, "main", "other")
        with self.assertRaises(NonFastForward):
            with self.transport.workspace() as ws:
                ws.push(self.ep_b, ws.fetch(self.ep_a, "main"), "main")

    def test_overwrite_with_lease(self):
        self.commit(self.a, "main", "one")
        with self.transport.workspace() as ws:
            ws.push(self.ep_b, ws.fetch(self.ep_a, "main"), "main")
        self.commit(self.a, "main", "two")
        self.commit(self.b, "main", "other")
        expected = self.transport.list_refs(self.ep_b)["main"]
        with self.transport.workspace() as ws:
            sha = ws.fetch(self.ep_a, "main")
            ws.overwrite(self.ep_b, sha, "main", expected=expected)
        self.assertEqual(self.transport.list_refs(self.ep_b), self.transport.list_refs(self.ep_a))

    def test_overwrite_rejects_stale_lease(self):
        self.commit(self.a, "main", "one")
        with self.transport.workspace() as ws:
            ws.push(self.ep_b, ws.fetch(self.ep_a, "main"), "main")
        self.commit(self.a, "main", "two")
        with self.assertRaises(LeaseMismatch):
            with self.transport.workspace() as ws:
                sha = ws.fetch(self.ep_a, "main")
                ws.overwrite(self.ep_b, sha, "main", expected="0" * 40)

    def test_commit_time(self):
        self.commit(self.a, "main", "one")
        with self.transport.workspace() as ws:
            sha = ws.fetch(self.ep_a, "main")
            self.assertGreater(ws.commit_time(sha), 0)

    def test_delete(self):
        self.commit(self.a, "main", "one")
        self.commit(self.a, "gone", "x")
        with self.transport.workspace() as ws:
            ws.delete(self.ep_a, "gone")
        self.assertNotIn("gone", self.transport.list_refs(self.ep_a))


if __name__ == "__main__":
    unittest.main()
