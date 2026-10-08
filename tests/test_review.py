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


class PostTest(unittest.TestCase):
    def test_post_refuses_a_moved_head(self):
        runner = FakeRunner({"gh pr view 9 --json headRefOid": json.dumps({"headRefOid": NEW})})
        with self.assertRaises(review.ReviewError):
            review.post(cfg(), runner, 9, "approve", OLD, "Round 1.", [])
        self.assertFalse(any("comment" in line for line in runner.lines()))

    def test_post_publishes_the_stamped_review(self):
        runner = FakeRunner({"gh pr view 9 --json headRefOid": json.dumps({"headRefOid": NEW}),
                             "gh pr comment 9": "https://example.com/c/1\n"})
        self.assertEqual(review.post(cfg(), runner, 9, "changes", NEW, "Round 1.", []),
                         "https://example.com/c/1")
