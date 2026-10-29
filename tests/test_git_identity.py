"""`baton identity worktree` and `baton push` at both tiers."""
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from baton import cli, identity
from tests.fakes import FakeRunner


def call(fn, args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = fn(args)
    return code, out.getvalue(), err.getvalue()


def tier1(test):
    ids = identity.Identities(identity.App(1, 10, "/k/b.pem", "baton[bot]"), None)
    identity.use(lambda: ids)
    test.addCleanup(identity.use, None)
    p = mock.patch.object(identity, "token", lambda *a: "tok-baton")
    p.start()
    test.addCleanup(p.stop)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Tier0Test(unittest.TestCase):
    def setUp(self):
        identity.use(lambda: None)
        self.addCleanup(identity.use, None)

    def test_worktree_runs_no_git(self):
        runner = FakeRunner()
        with mock.patch.object(cli.run, "run", runner):
            code, out, _ = call(cli.cmd_identity, ["worktree"])
        self.assertEqual(code, 0)
        self.assertIn("tier 0: commits keep your git identity", out)
        self.assertEqual(runner.calls, [])

    def test_push_is_plain(self):
        runner = FakeRunner({"gh repo view": '{"nameWithOwner": "o/r"}'})
        with mock.patch.object(cli.run, "run", runner):
            code, _, _ = call(cli.cmd_push, ["HEAD:refs/heads/b"])
        self.assertEqual(code, 0)
        self.assertEqual(runner.calls[-1], ["git", "push", "origin", "HEAD:refs/heads/b"])
        self.assertIsNone(runner.envs[-1])

    def test_usage(self):
        self.assertEqual(call(cli.cmd_push, [])[0], 64)
        self.assertEqual(call(cli.cmd_identity, ["x"])[0], 64)


class Tier1Test(unittest.TestCase):
    def setUp(self):
        tier1(self)
        p = mock.patch.object(identity, "bot_user_id", lambda runner, login: 42)
        p.start()
        self.addCleanup(p.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.main = os.path.join(tmp.name, "main")
        self.wt = os.path.join(tmp.name, "wt")
        os.mkdir(self.main)
        git(self.main, "init", "-q")
        git(self.main, "config", "user.name", "Maint")
        git(self.main, "config", "user.email", "maint@example.com")
        git(self.main, "commit", "-q", "--allow-empty", "-m", "x")
        git(self.main, "worktree", "add", "-q", "--detach", self.wt)

    def run_in(self, cwd):
        old = os.getcwd()
        os.chdir(cwd)
        try:
            return call(cli.cmd_identity, ["worktree"])
        finally:
            os.chdir(old)

    def test_sets_worktree_identity_only(self):
        code, out, _ = self.run_in(self.wt)
        self.assertEqual(code, 0)
        self.assertIn("baton[bot] 42+baton[bot]@users.noreply.github.com", out)
        self.assertEqual(git(self.wt, "config", "user.email"), "42+baton[bot]@users.noreply.github.com")
        self.assertEqual(git(self.wt, "config", "user.name"), "baton[bot]")
        self.assertEqual(git(self.main, "config", "user.email"), "maint@example.com")
        self.assertEqual(git(self.main, "config", "user.name"), "Maint")

    def test_refuses_in_main_checkout(self):
        code, _, err = self.run_in(self.main)
        self.assertEqual(code, 1)
        self.assertIn("linked worktree", err)
        self.assertEqual(git(self.main, "config", "user.email"), "maint@example.com")

    def test_push_uses_token_env(self):
        runner = FakeRunner({"gh repo view": '{"nameWithOwner": "o/r"}'})
        with mock.patch.object(cli.run, "run", runner):
            call(cli.cmd_push, ["HEAD:refs/heads/b"])
        self.assertIn("https://github.com/o/r.git", runner.calls[-1])
        self.assertEqual(runner.envs[-1]["BATON_GIT_TOKEN"], "tok-baton")
        self.assertFalse(any("tok-baton" in a for a in runner.calls[-1]))


if __name__ == "__main__":
    unittest.main()
