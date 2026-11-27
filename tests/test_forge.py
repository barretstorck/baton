import json
import unittest
from unittest import mock

from baton import cli, forge, identity, run
from baton.forge.github import GitHub
from tests.fakes import FakeRunner


class ForTest(unittest.TestCase):
    def test_for_repo_holds_runner(self):
        runner = FakeRunner({})
        backend = forge.for_repo(runner=runner)
        self.assertIsInstance(backend, GitHub)
        self.assertIs(backend.runner, runner)
        self.assertEqual(backend.kind, "github")
        self.assertEqual(runner.lines(), [])

    def test_for_repo_uses_patched_run(self):
        def fake(argv, **kw):
            return "x"
        with mock.patch.object(cli.run, "run", fake):
            self.assertIs(forge.for_repo().runner, fake)

    def test_read(self):
        backend = GitHub(FakeRunner({"gh a": json.dumps({"k": 1}), "gh b": ""}))
        self.assertEqual(backend._read(["gh", "a"]), {"k": 1})
        self.assertEqual(backend._read(["gh", "b"], empty=[]), [])
        with self.assertRaises(ValueError):
            backend._read(["gh", "b"])

    def test_write_without_role(self):
        runner = FakeRunner({"gh w": "ok"})
        self.assertEqual(GitHub(runner)._write(["gh", "w"]), "ok")
        self.assertEqual(runner.lines(), ["gh w"])

    def test_write_with_role(self):
        runner = FakeRunner({})
        calls = []
        with mock.patch.object(identity, "write",
                               lambda r, argv, role: calls.append((r, argv, role)) or "done"):
            self.assertEqual(GitHub(runner)._write(["gh", "w"], role="developer"), "done")
        self.assertEqual(calls, [(runner, ["gh", "w"], "developer")])
        self.assertEqual(runner.lines(), [])


class ReadTest(unittest.TestCase):
    def test_repo_view(self):
        runner = FakeRunner({"gh repo view": '{"owner": {"login": "o"}}'})
        self.assertEqual(GitHub(runner).repo_view("owner"), {"owner": {"login": "o"}})
        self.assertEqual(runner.lines(), ["gh repo view --json owner"])

    def test_issue_view(self):
        runner = FakeRunner({"gh issue view 7 --json comments": "",
                             "gh issue view 7 --json body": '{"body": "b"}'})
        backend = GitHub(runner)
        self.assertEqual(backend.issue_view(7, "body"), {"body": "b"})
        self.assertEqual(backend.issue_view(7, "comments", empty={}), {})
        self.assertEqual(runner.lines(), ["gh issue view 7 --json body",
                                          "gh issue view 7 --json comments"])

    def test_pr_view(self):
        runner = FakeRunner({"gh pr view 3": '{"state": "OPEN"}'})
        self.assertEqual(GitHub(runner).pr_view(3, "state"), {"state": "OPEN"})
        self.assertEqual(GitHub(FakeRunner()).pr_view(3, "state", empty={}), {})
        self.assertEqual(runner.lines(), ["gh pr view 3 --json state"])

    def test_issue_exists(self):
        runner = FakeRunner({"gh issue view 5": '{"number": 5}'})
        self.assertTrue(GitHub(runner).issue_exists(5))
        self.assertEqual(runner.lines(), ["gh issue view 5 --json number"])
        self.assertFalse(GitHub(FakeRunner()).issue_exists(6))

        def boom(argv, **kw):
            raise run.RunError("nope")
        self.assertFalse(GitHub(boom).issue_exists(6))


class WriteTest(unittest.TestCase):
    def _check(self, call, line, out=""):
        """Run `call(backend, role)` plain and with a role; check argv and identity.write."""
        runner = FakeRunner({"gh": out})
        result = call(GitHub(runner), None)
        self.assertEqual(runner.lines(), [line])
        seen = []

        def fake_write(r, argv, role):
            seen.append((r, argv, role))
            return "w"
        backend = GitHub(runner)
        with mock.patch.object(identity, "write", fake_write):
            self.assertEqual(call(backend, "developer"), "w")
        self.assertEqual(len(seen), 1)
        self.assertIs(seen[0][0], runner)
        self.assertEqual(" ".join(seen[0][1]), line)
        self.assertEqual(seen[0][2], "developer")
        self.assertEqual(runner.lines(), [line])
        return result

    def test_issue_edit_body(self):
        self._check(lambda b, r: b.issue_edit_body(4, "/p.md", role=r),
                    "gh issue edit 4 --body-file /p.md")

    def test_issue_edit_labels(self):
        self._check(lambda b, r: b.issue_edit_labels(4, ["a", "b"], ["c", "d"], role=r),
                    "gh issue edit 4 --add-label a --add-label b --remove-label c --remove-label d")
        self._check(lambda b, r: b.issue_edit_labels(4, ["a"], role=r),
                    "gh issue edit 4 --add-label a")

    def test_comment(self):
        out = self._check(lambda b, r: b.comment("pr", 9, "/p.md", role=r),
                          "gh pr comment 9 --body-file /p.md", out="url\n")
        self.assertEqual(out, "url\n")

    def test_change_state(self):
        self._check(lambda b, r: b.change_state("pr", "reopen", 9, role=r), "gh pr reopen 9")

    def test_issue_close(self):
        self._check(lambda b, r: b.issue_close(4, "completed", role=r),
                    "gh issue close 4 --reason completed")
        self._check(lambda b, r: b.issue_close(4, "duplicate", 2, role=r),
                    "gh issue close 4 --reason duplicate --duplicate-of 2")

    def test_issue_lock(self):
        self._check(lambda b, r: b.issue_lock(4, role=r), "gh issue lock 4")
        self._check(lambda b, r: b.issue_lock(4, "spam", role=r), "gh issue lock 4 --reason spam")

    def test_label_names(self):
        runner = FakeRunner({"gh label list": '[{"name": "x"}, {"name": "y"}]'})
        self.assertEqual(GitHub(runner).label_names(), {"x", "y"})
        self.assertEqual(runner.lines(), ["gh label list --limit 500 --json name"])
        self.assertEqual(GitHub(FakeRunner()).label_names(), set())

    def test_label_create(self):
        self._check(lambda b, r: b.label_create("n", "ffffff", "d e", role=r),
                    "gh label create n --color ffffff --description d e")


if __name__ == "__main__":
    unittest.main()
