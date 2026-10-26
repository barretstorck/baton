import copy
import pathlib
import unittest
from baton import comment, config, state
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


if __name__ == "__main__":
    unittest.main()
