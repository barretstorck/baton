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
