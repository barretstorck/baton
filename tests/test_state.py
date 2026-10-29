import copy
import pathlib
import unittest
import io
from unittest import mock
from baton import cli, comment, config, state
from tests.fakes import FakeRunner


def cfg():
    return config.Config(root=pathlib.Path("."), data=copy.deepcopy(config.DEFAULTS))


class ChangeTest(unittest.TestCase):
    def test_close_issue_comments_then_closes(self):
        runner = FakeRunner({"gh issue comment": "https://example.com/c/1\n"})
        url = state.change(cfg(), runner, "close", "issue", 5, "orchestrator", "Merged.", [])
        self.assertEqual(url, "https://example.com/c/1")
        lines = runner.lines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("gh issue comment 5 --body-file"))
        self.assertEqual(lines[1], "gh issue close 5")

    def test_reopen_pr(self):
        runner = FakeRunner()
        state.change(cfg(), runner, "reopen", "pr", 7, "reviewer", "Closed by mistake.", [])
        lines = runner.lines()
        self.assertTrue(lines[0].startswith("gh pr comment 7"))
        self.assertEqual(lines[1], "gh pr reopen 7")

    def test_marker_kind_matches_action(self):
        seen = []

        def runner(argv, cwd=None, input=None):
            if "--body-file" in argv:
                with open(argv[-1]) as handle:
                    seen.append(handle.read())
            return ""

        state.change(cfg(), runner, "close", "issue", 1, "developer", "Done.", [])
        state.change(cfg(), runner, "reopen", "issue", 1, "developer", "Again.", [])
        self.assertTrue(seen[0].startswith("<!-- baton:developer:closed -->"))
        self.assertTrue(seen[1].startswith("<!-- baton:developer:reopened -->"))

    def test_empty_reason_refused(self):
        runner = FakeRunner()
        for reason in ("", "   ", None):
            with self.assertRaises(comment.CommentError):
                state.change(cfg(), runner, "close", "issue", 5, "developer", reason, [])
        self.assertEqual(runner.calls, [])

    def test_unknown_action_or_target_refused(self):
        runner = FakeRunner()
        with self.assertRaises(comment.CommentError):
            state.change(cfg(), runner, "merge", "issue", 5, "developer", "x", [])
        with self.assertRaises(comment.CommentError):
            state.change(cfg(), runner, "close", "repo", 5, "developer", "x", [])
        self.assertEqual(runner.calls, [])

    def test_private_term_refused_and_nothing_closed(self):
        runner = FakeRunner()
        with self.assertRaises(comment.CommentError):
            state.change(cfg(), runner, "close", "issue", 5, "developer", "see secretword", ["secretword"])
        self.assertEqual(runner.calls, [])


class ReasonTest(unittest.TestCase):
    def go(self, runner, reason, **kw):
        return state.change(cfg(), runner, "close", "issue", 5, "developer", "Why.", [],
                            close_reason=reason, **kw)

    def test_labelled_reasons(self):
        for reason in ("spam", "support", "wont-do"):
            runner = FakeRunner()
            self.go(runner, reason)
            lines = runner.lines()
            self.assertEqual(len(lines), 3)
            self.assertEqual(lines[1], f"gh issue edit 5 --add-label closed:{reason}")
            self.assertEqual(lines[2], "gh issue close 5 --reason not planned")

    def test_completed_has_no_label(self):
        runner = FakeRunner()
        self.go(runner, "completed")
        self.assertEqual(runner.lines()[1:], ["gh issue close 5 --reason completed"])

    def test_duplicate_with_backlink(self):
        runner = FakeRunner({"gh issue view 3": '{"number": 3}\n'})
        self.go(runner, "duplicate", duplicate_of=3)
        lines = runner.lines()
        self.assertEqual(lines[0], "gh issue view 3 --json number")
        self.assertTrue(lines[1].startswith("gh issue comment 5"))
        self.assertEqual(lines[2], "gh issue edit 5 --add-label closed:duplicate")
        self.assertEqual(lines[3], "gh issue close 5 --reason duplicate --duplicate-of 3")
        self.assertTrue(lines[4].startswith("gh issue comment 3"))

    def test_duplicate_refusals(self):
        for kw in ({}, {"duplicate_of": 5}):
            runner = FakeRunner({"gh issue view": '{"number": 5}'})
            with self.assertRaises(comment.CommentError):
                self.go(runner, "duplicate", **kw)
            self.assertFalse([c for c in runner.lines() if "comment" in c or "close" in c])
        with self.assertRaises(comment.CommentError):
            self.go(FakeRunner(), "spam", duplicate_of=3)

    def test_missing_original(self):
        runner = FakeRunner()
        with self.assertRaises(comment.CommentError):
            self.go(runner, "duplicate", duplicate_of=3)
        self.assertEqual(runner.lines(), ["gh issue view 3 --json number"])

        def failing(argv, cwd=None, input=None):
            raise state.run.RunError("not found")

        with self.assertRaises(comment.CommentError):
            self.go(failing, "duplicate", duplicate_of=3)

    def test_pr_and_reopen_refused(self):
        runner = FakeRunner()
        with self.assertRaises(comment.CommentError):
            state.change(cfg(), runner, "close", "pr", 5, "developer", "x", [], close_reason="spam")
        with self.assertRaises(comment.CommentError):
            state.change(cfg(), runner, "close", "pr", 5, "developer", "x", [], lock=True)
        with self.assertRaises(comment.CommentError):
            state.change(cfg(), runner, "reopen", "issue", 5, "developer", "x", [], close_reason="spam")
        with self.assertRaises(comment.CommentError):
            self.go(runner, "bogus")
        self.assertEqual(runner.calls, [])

    def test_lock(self):
        runner = FakeRunner()
        self.go(runner, "spam", lock=True)
        self.assertEqual(runner.lines()[-1], "gh issue lock 5 --reason spam")
        runner = FakeRunner()
        self.go(runner, "support", lock=True)
        self.assertEqual(runner.lines()[-1], "gh issue lock 5")
        runner = FakeRunner()
        state.change(cfg(), runner, "close", "issue", 5, "developer", "x", [], lock=True)
        self.assertEqual(runner.lines()[1:], ["gh issue close 5", "gh issue lock 5"])


class CommandTest(unittest.TestCase):
    def test_registered(self):
        self.assertIs(cli.COMMANDS["close"], cli.cmd_close)
        self.assertIs(cli.COMMANDS["reopen"], cli.cmd_reopen)

    def test_wrong_arguments_exit_64(self):
        with mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(cli.cmd_close(["issue", "5", "developer"]), 64)
            self.assertEqual(cli.cmd_reopen([]), 64)

    def test_close_runs_comment_then_close(self):
        runner = FakeRunner({"gh issue comment": "https://example.com/c/9\n"})
        out = io.StringIO()
        with mock.patch.object(cli, "_require_config", return_value=cfg()), \
                mock.patch.object(cli.scan, "load_terms", return_value=[]), \
                mock.patch.object(cli.run, "run", runner), \
                mock.patch("sys.stdin", io.StringIO("Done.")), \
                mock.patch("sys.stdout", out):
            code = cli.main(["close", "issue", "5", "developer", "-"])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().strip(), "https://example.com/c/9")
        self.assertEqual(runner.lines()[-1], "gh issue close 5")

    def run_cli(self, argv, runner):
        err = io.StringIO()
        with mock.patch.object(cli, "_require_config", return_value=cfg()), \
                mock.patch.object(cli.scan, "load_terms", return_value=[]), \
                mock.patch.object(cli.run, "run", runner), \
                mock.patch("sys.stdin", io.StringIO("Dup.")), \
                mock.patch("sys.stdout", io.StringIO()), \
                mock.patch("sys.stderr", err):
            return cli.main(argv)

    def test_close_duplicate_flags(self):
        for argv in (["close", "issue", "5", "developer", "-", "--reason", "duplicate",
                      "--duplicate-of", "3"],
                     ["close", "--reason=duplicate", "issue", "5", "--duplicate-of=3",
                      "developer", "-"]):
            runner = FakeRunner({"gh issue view 3": '{"number": 3}'})
            self.assertEqual(self.run_cli(argv, runner), 0)
            lines = runner.lines()
            self.assertTrue(lines[1].startswith("gh issue comment 5"))
            self.assertEqual(lines[2], "gh issue edit 5 --add-label closed:duplicate")
            self.assertEqual(lines[3], "gh issue close 5 --reason duplicate --duplicate-of 3")
            self.assertTrue(lines[4].startswith("gh issue comment 3"))

    def test_close_lock_flag(self):
        runner = FakeRunner()
        self.assertEqual(self.run_cli(["close", "issue", "5", "developer", "-", "--lock"],
                                      runner), 0)
        self.assertEqual(runner.lines()[-1], "gh issue lock 5")

    def test_bad_flags_exit_64(self):
        base = ["issue", "5", "developer", "-"]
        for argv in (["close"] + base + ["--bogus"], ["close"] + base + ["--reason"],
                     ["close"] + base + ["--duplicate-of", "x"],
                     ["close"] + base + ["--duplicate-of"],
                     ["reopen"] + base + ["--lock"], ["reopen"] + base + ["--reason", "spam"]):
            runner = FakeRunner()
            self.assertEqual(self.run_cli(argv, runner), 64, argv)
            self.assertEqual(runner.calls, [])

    def test_empty_reason_exits_1(self):
        runner = FakeRunner()
        with mock.patch.object(cli, "_require_config", return_value=cfg()), \
                mock.patch.object(cli.scan, "load_terms", return_value=[]), \
                mock.patch.object(cli.run, "run", runner), \
                mock.patch("sys.stdin", io.StringIO("  ")), \
                mock.patch("sys.stderr", io.StringIO()):
            code = cli.cmd_reopen(["pr", "5", "developer", "-"])
        self.assertEqual(code, 1)
        self.assertEqual(runner.calls, [])


if __name__ == "__main__":
    unittest.main()
