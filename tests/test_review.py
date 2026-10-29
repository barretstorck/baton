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
            f"<!-- baton:reviewer:review verdict=changes commit={NEW} verify=missing -->"))
        self.assertIn("Fix the off-by-one.", text)

    def test_bad_input_is_refused(self):
        for args in (("maybe", NEW, "x"), ("approve", "abc1234", "x"), ("approve", NEW, "  ")):
            with self.subTest(args=args), self.assertRaises(review.ReviewError):
                review.compose(*args)


class MaintainersTest(unittest.TestCase):
    def test_second_maintainer_review_counts(self):
        comments = [rev("changes", login="second")]
        self.assertEqual(review.state(comments, [OWNER, "second"], 2)["state"], "changes")
        self.assertEqual(review.state(comments, OWNER, 2)["state"], "none")

    def test_baton_login_review_counts_in_comment_form(self):
        comments = [rev("changes", login="baton-reviewer")]
        self.assertEqual(
            review.state(comments, [OWNER, "baton-reviewer[bot]"], 2)["state"], "changes")
        self.assertEqual(review.state(comments, OWNER, 2)["state"], "none")

    def test_baton_login_reply_is_a_reply_event(self):
        events = review.history([reply("baton")], [OWNER, "baton[bot]"])
        self.assertEqual([e["kind"] for e in events], ["reply"])


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

    def test_state_with_failures_serializes_to_json(self):
        body = review.compose("changes", NEW, "Round 1.\n\n## Failures\n- some_test: caused\n")
        comment = {"author": {"login": OWNER}, "url": "u/c", "body": body}
        found = review.current(self.pr([comment]), OWNER, cfg(), FakeRunner(), "main")
        text = json.dumps(found, default=review.json_default)
        self.assertIn("some_test", text)
        self.assertEqual(json.loads(text)["review"]["failures"], ["some_test"])

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


class RefPlanTest(unittest.TestCase):
    REF_BODY = f"Plan: [{PLAN}](https://github.com/o/r/commits/refs/plans/4)\n\nBody."

    def test_a_ref_plan_excludes_no_plans_path(self):
        runner = plan_runner(DIFF + "-plan\n", f"D\t{PLAN}\n", body=self.REF_BODY)
        pr = {"number": 9, "headRefOid": NEW, "headRefName": "issue-4-x",
              "comments": [rev("approve", OLD)],
              "closingIssuesReferences": [{"number": 4}]}
        self.assertEqual(review.current(pr, OWNER, cfg(), runner, "main")["state"], "stale")
        self.assertFalse(any(":(exclude)" in line for line in runner.lines()))
        self.assertIn(f"git diff --binary base2 {NEW} -- .", runner.lines())

    def test_a_ref_plan_has_no_linked_path(self):
        runner = plan_runner(DIFF, "", body=self.REF_BODY)
        pr = {"closingIssuesReferences": [{"number": 4}]}
        self.assertIsNone(review._linked_plan_path(runner, pr, cfg()))


TASKS = "## Tasks\n- [ ] **1.1 Fix it**\n  - Check: none\n"
TASKS_NEW = ("## Tasks\n- [ ] Task 1.1: Fix it\n  <details>\n"
             "  <summary>Click to see plan details</summary>\n\n"
             "  - Problem: `a.py:1` wrong\n  - Fix: change it\n  - Check: none\n\n"
             "  </details>\n")


def changes_with_tasks(commit=NEW, ticked=False, new=False):
    body = TASKS_NEW if new else TASKS
    body = body.replace("[ ]", "[x]") if ticked else body
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


class ReviewTasksLayoutTest(unittest.TestCase):
    """The same behaviour on the older and the new task layout."""

    def test_tasks_and_progress(self):
        for new in (False, True):
            with self.subTest(new=new):
                found = review.review_tasks({"comments": [changes_with_tasks(new=new)]}, OWNER)
                self.assertEqual((found["round"], found["next"], found["total"]), (1, "1.1", 1))
                self.assertEqual(found["tasks"][0]["title"], "Fix it")
                self.assertFalse(found["tasks"][0]["done"])
                done = review.review_tasks(
                    {"comments": [changes_with_tasks(ticked=True, new=new)]}, OWNER)
                self.assertTrue(done["tasks"][0]["done"])

    def test_tick_review_ticks_only_that_box(self):
        class Patching(FakeRunner):
            sent = None

            def __call__(self, argv, cwd=None, input=None):
                if "PATCH" in argv:
                    path = argv[argv.index("-F") + 1].split("@", 1)[1]
                    self.sent = pathlib.Path(path).read_text()
                return super().__call__(argv, cwd, input)

        for new in (False, True):
            with self.subTest(new=new):
                comment = changes_with_tasks(new=new)
                runner = Patching(pr_runner([comment]).replies)
                self.assertTrue(review.tick_review(runner, 9, OWNER, "1.1"))
                self.assertEqual(runner.sent, comment["body"].replace("[ ]", "[x]", 1))

    def test_changes_post_gate_on_the_new_layout(self):
        runner = pr_runner([reply()])
        self.assertEqual(review.post(cfg(), runner, 9, "changes", NEW, TASKS_NEW, [], OWNER),
                         "https://example.com/c/1")
        with self.assertRaises(review.ReviewError):
            review.post(cfg(), runner, 9, "changes", NEW,
                        TASKS_NEW.replace("1.1", "2.1"), [], OWNER)


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


def vcfg(command="make test", **review_settings):
    data = copy.deepcopy(config.DEFAULTS)
    data["review"].update(review_settings)
    data["verify"]["command"] = command
    return config.Config(root=pathlib.Path("."), data=data)


def _comment(verdict, commit=NEW, verify=None):
    body = review.compose(verdict, commit, "Round 1.", verify or "missing")
    if verify is None:
        body = body.replace(" verify=missing", "")
    return {"author": {"login": OWNER}, "url": "u", "body": body}


class CapturingRunner(FakeRunner):
    """A runner that keeps the text given to `gh pr comment --body-file`."""

    def __init__(self, replies):
        super().__init__(replies)
        self.posted = []

    def __call__(self, argv, cwd=None, input=None):
        if argv[:3] == ["gh", "pr", "comment"] and "--body-file" in argv:
            self.posted.append(pathlib.Path(argv[argv.index("--body-file") + 1]).read_text())
        return super().__call__(argv, cwd, input)


def capturing(extra=None):
    return CapturingRunner({**pr_runner().replies, **(extra or {})})


class VerifyMarkerTest(unittest.TestCase):
    def test_round_trip_each_value(self):
        for value in review.VERIFY_STATES:
            with self.subTest(value=value):
                events = review.history([_comment("approve", verify=value)], OWNER)
                self.assertEqual(events[0]["verify"], value)

    def test_old_marker_reads_missing(self):
        old = _comment("approve")
        self.assertNotIn("verify=", old["body"])
        self.assertEqual(review.history([old], OWNER)[0]["verify"], "missing")

    def test_unknown_value_is_refused(self):
        with self.assertRaises(review.ReviewError):
            review.compose("approve", NEW, "x", "bogus")

    def test_verify_state(self):
        green = [_comment("approve", verify="green")]
        self.assertEqual(review.verify_state(cfg(), green, OWNER, NEW), "none")
        self.assertEqual(review.verify_state(vcfg(required=False), green, OWNER, NEW), "none")
        self.assertEqual(review.verify_state(vcfg(), green, OWNER, NEW), "green")
        self.assertEqual(review.verify_state(vcfg(), green, OWNER, OLD), "missing")
        self.assertEqual(review.verify_state(vcfg(), [], OWNER, NEW), "missing")
        red = [_comment("changes", verify="red")]
        self.assertEqual(review.verify_state(vcfg(), red, OWNER, NEW), "red")


class PostVerifyTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        from baton import verify
        self.verify = verify
        self.home = tempfile.mkdtemp()

    def record(self, result, commit=NEW):
        folder = self.verify.logs_dir(OWNER, "repo", 9, self.home)
        self.verify.write_result(folder, commit, result, 0 if result == "green" else 1, 3,
                                 "20260101T000000Z")

    def post(self, verdict, runner, config_=None):
        body = "Round 1." if verdict == "approve" else TASKS
        return review.post(config_ or vcfg(), runner, 9, verdict, NEW, body, [], OWNER,
                           repo="repo", home=self.home)

    def test_approve_refused_without_green(self):
        for result in (None, "red", "timeout"):
            with self.subTest(result=result):
                if result:
                    self.record(result)
                runner = capturing()
                with self.assertRaises(review.ReviewError) as caught:
                    self.post("approve", runner)
                self.assertIn(f"verification of {NEW[:7]} is {result or 'missing'}",
                              str(caught.exception))
                self.assertEqual(runner.posted, [])

    def test_approve_allowed_when_green_and_marker_says_so(self):
        self.record("green")
        runner = capturing()
        self.post("approve", runner)
        self.assertIn(" verify=green -->", runner.posted[0])

    def test_result_for_another_commit_does_not_count(self):
        self.record("green", commit=OLD)
        with self.assertRaises(review.ReviewError):
            self.post("approve", capturing())

    def test_approve_without_command_records_none(self):
        runner = capturing()
        review.post(cfg(), runner, 9, "approve", NEW, "Round 1.", [], OWNER)
        self.assertIn(" verify=none -->", runner.posted[0])

    def test_changes_allowed_with_red_and_records_it(self):
        self.record("red")
        runner = capturing()
        self.post("changes", runner)
        self.assertIn(" verify=red -->", runner.posted[0])

    def test_repo_looked_up_when_not_given(self):
        self.record("green")
        runner = capturing({"gh repo view --json name": json.dumps({"name": "repo"})})
        review.post(vcfg(), runner, 9, "approve", NEW, "Round 1.", [], OWNER, home=self.home)
        self.assertIn("gh repo view --json name", runner.lines())


BLOCKED_BODY = "Cause: default-branch\n\n## Reason\nThe default branch fails the same test.\n"


def blocked(body=BLOCKED_BODY, commit=NEW):
    return {"author": {"login": OWNER}, "url": "u/blocked",
            "body": review.compose("blocked", commit, body)}


class BlockedTest(unittest.TestCase):
    def test_compose_titles_from_the_table(self):
        text = review.compose("blocked", NEW, BLOCKED_BODY)
        self.assertTrue(text.startswith("<!-- baton:reviewer:review verdict=blocked commit="))
        self.assertIn("**Baton reviewer: review (blocked, commit bbbbbbb)**", text)

    def test_history_carries_the_cause(self):
        events = review.history([blocked(), blocked("Cause: environment\n## Reason\nx"),
                                 blocked("## Reason\nno cause"), rev("changes")], OWNER)
        self.assertEqual([e["cause"] for e in events],
                         ["default-branch", "environment", None, None])

    def test_state_blocked_then_replied(self):
        found = review.state([blocked()], OWNER, 2)
        self.assertEqual((found["state"], found["rounds"]), ("blocked", 0))
        self.assertEqual(review.state([blocked(), reply()], OWNER, 2)["state"], "replied")

    def test_blocked_is_no_round_and_does_not_end_the_count(self):
        found = review.state([rev("changes"), reply(), blocked(), reply(), rev("changes")],
                             OWNER, 3)
        self.assertEqual((found["state"], found["rounds"]), ("changes", 2))
        found = review.state([rev("changes"), blocked(), reply()], OWNER, 2)
        self.assertEqual(found["rounds"], 1)

    def test_no_tasks_after_a_blocked_review(self):
        self.assertEqual(review.review_tasks({"comments": [changes_with_tasks(), blocked()]},
                                             OWNER)["tasks"], [])
        runner = pr_runner([changes_with_tasks(), blocked()])
        with self.assertRaises(review.ReviewError):
            review.tick_review(runner, 9, OWNER, "1.1")


def failing(*ids, verdict="changes"):
    lines = "\n".join(f"- {i}: caused" for i in ids) or "none"
    return {"author": {"login": OWNER}, "url": "u/failing",
            "body": review.compose(verdict, NEW, f"Round.\n\n## Failures\n{lines}\n\n## Checked\n- x: y")}


class RepeatedTest(unittest.TestCase):
    def test_failure_ids_parse(self):
        body = ("## Findings\n- not_a_failure: caused\n\n## Failures\n"
                "- tests.test_a.A.test_x: caused\n- lint: flaky\n\n## Checked\n- other: caused\n")
        self.assertEqual(review.failure_ids(body), {"tests.test_a.A.test_x", "lint"})
        self.assertEqual(review.failure_ids("## Failures\nnone\n"), set())
        self.assertEqual(review.failure_ids("no section"), set())

    def test_history_carries_failures(self):
        events = review.history([failing("a", "b"), rev("changes")], OWNER)
        self.assertEqual([e["failures"] for e in events], [{"a", "b"}, set()])

    def test_shared_id_is_repeated(self):
        found = review.state([failing("a", "b"), reply(), failing("b", "c")], OWNER, 5)
        self.assertEqual((found["state"], found["rounds"]), ("repeated", 2))

    def test_disjoint_ids_are_changes(self):
        found = review.state([failing("a"), reply(), failing("b")], OWNER, 5)
        self.assertEqual(found["state"], "changes")

    def test_reply_after_the_second_is_replied(self):
        found = review.state([failing("a"), reply(), failing("a"), reply()], OWNER, 5)
        self.assertEqual(found["state"], "replied")

    def test_blocked_between_does_not_break_the_pair(self):
        found = review.state([failing("a"), reply(), blocked(), reply(), failing("a")], OWNER, 5)
        self.assertEqual((found["state"], found["rounds"]), ("repeated", 2))

    def test_repeated_outranks_escalate(self):
        found = review.state([failing("a"), reply(), failing("a")], OWNER, 2)
        self.assertEqual((found["state"], found["rounds"]), ("repeated", 2))

    def test_only_the_last_two_count(self):
        found = review.state([failing("a"), reply(), failing("a"), reply(), failing("b")],
                             OWNER, 5)
        self.assertEqual(found["state"], "changes")


class PostBlockedTest(unittest.TestCase):
    def post(self, body, runner=None):
        return review.post(cfg(), runner or pr_runner(), 9, "blocked", NEW, body, [], OWNER)

    def test_refused_bodies(self):
        for body in ("## Reason\nNo cause.", "Cause: network\n## Reason\nx",
                     "cause: environment\n## Reason\nx", "Cause: environment\nNo reason.",
                     BLOCKED_BODY + "\n## Tasks\n- [ ] **1.1. Fix**\n"):
            with self.subTest(body=body):
                runner = pr_runner()
                with self.assertRaises(review.ReviewError):
                    self.post(body, runner)
                self.assertFalse(any("pr comment" in line for line in runner.lines()))

    def test_allowed_with_open_plan_tasks(self):
        runner = pr_runner(closing=[4], issue_body=PostGateTest.PLAN_OPEN)
        self.assertEqual(self.post(BLOCKED_BODY, runner), "https://example.com/c/1")
        self.assertEqual(self.post("Cause: environment\n\n## Reason\nNo network.\n"),
                         "https://example.com/c/1")


REPLAN_BODY = "## Feedback\nThe plan misreads the second acceptance criterion.\n"


def replan(body=REPLAN_BODY, commit=NEW):
    return {"author": {"login": OWNER}, "url": "u/replan",
            "body": review.compose("replan", commit, body)}


def refine_reply():
    return {"author": {"login": OWNER}, "url": "u/refine",
            "body": "<!-- baton:refine:reply -->\n**Baton refine: reply**\n\nPlan rewritten."}


class ReplanTest(unittest.TestCase):
    def test_compose_titles_from_the_table(self):
        text = review.compose("replan", NEW, REPLAN_BODY)
        self.assertTrue(text.startswith("<!-- baton:reviewer:review verdict=replan commit="))
        self.assertIn("**Baton reviewer: review (replan requested, commit bbbbbbb)**", text)

    def test_history_reads_the_refiners_reply(self):
        events = review.history([replan(), refine_reply()], OWNER)
        self.assertEqual([e["kind"] for e in events], ["review", "reply"])

    def test_state_replan_and_rounds(self):
        found = review.state([replan()], OWNER, 2)
        self.assertEqual((found["state"], found["rounds"]), ("replan", 1))
        found = review.state([rev("changes"), reply(), replan()], OWNER, 2)
        self.assertEqual((found["state"], found["rounds"]), ("replan", 2))

    def test_replan_then_the_refiners_reply_is_replied(self):
        self.assertEqual(review.state([replan(), refine_reply()], OWNER, 2)["state"], "replied")

    def test_replan_outranks_escalate(self):
        found = review.state([rev("changes"), reply(), rev("changes"), reply(), replan()],
                             OWNER, 2)
        self.assertEqual(found["state"], "replan")

    def test_the_count_toward_max_rounds_restarts_after_a_replan(self):
        before = [rev("changes"), reply(), rev("changes"), reply(), replan(), refine_reply()]
        found = review.state(before + [rev("changes")], OWNER, 2)
        self.assertEqual((found["state"], found["rounds"]), ("changes", 4))
        found = review.state(before + [rev("changes"), reply(), rev("changes")], OWNER, 2)
        self.assertEqual((found["state"], found["rounds"]), ("escalate", 5))

    def test_two_replans_in_a_row_escalate(self):
        for between in ([refine_reply()], [], [blocked(), reply()]):
            with self.subTest(between=len(between)):
                found = review.state([replan(), *between, replan()], OWNER, 5)
                self.assertEqual(found["state"], "escalate")

    def test_a_replan_after_a_changes_review_is_not_two_in_a_row(self):
        found = review.state([replan(), refine_reply(), rev("changes"), reply(), replan()],
                             OWNER, 5)
        self.assertEqual(found["state"], "replan")

    def test_no_tasks_after_a_replan(self):
        self.assertEqual(review.review_tasks({"comments": [changes_with_tasks(), replan()]},
                                             OWNER)["tasks"], [])
        runner = pr_runner([changes_with_tasks(), replan()])
        with self.assertRaises(review.ReviewError):
            review.tick_review(runner, 9, OWNER, "1.1")


class PostReplanTest(unittest.TestCase):
    def post(self, body, runner=None):
        return review.post(cfg(), runner or pr_runner(), 9, "replan", NEW, body, [], OWNER)

    def test_refused_bodies(self):
        for body in ("The plan is wrong.", "## Feedback\n\n## Other\nx", "## Feedback\n",
                     REPLAN_BODY + "\n## Tasks\n- [ ] **1.1. Fix**\n"):
            with self.subTest(body=body):
                runner = pr_runner()
                with self.assertRaises(review.ReviewError):
                    self.post(body, runner)
                self.assertFalse(any("pr comment" in line for line in runner.lines()))

    def test_allowed_with_open_plan_tasks(self):
        runner = pr_runner(closing=[4], issue_body=PostGateTest.PLAN_OPEN)
        self.assertEqual(self.post(REPLAN_BODY, runner), "https://example.com/c/1")

    def test_approve_after_a_replan_needs_a_green_result_for_the_new_head(self):
        import tempfile
        from baton import verify
        home = tempfile.mkdtemp()
        folder = verify.logs_dir(OWNER, "repo", 9, home)
        verify.write_result(folder, OLD, "green", 0, 3, "20260101T000000Z")
        runner = pr_runner([replan(commit=OLD), refine_reply()])
        with self.assertRaises(review.ReviewError) as caught:
            review.post(vcfg(), runner, 9, "approve", NEW, "Looks right.", [], OWNER,
                        repo="repo", home=home)
        self.assertIn(f"verification of {NEW[:7]} is missing", str(caught.exception))
