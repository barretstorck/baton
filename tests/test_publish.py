import copy
import pathlib
import unittest
from baton import config, publish
from baton.comment import CommentError
from tests.fakes import FakeRunner


def cfg():
    return config.Config(root=pathlib.Path("."), data=copy.deepcopy(config.DEFAULTS))


class Capture(FakeRunner):
    def __call__(self, argv, cwd=None, input=None):
        if "--body-file" in argv:
            self.path = argv[argv.index("--body-file") + 1]
            with open(self.path) as f:
                self.body = f.read()
        return super().__call__(argv, cwd, input)


class IssueTest(unittest.TestCase):
    def test_create_issue_argv_and_cleanup(self):
        r = Capture({"gh issue create": "https://x/1\n"})
        url = publish.create_issue(cfg(), r, "T", "body", ["a", "b"], "M", [])
        self.assertEqual(url, "https://x/1")
        line = r.lines()[0]
        self.assertTrue(line.startswith("gh issue create --title T --body-file "))
        self.assertTrue(line.endswith("--label a --label b --milestone M"))
        self.assertEqual(r.body, "body")
        self.assertFalse(pathlib.Path(r.path).exists())

    def test_empty_body_refused(self):
        r = Capture()
        for body in ("", "  \n"):
            with self.assertRaisesRegex(CommentError, "body is empty"):
                publish.create_issue(cfg(), r, "T", body, [], None, [])
        self.assertEqual(r.calls, [])

    def test_scan_refusal_sends_nothing(self):
        r = Capture()
        with self.assertRaises(CommentError):
            publish.create_issue(cfg(), r, "T", "x", ["secretword"], None, ["secretword"])
        with self.assertRaises(CommentError):
            publish.create_pr(cfg(), r, "secretword", "x", False, ["secretword"])
        self.assertEqual(r.calls, [])

    def test_edit(self):
        r = Capture()
        publish.edit(cfg(), r, "pr", 5, [], title="N", add_labels=["a"],
                     remove_labels=["b"], remove_milestone=True)
        self.assertEqual(r.lines()[0], "gh pr edit 5 --title N --add-label a "
                         "--remove-label b --remove-milestone")
        publish.edit(cfg(), r, "issue", 5, [], body="new", milestone="M")
        self.assertIn("gh issue edit 5 --milestone M --body-file ", r.lines()[1])
        self.assertEqual(r.body, "new")

    def test_edit_nothing_or_bad(self):
        with self.assertRaises(CommentError):
            publish.edit(cfg(), Capture(), "issue", 5, [])
        with self.assertRaises(CommentError):
            publish.edit(cfg(), Capture(), "issue", 5, [], body=" ")


class PrTest(unittest.TestCase):
    def test_create_pr(self):
        r = Capture()
        publish.create_pr(cfg(), r, "T", "b", True, [])
        self.assertTrue(r.lines()[0].endswith("--draft"))
        self.assertTrue(r.lines()[0].startswith("gh pr create --title T --body-file "))

    def test_ready(self):
        r = Capture()
        publish.ready_pr(r, 7)
        publish.ready_pr(r, 7, undo=True)
        self.assertEqual(r.lines(), ["gh pr ready 7", "gh pr ready 7 --undo"])


class MilestoneTest(unittest.TestCase):
    def test_create(self):
        r = Capture()
        publish.create_milestone(cfg(), r, "M", "d", "2026-11-01T00:00:00Z", [])
        self.assertEqual(r.lines()[0], "gh api repos/{owner}/{repo}/milestones -f title=M "
                         "-f description=d -f due_on=2026-11-01T00:00:00Z")

    def test_create_scan(self):
        r = Capture()
        with self.assertRaises(CommentError):
            publish.create_milestone(cfg(), r, "M", "secretword", None, ["secretword"])
        self.assertEqual(r.calls, [])

    def test_edit_by_number(self):
        r = Capture()
        publish.edit_milestone(cfg(), r, "4", [], state="closed")
        self.assertEqual(r.lines(), ["gh api --method PATCH "
                                     "repos/{owner}/{repo}/milestones/4 -f state=closed"])

    def test_edit_by_title(self):
        r = Capture({"gh api repos/{owner}/{repo}/milestones?state=all":
                     '[{"number": 3, "title": "A"}, {"number": 9, "title": "B"}]'})
        publish.edit_milestone(cfg(), r, "B", [], description="x")
        self.assertEqual(r.lines()[1], "gh api --method PATCH "
                         "repos/{owner}/{repo}/milestones/9 -f description=x")

    def test_edit_unknown_title_and_bad_state(self):
        r = Capture({"gh api": "[]"})
        with self.assertRaises(CommentError):
            publish.edit_milestone(cfg(), r, "Z", [], state="open")
        with self.assertRaises(CommentError):
            publish.edit_milestone(cfg(), r, "1", [], state="weird")


class ParseTest(unittest.TestCase):
    def test_issue_create(self):
        o = publish.parse_issue(["create", "--title", "T", "--label", "a", "--label=b",
                                 "--milestone", "M", "-"])
        self.assertEqual((o["title"], o["labels"], o["milestone"], o["body_source"]),
                         ("T", ["a", "b"], "M", "-"))

    def test_issue_create_needs_title_and_body(self):
        for argv in (["create", "f"], ["create", "--title", "T"], [], ["x"],
                     ["create", "--bogus", "--title", "T", "f"], ["create", "--title"]):
            with self.assertRaises(publish.UsageError):
                publish.parse_issue(argv)

    def test_edit(self):
        o = publish.parse_pr(["edit", "5", "--add-label", "x", "--remove-label", "y",
                              "--body-file", "-", "--remove-milestone"])
        self.assertEqual((o["number"], o["add_labels"], o["remove_labels"], o["body_source"],
                          o["remove_milestone"]), (5, ["x"], ["y"], "-", True))
        for argv in (["edit"], ["edit", "x"], ["edit", "5", "--milestone", "M",
                                               "--remove-milestone"]):
            with self.assertRaises(publish.UsageError):
                publish.parse_issue(argv)

    def test_pr_create_and_ready(self):
        o = publish.parse_pr(["create", "--title", "T", "--draft", "f"])
        self.assertTrue(o["draft"])
        self.assertEqual(publish.parse_pr(["ready", "3", "--undo"]),
                         {"action": "ready", "number": 3, "undo": True})
        with self.assertRaises(publish.UsageError):
            publish.parse_pr(["ready"])

    def test_milestone(self):
        o = publish.parse_milestone(["create", "--title", "T", "--due-on", "2026-01-01"])
        self.assertEqual(o["due_on"], "2026-01-01")
        o = publish.parse_milestone(["edit", "v1", "--state", "closed"])
        self.assertEqual((o["ref"], o["state"]), ("v1", "closed"))
        with self.assertRaises(publish.UsageError):
            publish.parse_milestone(["edit", "v1", "--state", "bad"])


if __name__ == "__main__":
    unittest.main()
