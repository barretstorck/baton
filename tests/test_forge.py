import json
import re
import unittest
from pathlib import Path
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

    def test_milestones(self):
        runner = FakeRunner({"gh api": ""})
        self.assertEqual(GitHub(runner).milestones(), [])
        runner = FakeRunner({"gh api": '[{"number": 1}]'})
        self.assertEqual(GitHub(runner).milestones(), [{"number": 1}])
        self.assertEqual(runner.lines(), ["gh api repos/{owner}/{repo}/milestones?state=all"])

    def test_milestone_create(self):
        m = "repos/{owner}/{repo}/milestones"
        self._check(lambda b, r: b.milestone_create("T", role=r), f"gh api {m} -f title=T")
        self._check(lambda b, r: b.milestone_create("T", "D", "2026-01-01", role=r),
                    f"gh api {m} -f title=T -f description=D -f due_on=2026-01-01")

    def test_milestone_edit(self):
        self._check(lambda b, r: b.milestone_edit(
            "3", [("title", "N"), ("state", "closed")], role=r),
            "gh api --method PATCH repos/{owner}/{repo}/milestones/3 -f title=N -f state=closed")

    def test_issue_create(self):
        self._check(lambda b, r: b.issue_create("T", "p.md", role=r),
                    "gh issue create --title T --body-file p.md")
        self._check(lambda b, r: b.issue_create("T", "p.md", ["a", "b"], "M", role=r),
                    "gh issue create --title T --body-file p.md --label a --label b --milestone M")

    def test_item_edit(self):
        self._check(lambda b, r: b.item_edit("pr", 4, role=r), "gh pr edit 4")
        self._check(lambda b, r: b.item_edit(
            "issue", 4, title="T", add_labels=["a"], remove_labels=["r"], milestone="M",
            remove_milestone=True, path="p.md", role=r),
            "gh issue edit 4 --title T --add-label a --remove-label r --milestone M"
            " --remove-milestone --body-file p.md")

    def test_pr_create(self):
        self._check(lambda b, r: b.pr_create("T", "p.md", role=r),
                    "gh pr create --title T --body-file p.md")
        self._check(lambda b, r: b.pr_create("T", "p.md", head="h", draft=True, role=r),
                    "gh pr create --title T --body-file p.md --head h --draft")

    def test_pr_ready(self):
        self._check(lambda b, r: b.pr_ready(5, role=r), "gh pr ready 5")
        self._check(lambda b, r: b.pr_ready(5, undo=True, role=r), "gh pr ready 5 --undo")

    def test_pr_review(self):
        self._check(lambda b, r: b.pr_review(3, "approve", "f.md", role=r),
                    "gh pr review 3 --approve --body-file f.md")

    def test_review_edit_body(self):
        self._check(lambda b, r: b.review_edit_body(3, "PRR_1", "/p.md", role=r),
                    "gh api -X PUT repos/{owner}/{repo}/pulls/3/reviews/PRR_1 "
                    "-F body=@/p.md --silent")

    def test_comment_edit_body(self):
        self._check(lambda b, r: b.comment_edit_body(77, "/p.md", role=r),
                    "gh api -X PATCH repos/{owner}/{repo}/issues/comments/77 "
                    "-F body=@/p.md --silent")

    def test_review_request_remove(self):
        self._check(lambda b, r: b.review_request_remove(3, "bot", role=r),
                    "gh api -X DELETE repos/{owner}/{repo}/pulls/3/requested_reviewers "
                    "-f reviewers[]=bot --silent")


def _boom(argv, **kw):
    raise run.RunError("x")


class ReadTest(unittest.TestCase):
    def test_review_urls(self):
        runner = FakeRunner({"gh api": "u1\n\n  u2 \n"})
        self.assertEqual(GitHub(runner).review_urls(5, "bot"), ["u1", "u2"])
        self.assertEqual(runner.lines(), [
            "gh api --paginate repos/{owner}/{repo}/pulls/5/reviews?per_page=100 "
            "--jq .[] | select(.user.login == \"bot\") | .html_url"])

    def test_pr_review_items(self):
        runner = FakeRunner({"gh api": '{"id": "a"}\n\n  \n{"id": "b"}\n'})
        self.assertEqual(GitHub(runner).pr_review_items(5), [{"id": "a"}, {"id": "b"}])
        self.assertEqual(len(runner.lines()), 1)
        self.assertTrue(runner.lines()[0].startswith(
            "gh api --paginate repos/{owner}/{repo}/pulls/5/reviews?per_page=100 --jq "))
        self.assertEqual(GitHub(FakeRunner({"gh api": ""})).pr_review_items(5), [])

    def test_user(self):
        runner = FakeRunner({"gh api": '{"id": 7}'})
        self.assertEqual(GitHub(runner).user("b"), {"id": 7})
        self.assertEqual(runner.lines(), ["gh api users/b"])
        with self.assertRaises(ValueError):
            GitHub(FakeRunner()).user("b")

    def test_comment_author_types(self):
        runner = FakeRunner({"gh api": "n1\tBot\nbad\n"})
        self.assertEqual(GitHub(runner).comment_author_types(8), {"n1": "Bot"})
        self.assertEqual(runner.lines(), [
            "gh api --paginate repos/{owner}/{repo}/issues/8/comments?per_page=100 "
            "--jq .[] | [.node_id, .user.type] | @tsv"])
        with self.assertRaises(run.RunError):
            GitHub(_boom).comment_author_types(8)

    def test_issue_body_edits(self):
        runner = FakeRunner({"gh api graphql": "2026-01-01\tbob\tUser\n2026-01-02\t\t\nbad\n"})
        self.assertEqual(GitHub(runner).issue_body_edits(4), [
            {"editedAt": "2026-01-01", "editor": {"login": "bob", "__typename": "User"}},
            {"editedAt": "2026-01-02", "editor": None}])
        self.assertTrue(runner.lines()[0].startswith(
            "gh api graphql --paginate -F owner={owner} -F repo={repo} -F number=4 -f query="))
        self.assertIsNone(GitHub(_boom).issue_body_edits(4))

    def test_issue_timeline(self):
        runner = FakeRunner({"gh api": "assigned\ta\tb\t2026-01-01\nshort\n"})
        self.assertEqual(GitHub(runner).issue_timeline(6), [
            {"event": "assigned", "by": "a", "subject": "b", "at": "2026-01-01"}])
        self.assertTrue(runner.lines()[0].startswith(
            "gh api --paginate repos/{owner}/{repo}/issues/6/timeline?per_page=100 --jq "))
        self.assertIsNone(GitHub(_boom).issue_timeline(6))


class MergeMethodsTest(unittest.TestCase):
    def test_run_view(self):
        runner = FakeRunner({"gh run view 9 --json status,url": json.dumps({"status": "completed"})})
        self.assertEqual(GitHub(runner).run_view("9", "status,url"), {"status": "completed"})
        self.assertEqual(runner.lines(), ["gh run view 9 --json status,url"])
        with self.assertRaises(ValueError):
            GitHub(FakeRunner({"gh run view 9 --json x": ""})).run_view("9", "x")

    def test_pr_diff(self):
        runner = FakeRunner({"gh pr diff 4": "diff --git a b"})
        self.assertEqual(GitHub(runner).pr_diff(4), "diff --git a b")
        self.assertEqual(runner.lines(), ["gh pr diff 4"])

    def test_pr_merge(self):
        runner = FakeRunner({"gh pr merge 4 --merge --match-head-commit abc": ""})
        GitHub(runner).pr_merge(4, "abc")
        self.assertEqual(runner.lines(), ["gh pr merge 4 --merge --match-head-commit abc"])


GH_ARGV = re.compile(r'\[\s*"gh"\s*,')
LIB = Path(__file__).resolve().parent.parent / "lib" / "baton"
# #374 and #375 empty this set as they move the remaining call sites.
NOT_YET_MIGRATED = {"cli.py", "sprint.py", "triage.py"}
# The forge_clis default list is not a call.
PERMANENT = {"config.py"}


def _matching_files():
    return {p.name for p in LIB.glob("*.py") if GH_ARGV.search(p.read_text())}


class ForgeLayoutTest(unittest.TestCase):
    def test_no_gh_argv_outside_forge(self):
        bad = sorted(_matching_files() - NOT_YET_MIGRATED - PERMANENT)
        self.assertEqual(bad, [], f"gh argv outside the forge package: {bad}")

    def test_not_yet_migrated_is_current(self):
        stale = sorted(NOT_YET_MIGRATED - _matching_files())
        self.assertEqual(stale, [], f"no longer match, remove from NOT_YET_MIGRATED: {stale}")


if __name__ == "__main__":
    unittest.main()
