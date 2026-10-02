import copy
import json
import pathlib
import unittest
from baton import config, merge
from tests.fakes import FakeRunner

OWNER = "octo"


def make_config(**overrides):
    data = copy.deepcopy(config.DEFAULTS)
    data.update(overrides)
    return config.Config(root=pathlib.Path("."), data=data)


def make_pr(**overrides):
    pr = {
        "number": 9, "state": "OPEN", "isDraft": False, "mergeable": "MERGEABLE",
        "headRefOid": "abc123", "headRefName": "issue-4-flag",
        "body": "Adds the flag.\n\nCloses #4",
        "files": [{"path": "src/tool.py"}],
        "statusCheckRollup": [{"__typename": "CheckRun", "name": "test",
                               "status": "COMPLETED", "conclusion": "SUCCESS"}],
        "closingIssuesReferences": [{"number": 4}],
        "commits": [{"oid": "abc123", "messageHeadline": "Add the flag", "messageBody": ""}],
        "comments": [], "isCrossRepository": False, "baseRefName": "main", "changedFiles": 1,
    }
    pr.update(overrides)
    return pr


def problems(**overrides):
    return merge.check(make_pr(**overrides), make_config(), OWNER, "main")


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
        self.assertEqual(merge.check(make_pr(statusCheckRollup=[]), cfg, OWNER, "main"), [])

    def test_pending_and_failed_checks_are_refused(self):
        pending = [{"__typename": "CheckRun", "name": "test", "status": "IN_PROGRESS", "conclusion": ""}]
        failed = [{"__typename": "CheckRun", "name": "test", "status": "COMPLETED", "conclusion": "FAILURE"}]
        status = [{"__typename": "StatusContext", "context": "ext", "state": "PENDING"}]
        for rollup in (pending, failed, status):
            with self.subTest(rollup=rollup):
                self.assertTrue(any("check" in p for p in problems(statusCheckRollup=rollup)))

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
        ok = [{"author": {"login": OWNER}, "body": "/baton approve abc1234"}]
        self.assertEqual(problems(files=files, comments=ok, headRefOid="abc1234def"), [])

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
                found = problems(files=files, comments=comments, headRefOid="abc1234def")
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
            "gh repo view": json.dumps({"owner": {"login": OWNER},
                                        "defaultBranchRef": {"name": "main"}}),
            "gh pr view 9": json.dumps(pr),
            "gh issue view 4 --json state": json.dumps({"state": issue_state}),
            "gh issue view 4 --json labels": json.dumps({"labels": [{"name": "status:in-review"}]}),
            "git worktree list": "worktree /repo\nbranch refs/heads/main\n\n"
                                 "worktree /wt/issue-4\nbranch refs/heads/issue-4-flag\n",
        }

    def test_refused_pr_is_not_merged(self):
        runner = FakeRunner(self.replies(make_pr(isDraft=True)))
        self.assertTrue(merge.merge(9, make_config(), runner))
        self.assertFalse(any("pr merge" in line for line in runner.lines()))

    def test_merge_pins_head_closes_issue_sets_status_and_cleans_own_branch(self):
        runner = FakeRunner(self.replies(make_pr()))
        self.assertEqual(merge.merge(9, make_config(), runner), [])
        lines = runner.lines()
        self.assertIn("gh pr merge 9 --merge --match-head-commit abc123", lines)
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
