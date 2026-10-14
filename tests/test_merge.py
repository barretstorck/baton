import copy
import json
import pathlib
import unittest
from baton import config, merge, review
from tests.fakes import FakeRunner

OWNER = "octo"
HEAD = "abc1234" + "0" * 33


def approval(commit=HEAD):
    return {"author": {"login": OWNER}, "body": review.compose("approve", commit, "Round 1.")}


def make_config(**overrides):
    data = copy.deepcopy(config.DEFAULTS)
    data.update(overrides)
    return config.Config(root=pathlib.Path("."), data=data)


def make_pr(**overrides):
    pr = {
        "number": 9, "state": "OPEN", "isDraft": False, "mergeable": "MERGEABLE",
        "headRefOid": HEAD, "headRefName": "issue-4-flag",
        "body": "Adds the flag.\n\nCloses #4",
        "files": [{"path": "src/tool.py"}],
        "statusCheckRollup": [{"__typename": "CheckRun", "name": "test",
                               "status": "COMPLETED", "conclusion": "SUCCESS"}],
        "closingIssuesReferences": [{"number": 4}],
        "commits": [{"oid": HEAD, "messageHeadline": "Add the flag", "messageBody": ""}],
        "comments": [approval()], "isCrossRepository": False, "baseRefName": "main", "changedFiles": 1,
    }
    pr.update(overrides)
    return pr


def problems(**overrides):
    return merge.check(make_pr(**overrides), make_config(), OWNER, "main", FakeRunner())


class CheckTest(unittest.TestCase):
    def test_good_pr_has_no_problems(self):
        self.assertEqual(problems(), [])

    def test_draft_closed_and_conflicted_are_refused(self):
        self.assertTrue(any("draft" in p for p in problems(isDraft=True)))
        self.assertTrue(any("open" in p for p in problems(state="MERGED")))
        self.assertTrue(any("mergeable" in p for p in problems(mergeable="CONFLICTING")))
        self.assertTrue(any("mergeable" in p for p in problems(mergeable="UNKNOWN")))

    def test_no_checks_registered_is_refused(self):
        self.assertTrue(any("no CI checks" in p for p in problems(statusCheckRollup=[])))

    def test_no_checks_allowed_when_configured(self):
        cfg = make_config(merge={"allow_no_ci": True})
        self.assertEqual(merge.check(make_pr(statusCheckRollup=[]), cfg, OWNER, "main", FakeRunner()), [])

    def test_pending_and_failed_checks_are_refused(self):
        pending = [{"__typename": "CheckRun", "name": "test", "status": "IN_PROGRESS", "conclusion": ""}]
        failed = [{"__typename": "CheckRun", "name": "test", "status": "COMPLETED", "conclusion": "FAILURE"}]
        status = [{"__typename": "StatusContext", "context": "ext", "state": "PENDING"}]
        for rollup in (pending, failed, status):
            with self.subTest(rollup=rollup):
                self.assertTrue(any("check" in p for p in problems(statusCheckRollup=rollup)))

    def test_only_the_latest_run_of_each_check_counts(self):
        def check(conclusion, started):
            return {"__typename": "CheckRun", "name": "test", "workflowName": "test",
                    "status": "COMPLETED", "conclusion": conclusion, "startedAt": started}
        cancelled_then_passed = [check("CANCELLED", "2026-10-02T10:00:00Z"),
                                 check("SUCCESS", "2026-10-02T11:00:00Z")]
        self.assertEqual(problems(statusCheckRollup=cancelled_then_passed), [])
        passed_then_failed = [check("SUCCESS", "2026-10-02T10:00:00Z"),
                              check("FAILURE", "2026-10-02T11:00:00Z")]
        self.assertTrue(any("FAILURE" in p for p in problems(statusCheckRollup=passed_then_failed)))

    def test_skipped_draft_run_then_success_passes(self):
        rollup = [{"__typename": "CheckRun", "name": "test", "workflowName": "test", "status": "COMPLETED",
                   "conclusion": "SKIPPED", "startedAt": "2026-10-02T10:00:00Z"},
                  {"__typename": "CheckRun", "name": "test", "workflowName": "test", "status": "COMPLETED",
                   "conclusion": "SUCCESS", "startedAt": "2026-10-02T11:00:00Z"}]
        self.assertEqual(problems(statusCheckRollup=rollup), [])

    def test_all_skipped_checks_are_refused(self):
        rollup = [{"__typename": "CheckRun", "name": "test", "status": "COMPLETED", "conclusion": "SKIPPED"}]
        self.assertTrue(any("skipped" in p for p in problems(statusCheckRollup=rollup)))

    def test_skipped_check_and_success_status_pass(self):
        rollup = [{"__typename": "CheckRun", "name": "a", "status": "COMPLETED", "conclusion": "SKIPPED"},
                  {"__typename": "StatusContext", "context": "ext", "state": "SUCCESS"}]
        self.assertEqual(problems(statusCheckRollup=rollup), [])

    def test_must_close_exactly_one_issue(self):
        self.assertTrue(any("exactly one" in p for p in problems(closingIssuesReferences=[])))
        self.assertTrue(any("exactly one" in p for p in problems(
            closingIssuesReferences=[{"number": 4}, {"number": 5}])))

    def test_stray_closing_keyword_is_refused(self):
        body = "Closes #4\n\nDeferred rather than a fix: #55"
        self.assertTrue(any("#55" in p for p in problems(body=body)))

    def test_protected_path_needs_approval(self):
        files = [{"path": ".github/workflows/ci.yml"}]
        self.assertTrue(any("protected" in p for p in problems(files=files)))

    def test_owner_approval_naming_the_head_commit_unlocks(self):
        files = [{"path": ".github/workflows/ci.yml"}]
        ok = [approval(), {"author": {"login": OWNER}, "body": "/baton approve abc1234"}]
        self.assertEqual(problems(files=files, comments=ok), [])

    def test_other_approvals_do_not_unlock(self):
        files = [{"path": ".baton/brief.md"}]
        cases = {
            "no sha": [{"author": {"login": OWNER}, "body": "/baton approve"}],
            "old sha": [{"author": {"login": OWNER}, "body": "/baton approve 9999999"}],
            "short sha": [{"author": {"login": OWNER}, "body": "/baton approve abc"}],
            "marked": [{"author": {"login": OWNER},
                        "body": "<!-- baton:developer:status -->\n/baton approve abc1234"}],
            "foreign": [{"author": {"login": "mallory"}, "body": "/baton approve abc1234"}],
        }
        for name, comments in cases.items():
            with self.subTest(name=name):
                found = problems(files=files, comments=[approval()] + comments)
                self.assertTrue(any("protected" in p for p in found))

    def test_review_is_required(self):
        self.assertTrue(any("reviewer approval" in p for p in problems(comments=[])))
        changes = [{"author": {"login": OWNER}, "body": review.compose("changes", HEAD, "x")}]
        self.assertTrue(any("reviewer approval" in p for p in problems(comments=changes)))

    def test_review_not_required_when_configured(self):
        cfg = make_config(review={"required": False, "max_rounds": 2})
        self.assertEqual(merge.check(make_pr(comments=[]), cfg, OWNER, "main", FakeRunner()), [])

    def test_stale_review_is_refused(self):
        old = "d" * 40
        runner = FakeRunner({f"git merge-base origin/main {old}": "b1\n",
                             f"git merge-base origin/main {HEAD}": "b2\n",
                             f"git diff --binary b1 {old}": "+a\n", f"git diff --binary b2 {HEAD}": "+a\n+b\n"})
        pr = make_pr(comments=[approval(old)])
        found = merge.check(pr, make_config(), OWNER, "main", runner)
        self.assertTrue(any("stale" in p for p in found))

    def test_protected_approval_of_an_earlier_identical_change_unlocks(self):
        old = "e" * 40
        files = [{"path": ".github/workflows/ci.yml"}]
        runner = FakeRunner({f"git merge-base origin/main {old[:7]}": "b1\n",
                             f"git merge-base origin/main {HEAD}": "b2\n",
                             f"git diff --binary b1 {old[:7]} -- .github/workflows/ci.yml": "+on: push\n",
                             f"git diff --binary b2 {HEAD} -- .github/workflows/ci.yml": "+on: push\n"})
        comments = [approval(), {"author": {"login": OWNER}, "body": f"/baton approve {old[:7]}"}]
        pr = make_pr(files=files, comments=comments)
        self.assertEqual(merge.check(pr, make_config(), OWNER, "main", runner), [])
        self.assertIn("git fetch --quiet origin main issue-4-flag", runner.lines())

    def test_protected_approval_of_a_different_change_does_not_unlock(self):
        old = "e" * 40
        files = [{"path": ".github/workflows/ci.yml"}]
        runner = FakeRunner({f"git merge-base origin/main {old[:7]}": "b1\n",
                             f"git merge-base origin/main {HEAD}": "b2\n",
                             f"git diff --binary b1 {old[:7]} -- .github/workflows/ci.yml": "+on: push\n",
                             f"git diff --binary b2 {HEAD} -- .github/workflows/ci.yml": "+on: [push]\n"})
        comments = [approval(), {"author": {"login": OWNER}, "body": f"/baton approve {old[:7]}"}]
        found = merge.check(make_pr(files=files, comments=comments), make_config(), OWNER, "main", runner)
        self.assertTrue(any("protected" in p for p in found))

    def test_fork_and_wrong_base_are_refused(self):
        self.assertTrue(any("fork" in p for p in problems(isCrossRepository=True)))
        self.assertTrue(any("default branch" in p for p in problems(baseRefName="release")))

    def test_closing_keyword_in_a_commit_message_is_refused(self):
        commits = [{"oid": "abc123", "messageHeadline": "Tidy", "messageBody": "fixes #99"}]
        self.assertTrue(any("#99" in p for p in problems(commits=commits)))

    def test_truncated_file_list_is_refused(self):
        self.assertTrue(any("file list" in p for p in problems(changedFiles=150)))


class MergeTest(unittest.TestCase):
    def replies(self, pr, issue_state="OPEN"):
        return {
            "gh repo view": json.dumps({"owner": {"login": OWNER}, "name": "tool",
                                        "defaultBranchRef": {"name": "main"}}),
            "gh pr view 9": json.dumps(pr),
            "gh issue view 4 --json state": json.dumps({"state": issue_state}),
            "gh issue view 4 --json body": json.dumps({"body": "## Context\nx\n"}),
            "gh issue view 4 --json labels": json.dumps({"labels": [{"name": "status:in-review"}]}),
            "git worktree list": "worktree /repo\nbranch refs/heads/main\n\n"
                                 "worktree /wt/issue-4\nbranch refs/heads/issue-4-flag\n",
        }

    PLAN = "docs/baton/plans/4-flag.md"

    def plan_replies(self, ref, pr=None):
        url = f"https://github.com/octo/tool/blob/{ref}/{self.PLAN}"
        replies = self.replies(pr or make_pr())
        replies["gh issue view 4 --json body"] = json.dumps(
            {"body": f"Plan: [{self.PLAN}]({url})\n\n## Context\nx\n"})
        return replies

    def test_unpinned_plan_is_pinned_removed_and_pushed_before_any_merge(self):
        runner = FakeRunner(self.plan_replies("issue-4-flag"))
        with self.assertRaises(merge.PlanRetired) as raised:
            merge.merge(9, make_config(), runner)
        self.assertIn("run `baton merge-pr 9` again", str(raised.exception))
        lines = runner.lines()
        self.assertFalse(any(line.startswith("gh pr merge") for line in lines))
        pin = next(i for i, l in enumerate(lines) if l.startswith("gh issue edit 4 --body-file"))
        rm = next(i for i, l in enumerate(lines) if f"git rm -q -- {self.PLAN}" in l)
        commit = next(i for i, l in enumerate(lines) if "Remove the plan of #4" in l)
        push = next(i for i, l in enumerate(lines) if l == "git push origin HEAD:issue-4-flag")
        self.assertLess(pin, rm)
        self.assertLess(rm, commit)
        self.assertLess(commit, push)
        add = next(l for l in lines if l.startswith("git worktree add --detach"))
        self.assertTrue(add.endswith(HEAD))

    def test_pinned_link_with_the_file_gone_merges_as_before(self):
        runner = FakeRunner(self.plan_replies(HEAD))
        self.assertEqual(merge.merge(9, make_config(), runner), [])
        self.assertIn(f"gh pr merge 9 --merge --match-head-commit {HEAD}", runner.lines())
        self.assertFalse(any(" rm " in line for line in runner.lines()))

    def test_unpinned_link_without_the_file_on_the_head_merges_as_before(self):
        class Missing(FakeRunner):
            def __call__(self, argv, cwd=None):
                if argv[:2] == ["git", "show"]:
                    raise merge.RunError("missing")
                return super().__call__(argv, cwd)
        runner = Missing(self.plan_replies("issue-4-flag"))
        self.assertEqual(merge.merge(9, make_config(), runner), [])
        self.assertFalse(any(" rm " in line for line in runner.lines()))

    def test_refused_pr_does_not_retire_the_plan(self):
        runner = FakeRunner(self.plan_replies("issue-4-flag", pr=make_pr(isDraft=True)))
        self.assertTrue(merge.merge(9, make_config(), runner))
        self.assertFalse(any(" rm " in line for line in runner.lines()))

    def test_refused_pr_is_not_merged(self):
        runner = FakeRunner(self.replies(make_pr(isDraft=True)))
        self.assertTrue(merge.merge(9, make_config(), runner))
        self.assertFalse(any("pr merge" in line for line in runner.lines()))

    def test_merge_pins_head_closes_issue_sets_status_and_cleans_own_branch(self):
        runner = FakeRunner(self.replies(make_pr()))
        self.assertEqual(merge.merge(9, make_config(), runner), [])
        lines = runner.lines()
        self.assertIn(f"gh pr merge 9 --merge --match-head-commit {HEAD}", lines)
        self.assertIn("gh issue close 4", lines)
        self.assertIn("gh issue edit 4 --add-label status:verification --remove-label status:in-review", lines)
        self.assertIn("git worktree remove --force /wt/issue-4", lines)
        self.assertIn("git branch -D issue-4-flag", lines)
        self.assertIn("git push origin --delete issue-4-flag", lines)
        self.assertFalse(any("/repo" in line and "remove" in line for line in lines))

    def test_already_closed_issue_is_not_closed_again(self):
        runner = FakeRunner(self.replies(make_pr(), issue_state="CLOSED"))
        merge.merge(9, make_config(), runner)
        self.assertNotIn("gh issue close 4", runner.lines())

    def test_already_merged_pr_finishes_the_follow_up_steps(self):
        runner = FakeRunner(self.replies(make_pr(state="MERGED", mergeable="UNKNOWN")))
        self.assertEqual(merge.merge(9, make_config(), runner), [])
        lines = runner.lines()
        self.assertFalse(any(line.startswith("gh pr merge") for line in lines))
        self.assertIn("gh issue close 4", lines)
        self.assertIn("git branch -D issue-4-flag", lines)

    def test_locked_clean_worktree_is_removed(self):
        replies = self.replies(make_pr())
        replies["git worktree list"] += "locked agent\n"
        runner = FakeRunner(replies)
        merge.merge(9, make_config(), runner)
        self.assertIn("git worktree remove --force --force /wt/issue-4", runner.lines())

    def test_dirty_worktree_is_kept_with_a_note(self):
        replies = self.replies(make_pr())
        replies["git -C /wt/issue-4 status --porcelain"] = " M src/tool.py\n"
        runner, notes = FakeRunner(replies), []
        self.assertEqual(merge.merge(9, make_config(), runner, notes), [])
        self.assertFalse(any("worktree remove" in line for line in runner.lines()))
        self.assertFalse(any(line.startswith("git branch -D") for line in runner.lines()))
        self.assertIn("git push origin --delete issue-4-flag", runner.lines())
        self.assertTrue(any("/wt/issue-4" in note for note in notes))

    TASK_TREES = (f"worktree /wt/agent-a\nHEAD {HEAD}\ndetached\nlocked agent\n\n"
                  "worktree /wt/agent-b\nHEAD 3333\ndetached\n\n"
                  "worktree /wt/orchestrator\nHEAD 4444\ndetached\n\n")

    def task_runner(self, dirty=False):
        replies = self.replies(make_pr())
        replies["git worktree list"] += "\n" + self.TASK_TREES
        if dirty:
            replies["git -C /wt/agent-a status --porcelain"] = "?? scratch.txt\n"
        fake = FakeRunner(replies)
        return fake, fake

    def test_detached_task_worktrees_on_the_branch_are_removed(self):
        runner, fake = self.task_runner()
        self.assertEqual(merge.merge(9, make_config(), runner), [])
        lines = fake.lines()
        self.assertIn("git worktree remove --force --force /wt/agent-a", lines)
        for other in ("/wt/agent-b", "/wt/orchestrator"):
            self.assertFalse(any("remove" in line and other in line for line in lines), other)

    def test_dirty_task_worktree_is_kept_with_a_note(self):
        runner, fake = self.task_runner(dirty=True)
        notes = []
        self.assertEqual(merge.merge(9, make_config(), runner, notes), [])
        self.assertFalse(any("remove" in line and "/wt/agent-a" in line for line in fake.lines()))
        self.assertTrue(any("/wt/agent-a" in note for note in notes))

    def test_failure_after_merge_says_the_merge_happened(self):
        class Failing(FakeRunner):
            def __call__(self, argv, cwd=None):
                if argv[:3] == ["gh", "issue", "close"]:
                    raise RuntimeError("network down")
                return super().__call__(argv, cwd)

        runner = Failing(self.replies(make_pr()))
        with self.assertRaises(merge.PostMergeError) as caught:
            merge.merge(9, make_config(), runner)
        self.assertIn("merged", str(caught.exception))
        self.assertIn("baton merge-pr 9", str(caught.exception))
