import copy
import pathlib
import unittest
from baton import comment, config, gate
from tests.fakes import FakeRunner

OWNER = "octo"


def cfg():
    return config.Config(root=pathlib.Path("."), data=copy.deepcopy(config.DEFAULTS))


def c(login, body, n):
    return {"author": {"login": login}, "body": body, "url": f"https://example.com/c/{n}"}


Q = "<!-- baton:developer:question -->\n**Baton developer: question**\n\nWhich?"


class ComposeTest(unittest.TestCase):
    def test_body_starts_with_marker_and_visible_prefix(self):
        text = comment.compose("developer", "question", "Which mode?")
        self.assertTrue(text.startswith("<!-- baton:developer:question -->\n**Baton developer: question**"))
        self.assertTrue(text.startswith(gate.MARKER))
        self.assertIn("Which mode?", text)

    def test_unknown_role_or_kind_is_rejected(self):
        for role, kind in (("maintainer", "status"), ("developer", "approval")):
            with self.subTest(role=role, kind=kind):
                with self.assertRaises(comment.CommentError):
                    comment.compose(role, kind, "x")


class PostTest(unittest.TestCase):
    def test_posts_stamped_body_and_returns_url(self):
        seen = {}

        class Runner(FakeRunner):
            def __call__(self, argv, cwd=None):
                if argv[:3] == ["gh", "issue", "comment"]:
                    seen["body"] = pathlib.Path(argv[argv.index("--body-file") + 1]).read_text()
                    self.calls.append(argv)
                    return "https://example.com/c/9\n"
                return super().__call__(argv, cwd)

        runner = Runner()
        url = comment.post(cfg(), runner, "issue", 7, "developer", "status", "Done.", ["Zebrowski"])
        self.assertEqual(url, "https://example.com/c/9")
        self.assertTrue(seen["body"].startswith(gate.MARKER))
        self.assertEqual(runner.calls[-1][:4], ["gh", "issue", "comment", "7"])

    def test_private_text_is_refused_and_nothing_is_posted(self):
        runner = FakeRunner()
        with self.assertRaises(comment.CommentError) as caught:
            comment.post(cfg(), runner, "pr", 7, "developer", "status", "the Zebrowski set", ["Zebrowski"])
        self.assertEqual(runner.calls, [])
        self.assertNotIn("Zebrowski", str(caught.exception))

    def test_bad_target_is_rejected(self):
        with self.assertRaises(comment.CommentError):
            comment.post(cfg(), FakeRunner(), "release", 7, "developer", "status", "x", [])


class AnswersTest(unittest.TestCase):
    def test_no_question_means_nothing_pending(self):
        result = comment.pending_answers({"comments": [c(OWNER, "hello", 1)]}, OWNER)
        self.assertEqual(result, {"question": None, "answers": []})

    def test_owner_comment_after_question_is_the_answer(self):
        comments = [c(OWNER, "earlier note", 1), c(OWNER, Q, 2), c(OWNER, "Use mode A.", 3)]
        result = comment.pending_answers({"comments": comments}, OWNER)
        self.assertEqual(result["question"], "https://example.com/c/2")
        self.assertEqual(result["answers"], [{"url": "https://example.com/c/3", "body": "Use mode A."}])

    def test_marked_and_foreign_comments_are_not_answers(self):
        marked = "<!-- baton:orchestrator:status -->\nstill waiting"
        comments = [c(OWNER, Q, 2), c("mallory", "Use mode B.", 3), c(OWNER, marked, 4)]
        self.assertEqual(comment.pending_answers({"comments": comments}, OWNER)["answers"], [])

    def test_only_the_latest_question_counts(self):
        comments = [c(OWNER, Q, 1), c(OWNER, "A.", 2), c(OWNER, Q, 3)]
        result = comment.pending_answers({"comments": comments}, OWNER)
        self.assertEqual(result["question"], "https://example.com/c/3")
        self.assertEqual(result["answers"], [])

    def test_decision_comment_from_orchestrator_counts_as_an_answer(self):
        decision = "<!-- baton:orchestrator:decision -->\n**Baton orchestrator: decision**\n\n> Use A."
        comments = [c(OWNER, Q, 1), c(OWNER, decision, 2)]
        result = comment.pending_answers({"comments": comments}, OWNER)
        self.assertEqual(result["answers"][0]["url"], "https://example.com/c/2")
