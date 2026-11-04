import unittest

from baton import branches, run
from tests.fakes import FakeRunner


class Raising:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, cwd=None, input=None, env=None):
        self.calls.append(argv)
        raise run.RunError("boom")


class BranchesTest(unittest.TestCase):
    def test_detached(self):
        self.assertTrue(branches.detached(FakeRunner({"git rev-parse": "HEAD\n"}), "."))
        self.assertFalse(branches.detached(FakeRunner({"git rev-parse": "main\n"}), "."))
        self.assertFalse(branches.detached(Raising(), "."))

    def pushed(self, out):
        return branches.pushed_branches_at_head(FakeRunner({"git for-each-ref": out}), ".")

    def test_one_branch(self):
        self.assertEqual(self.pushed("refs/remotes/origin/issue-1-x\n"), ["issue-1-x"])

    def test_filters_head_and_default(self):
        out = "refs/remotes/origin/HEAD\nrefs/remotes/origin/main\nrefs/remotes/origin/issue-1-x\n"
        self.assertEqual(self.pushed(out), ["issue-1-x"])

    def test_unsafe_names_dropped(self):
        out = "refs/remotes/origin/issue-1-a;b\nrefs/remotes/origin/issue-1-a b\n"
        self.assertEqual(self.pushed(out), [])

    def test_two_branches(self):
        out = "refs/remotes/origin/issue-2-b\nrefs/remotes/origin/issue-1-a\nrefs/remotes/origin/issue-1-a\n"
        self.assertEqual(self.pushed(out), ["issue-1-a", "issue-2-b"])

    def test_error(self):
        self.assertEqual(branches.pushed_branches_at_head(Raising(), "."), [])

    def test_record_push(self):
        r = FakeRunner()
        branches.record_push(r, ".", "HEAD:refs/heads/issue-1-x", "abc")
        self.assertEqual(r.calls, [["git", "update-ref", "refs/remotes/origin/issue-1-x", "abc"]])

    def test_record_push_nothing(self):
        r = FakeRunner()
        branches.record_push(r, ".", "HEAD:refs/heads/issue-1-x", "")
        branches.record_push(r, ".", "HEAD", "abc")
        self.assertEqual(r.calls, [])
        branches.record_push(Raising(), ".", "HEAD:refs/heads/b", "abc")


if __name__ == "__main__":
    unittest.main()
