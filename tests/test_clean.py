import os
import tempfile
import unittest
from unittest import mock

from baton import clean, run
from tests.fakes import FakeRunner

ROOT = "/repo"
WT = f"{ROOT}/.claude/worktrees/w1"


def listing(state, sha="bbb"):
    return (f"worktree {ROOT}\nHEAD aaa\nbranch refs/heads/main\n\n"
            f"worktree {WT}\nHEAD {sha}\n{state}\n\n"
            "worktree /elsewhere/other\nHEAD ccc\nbranch refs/heads/other\n\n")


class NotContained(FakeRunner):
    """Like git: `merge-base --is-ancestor` exits 1 when the commit is not contained."""

    def __call__(self, argv, cwd=None, input=None):
        if argv[:3] == ["git", "merge-base", "--is-ancestor"]:
            self.calls.append(argv)
            raise run.RunError("exit 1")
        return super().__call__(argv, cwd, input)


class WorktreeTest(unittest.TestCase):
    def go(self, state, extra=None, cls=FakeRunner):
        replies = {"git worktree list --porcelain": listing(state)}
        replies.update(extra or {})
        runner = cls(replies)
        out = []
        result = clean.clean_worktrees(runner, "main", out.append)
        return runner, result, out

    def test_agent_worktrees_only_under_claude_dir(self):
        runner = FakeRunner({"git worktree list": listing("branch refs/heads/x")})
        found = clean.agent_worktrees(runner)
        self.assertEqual([w["path"] for w in found], [WT])
        self.assertEqual(found[0]["branch"], "x")
        self.assertFalse(found[0]["detached"])

    def test_worktree_removed_when_not_on_open_issue(self):
        runner, (removed, kept, failed), _ = self.go("branch refs/heads/worktree-agent-1")
        self.assertEqual((removed, kept, failed), ([WT], [], []))
        self.assertIn(f"git worktree remove --force {WT}", runner.lines())

    def test_worktree_kept_for_open_issue_branch(self):
        runner, (removed, kept, _), out = self.go(
            "branch refs/heads/issue-7-x", {"gh issue view 7": '{"state": "OPEN"}'})
        self.assertEqual((removed, kept), ([], [WT]))
        self.assertFalse([c for c in runner.lines() if "worktree remove" in c])
        self.assertIn("issue #7 is open", out[0])

    def test_worktree_removed_for_closed_issue_branch(self):
        _, (removed, _, _), _ = self.go(
            "branch refs/heads/issue-7-x", {"gh issue view 7": '{"state": "CLOSED"}'})
        self.assertEqual(removed, [WT])

    def test_worktree_kept_with_uncommitted_changes(self):
        _, (removed, kept, _), out = self.go(
            "branch refs/heads/worktree-agent-1", {f"git -C {WT} status": " M file\n"})
        self.assertEqual((removed, kept), ([], [WT]))
        self.assertIn("uncommitted", out[0])

    def test_worktree_detached_contained_in_default_is_removed(self):
        runner, (removed, _, _), _ = self.go("detached")
        self.assertEqual(removed, [WT])
        self.assertIn("git merge-base --is-ancestor bbb origin/main", runner.lines())

    def test_worktree_detached_not_contained_is_kept(self):
        _, (removed, kept, _), out = self.go("detached", cls=NotContained)
        self.assertEqual((removed, kept), ([], [WT]))
        self.assertIn("not in main", out[0])

    def test_worktree_locked_removed_with_double_force(self):
        runner, _, _ = self.go("branch refs/heads/x\nlocked")
        self.assertIn(f"git worktree remove --force --force {WT}", runner.lines())


class FailingRemove(FakeRunner):
    def __call__(self, argv, cwd=None, input=None):
        if argv[:3] == ["git", "worktree", "remove"]:
            self.calls.append(argv)
            raise run.RunError("worktree remove failed")
        return super().__call__(argv, cwd, input)


class OwnerTest(unittest.TestCase):
    def wt(self, d):
        return {"path": d, "sha": "b", "branch": "x", "detached": False, "locked": False}

    def test_owner_message_and_failed_list(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "data.bin")
            open(f, "w").close()
            out = []
            with mock.patch.object(clean, "agent_worktrees", return_value=[self.wt(d)]), \
                    mock.patch("os.getuid", return_value=os.getuid() + 1), \
                    mock.patch("pwd.getpwuid") as pw:
                pw.return_value.pw_name = "root"
                result = clean.clean_worktrees(FailingRemove(), "main", out.append)
            self.assertEqual(result, ([], [], [d]))
            self.assertEqual(out, [f"cannot remove {d}: {f} is owned by root"])

    def test_foreign_owner_none_when_all_mine(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "f"), "w").close()
            self.assertIsNone(clean.foreign_owner(d))

    def test_foreign_owner_finds_file(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "f")
            open(f, "w").close()
            with mock.patch("os.getuid", return_value=os.getuid() + 1):
                found = clean.foreign_owner(d)
            self.assertEqual(found[0], f)

    def test_owner_fallback_names_directory(self):
        with tempfile.TemporaryDirectory() as d:
            out = []
            with mock.patch.object(clean, "agent_worktrees", return_value=[self.wt(d)]):
                clean.clean_worktrees(FailingRemove(), "main", out.append)
            self.assertIn(f"cannot remove {d}", out[0])


if __name__ == "__main__":
    unittest.main()
