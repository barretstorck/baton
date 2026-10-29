import copy
import json
import pathlib
import unittest
from baton import comment, config, gate
from tests.fakes import FakeRunner

OWNER = "octo"


def cfg():
    return config.Config(root=pathlib.Path("."), data=copy.deepcopy(config.DEFAULTS))


def c(login, body, n):
    return {"author": {"login": login}, "body": body, "url": f"https://example.com/c/{n}"}


def bot(login, body, n):
    item = c(login, body, n)
    item["author"]["__typename"] = "Bot"
    return item


Q = "<!-- baton:developer:question -->\n**Baton developer: question**\n\nWhich?"


class ComposeTest(unittest.TestCase):
    def test_body_starts_with_marker_and_visible_prefix(self):
        text = comment.compose("developer", "question", "Which mode?")
        self.assertTrue(text.startswith("<!-- baton:developer:question -->\n**Baton developer: question**"))
        self.assertTrue(text.startswith(gate.MARKER))
        self.assertIn("Which mode?", text)

    def test_closed_and_reopened_markers(self):
        for kind in ("closed", "reopened"):
            with self.subTest(kind=kind):
                text = comment.compose("reviewer", kind, "x")
                self.assertTrue(text.startswith(f"<!-- baton:reviewer:{kind} -->\n"))
                self.assertIn("x", text)

    def test_unknown_role_or_kind_is_rejected(self):
        for role, kind in (("maintainer", "status"), ("developer", "approval")):
            with self.subTest(role=role, kind=kind):
                with self.assertRaises(comment.CommentError):
                    comment.compose(role, kind, "x")


class RolesTest(unittest.TestCase):
    def test_researcher_role_composes(self):
        self.assertIn("researcher:findings", comment.compose("researcher", "findings", "x"))

    def test_skill_comment_roles_are_known(self):
        import re
        root = pathlib.Path(__file__).resolve().parent.parent / "skills"
        seen = set()
        for skill in root.glob("*/SKILL.md"):
            seen.update(re.findall(r"baton comment (?:issue|pr) <\w+> (\w+) <?\w+>? ?-?",
                                   skill.read_text()))
        self.assertIn("researcher", seen)
        self.assertLessEqual(seen, comment.ROLES)


class PostTest(unittest.TestCase):
    def test_reviewer_role_and_reply_kind(self):
        text = comment.compose("developer", "reply", "1. Fixed in abc1234.")
        self.assertTrue(text.startswith("<!-- baton:developer:reply -->"))
        self.assertIn("reviewer", comment.ROLES)

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

    def test_credentials_are_refused(self):
        for shape in ("ghs_" + "a1B2" * 6, "-----BEGIN " + "PRIVATE KEY-----"):
            runner = FakeRunner()
            with self.assertRaises(comment.CommentError):
                comment.post(cfg(), runner, "issue", 7, "developer", "status", "got " + shape, [])
            self.assertEqual(runner.calls, [])

    def test_bad_target_is_rejected(self):
        with self.assertRaises(comment.CommentError):
            comment.post(cfg(), FakeRunner(), "release", 7, "developer", "status", "x", [])


class BodyArgumentTest(unittest.TestCase):
    def test_dash_reads_the_body_from_stdin(self):
        import io
        self.assertEqual(comment.read_body("-", io.StringIO("from stdin\n")), "from stdin\n")

    def test_path_reads_the_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as handle:
            handle.write("from a file")
        self.assertEqual(comment.read_body(handle.name, None), "from a file")

    def test_empty_body_is_refused(self):
        import io
        with self.assertRaises(comment.CommentError):
            comment.read_body("-", io.StringIO("  \n"))


class AnswersTest(unittest.TestCase):
    def test_no_question_means_nothing_pending(self):
        result = comment.pending_answers({"comments": [c(OWNER, "hello", 1)]}, OWNER)
        self.assertEqual(result, {"question": None, "answers": []})

    def test_second_maintainer_answer_counts(self):
        comments = [c(OWNER, Q, 1), c("second", "Use mode A.", 2), c("mallory", "No, B.", 3)]
        result = comment.pending_answers({"comments": comments}, [OWNER, "second"])
        self.assertEqual([a["body"] for a in result["answers"]], ["Use mode A."])

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

    def test_question_by_a_bot_is_found(self):
        result = comment.pending_answers({"comments": [bot("baton", Q, 1)]}, OWNER, {"baton[bot]"})
        self.assertEqual(result["question"], "https://example.com/c/1")

    def test_question_marker_by_a_stranger_is_ignored(self):
        result = comment.pending_answers({"comments": [c("mallory", Q, 1)]}, OWNER, {"baton[bot]"})
        self.assertIsNone(result["question"])

    def test_decision_by_a_bot_counts_as_an_answer(self):
        decision = "<!-- baton:orchestrator:decision -->\n> Use A."
        comments = [c(OWNER, Q, 1), bot("baton", decision, 2)]
        result = comment.pending_answers({"comments": comments}, OWNER, {"baton[bot]"})
        self.assertEqual(len(result["answers"]), 1)

    def test_markerless_bot_comment_is_not_an_answer(self):
        comments = [c(OWNER, Q, 1), bot("baton", "Use A.", 2)]
        for owner in (OWNER, [OWNER, "baton[bot]"]):
            result = comment.pending_answers({"comments": comments}, owner, {"baton[bot]"})
            self.assertEqual(result["answers"], [])


class NotifyTest(unittest.TestCase):
    def runner(self, comments=()):
        return FakeRunner({
            "gh issue view 7 --json comments": json.dumps({"comments": list(comments)}),
            "gh issue comment": "https://example.com/c/9\n"})

    def notify(self, runner, cause="stuck", maintainers=("a", "b")):
        return comment.notify(cfg(), runner, 7, cause, "Stuck.", list(maintainers), [])

    def test_adds_label_and_posts_one_comment_with_marker_and_handles(self):
        runner = self.runner()
        self.assertEqual(self.notify(runner), "https://example.com/c/9")
        lines = runner.lines()
        self.assertIn("gh issue edit 7 --add-label needs-maintainer", lines)
        self.assertEqual(len([x for x in lines if x.startswith("gh issue comment")]), 1)

    def test_body_has_marker_and_every_handle(self):
        seen = {}

        class Runner(FakeRunner):
            def __call__(self, argv, cwd=None, input=None):
                if argv[:3] == ["gh", "issue", "comment"]:
                    seen["body"] = pathlib.Path(argv[argv.index("--body-file") + 1]).read_text()
                    return "u"
                return "{}"

        self.notify(Runner())
        self.assertIn("<!-- baton:notify:stuck -->\n@a @b\nStuck.", seen["body"])
        self.assertTrue(seen["body"].startswith("<!-- baton:orchestrator:status -->"))

    def test_second_call_for_the_same_cause_does_nothing(self):
        marked = {"body": "<!-- baton:orchestrator:status -->\n**x**\n\n<!-- baton:notify:stuck -->\n@a"}
        runner = self.runner([marked])
        self.assertIsNone(self.notify(runner))
        self.assertFalse([x for x in runner.lines() if x.startswith(("gh issue edit", "gh issue comment"))])
        self.assertTrue(comment.notified([marked], "stuck"))
        self.assertFalse(comment.notified([marked], "approval"))

    def test_a_different_cause_still_posts(self):
        runner = self.runner([{"body": "<!-- baton:notify:stuck -->"}])
        self.assertEqual(self.notify(runner, "approval"), "https://example.com/c/9")

    def test_unknown_cause_and_no_maintainers_raise(self):
        with self.assertRaises(comment.CommentError):
            self.notify(self.runner(), "bogus")
        with self.assertRaises(comment.CommentError):
            self.notify(self.runner(), maintainers=())

    def test_notify_comment_is_not_an_answer_or_question(self):
        text = comment.compose("orchestrator", "status", "<!-- baton:notify:stuck -->\n@octo\nx")
        issue = {"comments": [c("octo", text, 1)]}
        self.assertEqual(comment.pending_answers(issue, OWNER),
                         comment.pending_answers({"comments": []}, OWNER))


class NotifyCliTest(unittest.TestCase):
    def _run(self, args, issues=None, comments=None):
        import io
        from unittest import mock
        from baton import cli
        calls = []

        def runner(cmd, *a, **k):
            calls.append(cmd)
            if cmd[:3] == ["gh", "issue", "list"]:
                return json.dumps(issues or [])
            if cmd[:3] == ["gh", "issue", "view"]:
                return json.dumps({"comments": comments or []})
            if cmd[:3] == ["gh", "issue", "comment"]:
                return "https://x/1#c"
            return ""
        cfg = mock.MagicMock()
        cfg.maintainers.return_value = ["me"]
        cfg.label.side_effect = lambda n: n
        cfg.data = {"privacy_allow": []}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "_require_config", return_value=cfg), \
                mock.patch.object(cli, "_owner", return_value="me"), \
                mock.patch.object(cli.identity, "use"), \
                mock.patch.object(cli.run, "run", runner), \
                mock.patch.object(cli.scan, "load_terms", return_value=[]), \
                mock.patch.object(cli.questions, "notify_matches",
                                  return_value=[7] if issues else []), \
                mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            code = cli.main(args)
        return code, out.getvalue(), err.getvalue(), calls

    def test_registered(self):
        from baton import cli
        self.assertIn("notify", cli.COMMANDS)

    def test_wrong_arguments_exit_64(self):
        for args in (["notify"], ["notify", "1", "bogus", "-"], ["notify", "x", "stuck", "-"],
                     ["notify", "--rules", "1"]):
            code, _, err, _ = self._run(args)
            self.assertEqual(code, 64, args)
            self.assertIn("stuck", err)

    def test_notify_posts_then_already(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt") as f:
            f.write("blocked")
            f.flush()
            code, out, _, calls = self._run(["notify", "5", "stuck", f.name])
            self.assertEqual((code, out.strip()), (0, "https://x/1#c"))
            self.assertTrue(any(c[:3] == ["gh", "issue", "edit"] for c in calls))
            marked = [{"body": "<!-- baton:notify:stuck -->"}]
            code, out, _, _ = self._run(["notify", "5", "stuck", f.name], comments=marked)
            self.assertEqual((code, out.strip()), (0, "already notified"))

    def test_rules_once(self):
        issues = [{"number": 7}]
        code, out, _, _ = self._run(["notify", "--rules"], issues=issues)
        self.assertEqual((code, out.strip()), (0, "#7 https://x/1#c"))
        marked = [{"body": "<!-- baton:notify:rule -->"}]
        code, out, _, _ = self._run(["notify", "--rules"], issues=issues, comments=marked)
        self.assertEqual(out.strip(), "#7 already notified")
