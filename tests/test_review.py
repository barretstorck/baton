import copy
import json
import pathlib
import unittest
from baton import config, review, run
from tests.fakes import FakeRunner

OWNER = "octo"
OLD = "a" * 40
NEW = "b" * 40
DIFF = ("diff --git a/x.py b/x.py\nindex 1..2 100644\n--- a/x.py\n+++ b/x.py\n"
        "@@ -1,3 +1,3 @@\n ctx\n-old\n+new\n")


def cfg(**review_settings):
    data = copy.deepcopy(config.DEFAULTS)
    data["review"].update(review_settings)
    return config.Config(root=pathlib.Path("."), data=data)


def rev(verdict, commit=NEW, login=OWNER):
    return {"author": {"login": login}, "url": f"u/{verdict}",
            "body": review.compose(verdict, commit, "Round 1.")}


def reply(login=OWNER):
    return {"author": {"login": login}, "url": "u/reply",
            "body": "<!-- baton:developer:reply -->\n**Baton developer: reply**\n\n1. Fixed."}


def diff_runner(old_diff, new_diff):
    return FakeRunner({
        f"git merge-base origin/main {OLD}": "base1\n",
        f"git merge-base origin/main {NEW}": "base2\n",
        f"git diff --binary base1 {OLD}": old_diff,
        f"git diff --binary base2 {NEW}": new_diff,
    })


class ComposeTest(unittest.TestCase):
    def test_marker_carries_verdict_and_commit(self):
        text = review.compose("changes", NEW, "Fix the off-by-one.")
        self.assertTrue(text.startswith(
            f"<!-- baton:reviewer:review verdict=changes commit={NEW} -->"))
        self.assertIn("Fix the off-by-one.", text)

    def test_bad_input_is_refused(self):
        for args in (("maybe", NEW, "x"), ("approve", "abc1234", "x"), ("approve", NEW, "  ")):
            with self.subTest(args=args), self.assertRaises(review.ReviewError):
                review.compose(*args)


class StateTest(unittest.TestCase):
    def test_states_in_order(self):
        self.assertEqual(review.state([], OWNER, 2)["state"], "none")
        self.assertEqual(review.state([rev("changes")], OWNER, 2)["state"], "changes")
        self.assertEqual(review.state([rev("changes"), reply()], OWNER, 2)["state"], "replied")
        done = review.state([rev("changes"), reply(), rev("approve")], OWNER, 2)
        self.assertEqual((done["state"], done["rounds"]), ("approved", 0))

    def test_rounds_used_up_escalates(self):
        found = review.state([rev("changes"), reply(), rev("changes")], OWNER, 2)
        self.assertEqual(found["state"], "escalate")
        # The maintainer settled it and the developer replied: review again.
        found = review.state([rev("changes"), reply(), rev("changes"), reply()], OWNER, 2)
        self.assertEqual(found["state"], "replied")

    def test_rounds_restart_after_an_approval(self):
        # Approved, then a CI fix made it stale; the next request for changes is round 1.
        found = review.state([rev("approve", OLD), rev("changes")], OWNER, 2)
        self.assertEqual((found["state"], found["rounds"]), ("changes", 1))

    def test_reply_after_approval_asks_for_another_review(self):
        self.assertEqual(review.state([rev("approve"), reply()], OWNER, 2)["state"], "replied")

    def test_foreign_and_malformed_reviews_are_ignored(self):
        forged = rev("approve", login="mallory")
        short = {"author": {"login": OWNER},
                 "body": "<!-- baton:reviewer:review verdict=approve commit=abc1234 -->\nok"}
        quoted = {"author": {"login": OWNER},
                  "body": "see\n" + review.compose("approve", NEW, "x")}
        self.assertEqual(review.state([forged, short, quoted], OWNER, 2)["state"], "none")


class SameChangeTest(unittest.TestCase):
    def test_fingerprint_ignores_positions_and_blob_ids(self):
        shifted = (DIFF.replace("index 1..2", "index 7..8")
                   .replace("@@ -1,3 +1,3 @@", "@@ -9,3 +9,3 @@ def f():"))
        self.assertEqual(review.fingerprint(DIFF), review.fingerprint(shifted))
        self.assertNotEqual(review.fingerprint(DIFF),
                            review.fingerprint(DIFF.replace("+new", "+newer")))

    def test_moved_lines_modes_and_binaries_are_different_changes(self):
        moved = DIFF.replace(" ctx", " other")
        chmod = DIFF.replace("index 1..2 100644", "old mode 100644\nnew mode 100755\nindex 1..2")
        binary = "diff --git a/b.bin b/b.bin\nindex 1..2 100644\nGIT binary patch\nliteral 4\nLcmZ?d00001\n"
        for other in (moved, chmod):
            with self.subTest(other=other):
                self.assertNotEqual(review.fingerprint(DIFF), review.fingerprint(other))
        self.assertNotEqual(review.fingerprint(binary),
                            review.fingerprint(binary.replace("LcmZ?d00001", "LcmZ?d00002")))

    def test_same_and_different_changes(self):
        self.assertTrue(review.same_change(diff_runner(DIFF, DIFF), "origin/main", OLD, NEW))
        self.assertFalse(review.same_change(diff_runner(DIFF, DIFF + "+more\n"),
                                            "origin/main", OLD, NEW))

    def test_same_commit_and_prefix_need_no_git(self):
        runner = FakeRunner()
        self.assertTrue(review.same_change(runner, "origin/main", NEW[:7], NEW))
        self.assertEqual(runner.calls, [])

    def test_paths_limit_the_diff(self):
        runner = diff_runner(DIFF, DIFF)
        review.same_change(runner, "origin/main", OLD, NEW, (".github/",))
        self.assertIn(f"git diff --binary base1 {OLD} -- .github/", runner.lines())

    def test_unreachable_commit_is_not_the_same_change(self):
        class Failing(FakeRunner):
            def __call__(self, argv, cwd=None):
                raise run.RunError("bad object")
        self.assertFalse(review.same_change(Failing(), "origin/main", OLD, NEW))


class CurrentTest(unittest.TestCase):
    def pr(self, comments, head=NEW):
        return {"number": 9, "headRefOid": head, "headRefName": "issue-4-x", "comments": comments}

    def test_approval_of_the_head_is_approved(self):
        runner = FakeRunner()
        found = review.current(self.pr([rev("approve")]), OWNER, cfg(), runner, "main")
        self.assertEqual(found["state"], "approved")
        self.assertEqual(runner.calls, [])

    def test_approval_of_an_earlier_identical_change_is_approved(self):
        runner = diff_runner(DIFF, DIFF)
        found = review.current(self.pr([rev("approve", OLD)]), OWNER, cfg(), runner, "main")
        self.assertEqual(found["state"], "approved")
        self.assertIn("git fetch --quiet origin main issue-4-x", runner.lines())

    def test_approval_of_a_different_change_is_stale(self):
        runner = diff_runner(DIFF, DIFF + "+x\n")
        found = review.current(self.pr([rev("approve", OLD)]), OWNER, cfg(), runner, "main")
        self.assertEqual(found["state"], "stale")


class PlanRemovalTest(unittest.TestCase):
    def test_diff_covers_the_whole_tree(self):
        runner = diff_runner(DIFF, DIFF)
        pr = {"number": 9, "headRefOid": NEW, "headRefName": "issue-4-x",
              "comments": [rev("approve", OLD)]}
        found = review.current(pr, OWNER, cfg(), runner, "main")
        self.assertEqual(found["state"], "approved")
        spec = "-- ."
        self.assertIn(f"git diff --binary base1 {OLD} {spec}", runner.lines())
        self.assertIn(f"git diff --binary base2 {NEW} {spec}", runner.lines())

    def test_other_changes_still_make_it_stale(self):
        runner = diff_runner(DIFF, DIFF + "+x\n")
        pr = {"number": 9, "headRefOid": NEW, "headRefName": "issue-4-x",
              "comments": [rev("approve", OLD)]}
        self.assertEqual(review.current(pr, OWNER, cfg(), runner, "main")["state"], "stale")


PLAN = "docs/baton/plans/4-x.md"
OTHER_PLAN = "docs/baton/plans/5-y.md"
PLAN_BODY = f"Plan: [{PLAN}](https://github.com/o/r/blob/issue-4-x/{PLAN})\n\nBody."
EXCLUDE = f"-- . :(exclude){PLAN}"


def plan_runner(full_new, status, excluded_new=DIFF, body=PLAN_BODY):
    """Approved OLD, head NEW: `full_new` is the whole-tree diff of the head, `status` the
    name-status of the linked plan file between them, `excluded_new` the head's diff without it."""
    return FakeRunner({
        f"git merge-base origin/main {OLD}": "base1\n",
        f"git merge-base origin/main {NEW}": "base2\n",
        f"git diff --binary base1 {OLD} -- .": DIFF,
        f"git diff --binary base2 {NEW} -- .": full_new,
        f"git diff --binary base1 {OLD} {EXCLUDE}": DIFF,
        f"git diff --binary base2 {NEW} {EXCLUDE}": excluded_new,
        f"git diff --name-status {OLD} {NEW} -- {PLAN}": status,
        "gh issue view 4 --json body": json.dumps({"body": body}),
    })


class LinkedPlanDeletionTest(unittest.TestCase):
    def state(self, runner, closing=(4,)):
        pr = {"number": 9, "headRefOid": NEW, "headRefName": "issue-4-x",
              "comments": [rev("approve", OLD)],
              "closingIssuesReferences": [{"number": n} for n in closing]}
        return review.current(pr, OWNER, cfg(), runner, "main")["state"]

    def test_deleting_only_the_linked_plan_stays_approved(self):
        runner = plan_runner(DIFF + "-plan\n", f"D\t{PLAN}\n")
        self.assertEqual(self.state(runner), "approved")

    def test_editing_the_linked_plan_is_stale(self):
        runner = plan_runner(DIFF + "+plan\n", f"M\t{PLAN}\n")
        self.assertEqual(self.state(runner), "stale")

    def test_adding_the_linked_plan_is_stale(self):
        runner = plan_runner(DIFF + "+plan\n", f"A\t{PLAN}\n")
        self.assertEqual(self.state(runner), "stale")

    def test_changing_another_plan_is_stale(self):
        for change in ("added", "edited", "deleted"):
            with self.subTest(change=change):
                # The linked plan is untouched, so only the other plan's change differs.
                runner = plan_runner(DIFF + f"{change} {OTHER_PLAN}\n", "")
                self.assertEqual(self.state(runner), "stale")

    def test_deleting_the_linked_plan_and_another_file_is_stale(self):
        runner = plan_runner(DIFF + "-plan\n+x\n", f"D\t{PLAN}\n", excluded_new=DIFF + "+x\n")
        self.assertEqual(self.state(runner), "stale")

    def test_nothing_is_exempt_without_a_plan_link(self):
        runner = plan_runner(DIFF + "-plan\n", f"D\t{PLAN}\n", body="No link here.")
        self.assertEqual(self.state(runner), "stale")

    def test_nothing_is_exempt_without_one_closing_issue(self):
        for closing in ((), (4, 6)):
            with self.subTest(closing=closing):
                runner = plan_runner(DIFF + "-plan\n", f"D\t{PLAN}\n")
                self.assertEqual(self.state(runner, closing), "stale")
                self.assertFalse(any("gh issue view" in line for line in runner.lines()))


TASKS = "## Tasks\n- [ ] **1.1 Fix it**\n  - Check: none\n"


def changes_with_tasks(commit=NEW, ticked=False):
    body = TASKS.replace("[ ]", "[x]") if ticked else TASKS
    return {"author": {"login": OWNER}, "url": "https://x/pull/9#issuecomment-55",
            "body": review.compose("changes", commit, body)}


def pr_runner(comments=(), closing=(), issue_body=""):
    return FakeRunner({
        "gh pr view 9 --json headRefOid,comments,closingIssuesReferences": json.dumps(
            {"headRefOid": NEW, "comments": list(comments),
             "closingIssuesReferences": [{"number": n} for n in closing]}),
        "gh pr view 9 --json comments": json.dumps({"comments": list(comments)}),
        "gh issue view 4 --json body": json.dumps({"body": issue_body}),
        "gh pr comment 9": "https://example.com/c/1\n",
    })


class ReviewTasksTest(unittest.TestCase):
    def test_latest_changes_review_tasks(self):
        found = review.review_tasks({"comments": [changes_with_tasks()]}, OWNER)
        self.assertEqual((found["round"], found["next"], found["total"]), (1, "1.1", 1))

    def test_after_approval_there_are_none(self):
        found = review.review_tasks({"comments": [changes_with_tasks(), rev("approve")]}, OWNER)
        self.assertEqual(found["tasks"], [])

    def test_tick_review_patches_that_comment(self):
        runner = pr_runner([changes_with_tasks()])
        self.assertTrue(review.tick_review(runner, 9, OWNER, "1.1"))
        patch = [c for c in runner.calls if c[:4] == ["gh", "api", "-X", "PATCH"]]
        self.assertEqual(patch[0][4], "repos/{owner}/{repo}/issues/comments/55")

    def test_tick_review_already_ticked(self):
        runner = pr_runner([changes_with_tasks(ticked=True)])
        self.assertFalse(review.tick_review(runner, 9, OWNER, "1.1"))
        self.assertFalse(any("PATCH" in line for line in runner.lines()))


class PostGateTest(unittest.TestCase):
    PLAN_OPEN = "## Plan\nWritten against: " + "c" * 40 + "\n\n- [ ] **1. A**\n"

    def test_approve_refused_with_open_review_task(self):
        runner = pr_runner([changes_with_tasks(), reply()])
        with self.assertRaises(review.ReviewError):
            review.post(cfg(), runner, 9, "approve", NEW, "Looks right.", [], OWNER)
        self.assertFalse(any("pr comment" in line for line in runner.lines()))

    def test_approve_refused_with_open_plan_task(self):
        runner = pr_runner(closing=[4], issue_body=self.PLAN_OPEN)
        with self.assertRaises(review.ReviewError):
            review.post(cfg(), runner, 9, "approve", NEW, "Looks right.", [], OWNER)

    def test_approve_when_all_ticked(self):
        runner = pr_runner([changes_with_tasks(ticked=True), reply()], closing=[4],
                           issue_body=self.PLAN_OPEN.replace("[ ]", "[x]"))
        self.assertEqual(review.post(cfg(), runner, 9, "approve", NEW, "Good.", [], OWNER),
                         "https://example.com/c/1")

    def test_changes_need_tasks_numbered_for_the_round(self):
        runner = pr_runner([changes_with_tasks(ticked=True), reply()])
        for body in ("No checklist.", TASKS):          # round 2 needs 2.n ids
            with self.subTest(body=body), self.assertRaises(review.ReviewError):
                review.post(cfg(), runner, 9, "changes", NEW, body, [], OWNER)
        self.assertEqual(review.post(cfg(), runner, 9, "changes", NEW,
                                     TASKS.replace("1.1", "2.1"), [], OWNER),
                         "https://example.com/c/1")


class PostTest(unittest.TestCase):
    def test_post_refuses_a_moved_head(self):
        runner = pr_runner()
        with self.assertRaises(review.ReviewError):
            review.post(cfg(), runner, 9, "approve", OLD, "Round 1.", [], OWNER)
        self.assertFalse(any("pr comment" in line for line in runner.lines()))

    def test_post_publishes_the_stamped_review(self):
        runner = pr_runner()
        self.assertEqual(review.post(cfg(), runner, 9, "approve", NEW, "Round 1.", [], OWNER),
                         "https://example.com/c/1")


class InLinkedWorktreeTests(unittest.TestCase):
    BASE = "git rev-parse --path-format=absolute"

    def test_equal_paths_is_not_linked(self):
        runner = FakeRunner({f"{self.BASE} --git-dir": "/r/.git\n",
                             f"{self.BASE} --git-common-dir": "/r/.git\n"})
        self.assertFalse(review.in_linked_worktree(runner, "/r"))

    def test_different_paths_is_linked(self):
        runner = FakeRunner({f"{self.BASE} --git-dir": "/r/.git/worktrees/w\n",
                             f"{self.BASE} --git-common-dir": "/r/.git\n"})
        self.assertTrue(review.in_linked_worktree(runner, "/w"))
