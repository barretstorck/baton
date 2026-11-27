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


if __name__ == "__main__":
    unittest.main()
