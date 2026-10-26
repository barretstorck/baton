import copy
import json
import os
import pathlib
import tempfile
import unittest
from baton import config, merge, review
from baton.run import RunError
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

    def _lagging(self, run, runner=True, notes=None):
        url = "https://github.com/octo/repo/actions/runs/123/job/456"
        rollup = [{"__typename": "CheckRun", "name": "build", "status": "IN_PROGRESS",
                   "conclusion": "", "detailsUrl": url}]
        fake = FakeRunner({"gh run view 123": run}) if runner else None
        return merge.check(make_pr(statusCheckRollup=rollup), make_config(), OWNER, "main",
                           fake, notes)

    def test_lagging_check_on_a_successful_run_passes_with_a_note(self):
        run = json.dumps({"status": "completed", "conclusion": "success", "headSha": HEAD,
                          "url": "https://github.com/octo/repo/actions/runs/123"})
        notes = []
        self.assertEqual(self._lagging(run, notes=notes), [])
        self.assertEqual(len(notes), 1)
        self.assertIn("'build'", notes[0])
        self.assertIn("actions/runs/123", notes[0])

    def test_lagging_check_on_a_run_of_another_commit_is_not_settled(self):
        run = json.dumps({"status": "completed", "conclusion": "success",
                          "headSha": "0" * 40, "url": "u"})
        notes = []
        self.assertTrue(any("has not finished" in p for p in self._lagging(run, notes=notes)))
        self.assertEqual(notes, [])
        no_sha = json.dumps({"status": "completed", "conclusion": "success", "url": "u"})
        self.assertTrue(any("has not finished" in p for p in self._lagging(no_sha)))

    def test_lagging_check_requires_the_same_workflow_when_named(self):
        def lagging(item_workflow, run_workflow):
            url = "https://github.com/octo/repo/actions/runs/123/job/456"
            item = {"__typename": "CheckRun", "name": "build", "status": "IN_PROGRESS",
                    "conclusion": "", "detailsUrl": url}
            if item_workflow:
                item["workflowName"] = item_workflow
            run = json.dumps({"status": "completed", "conclusion": "success",
                              "headSha": HEAD, "workflowName": run_workflow, "url": "u"})
            return merge.check(make_pr(statusCheckRollup=[item]), make_config(), OWNER, "main",
                               FakeRunner({"gh run view 123": run}))
        self.assertEqual(lagging("ci", "ci"), [])
        self.assertEqual(lagging(None, "ci"), [])
        self.assertTrue(any("has not finished" in p for p in lagging("ci", "other")))

    def test_lagging_check_on_a_failed_run_is_refused(self):
        run = json.dumps({"status": "completed", "conclusion": "failure", "headSha": HEAD, "url": "u"})
        self.assertTrue(any("concluded FAILURE" in p for p in self._lagging(run)))

    def test_lagging_check_on_a_running_run_has_not_finished(self):
        run = json.dumps({"status": "in_progress", "conclusion": "", "url": "u"})
        notes = []
        self.assertTrue(any("has not finished" in p for p in self._lagging(run, notes=notes)))
        self.assertEqual(notes, [])

    def test_lagging_check_without_a_runner_or_readable_run_is_unchanged(self):
        self.assertTrue(any("has not finished" in p for p in self._lagging("", runner=False)))
        self.assertTrue(any("has not finished" in p for p in self._lagging("not json")))

        class Broken:
            def __call__(self, argv, cwd=None):
                raise RunError("gh failed")
        rollup = [{"__typename": "CheckRun", "name": "build", "status": "QUEUED",
                   "detailsUrl": "https://x/actions/runs/123/job/4"}]
        found = merge.check(make_pr(statusCheckRollup=rollup), make_config(), OWNER, "main", Broken())
        self.assertTrue(any("has not finished" in p for p in found))

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

    def _forbid(self, plan="", pr=None):
        diff = ("diff --git a/pyproject.toml b/pyproject.toml\n--- a/pyproject.toml\n"
                "+++ b/pyproject.toml\n@@ -1,0 +2 @@\n+\"src/x\" = [\"a\"]\n")
        cfg = make_config(forbid_added={"pyproject.toml": '^\\s*"src/'})
        runner = FakeRunner({
            "gh pr diff 9": diff,
            "git ls-tree --name-only refs/baton/plans/4": "docs/baton/plans/4-flag.md\n",
            "git show refs/baton/plans/4:docs/baton/plans/4-flag.md": plan})
        self.runner = runner
        return merge.check(pr or make_pr(), cfg, OWNER, "main", runner)

    def test_forbidden_added_line_is_refused(self):
        self.assertTrue(any("forbidden added line in pyproject.toml" in p
                            for p in self._forbid()))

    def test_forbidden_added_line_allowed_by_the_plan(self):
        self.assertEqual(self._forbid('- Allows: forbid_added "pyproject.toml"\n'), [])

    def test_plan_ref_is_fetched_before_reading(self):
        self._forbid()
        self.assertIn("git fetch --quiet origin +refs/plans/4:refs/baton/plans/4",
                      self.runner.lines())

    def test_branch_of_another_issue_gets_no_allowance(self):
        plan = '- Allows: forbid_added "pyproject.toml"\n'
        found = self._forbid(plan, make_pr(headRefName="issue-99-x"))
        self.assertTrue(any("forbidden added line in pyproject.toml" in p for p in found))
        self.assertTrue(any("issue-99-x" in p and "#4" in p for p in found))

    def test_two_closing_issues_get_no_allowance(self):
        plan = '- Allows: forbid_added "pyproject.toml"\n'
        found = self._forbid(plan, make_pr(closingIssuesReferences=[{"number": 4}, {"number": 5}]))
        self.assertTrue(any("forbidden added line in pyproject.toml" in p for p in found))

    def test_no_forbid_rules_does_not_fetch_the_diff(self):
        runner = FakeRunner()
        merge.check(make_pr(), make_config(), OWNER, "main", runner)
        self.assertFalse(any(line.startswith("gh pr diff") for line in runner.lines()))

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

    def test_blocked_review_is_named_in_the_problem(self):
        body = review.compose("blocked", HEAD, "Cause: environment\n\n## Reason\nno network")
        found = problems(comments=[{"author": {"login": OWNER}, "body": body}])
        self.assertTrue(any("no reviewer approval" in p and "(review state: blocked)" in p
                            for p in found), found)

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
            "git branch --list issue-4-flag": "  issue-4-flag\n",
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

    TIP = "f" * 40

    def ref_replies(self, tip=None):
        url = "https://github.com/octo/tool/commits/refs/plans/4"
        replies = self.replies(make_pr())
        replies["gh issue view 4 --json body"] = json.dumps(
            {"body": f"Plan: [{self.PLAN}]({url})\n\n## Context\nx\n"})
        replies["git ls-remote origin refs/plans/4"] = f"{tip}\trefs/plans/4\n" if tip else ""
        return replies

    def test_ref_plan_is_pinned_and_merged_in_one_call(self):
        runner = FakeRunner(self.ref_replies(self.TIP))
        self.assertEqual(merge.merge(9, make_config(), runner), [])
        lines = runner.lines()
        pin = next(i for i, l in enumerate(lines) if l.startswith("gh issue edit 4 --body-file"))
        done = next(i for i, l in enumerate(lines) if l.startswith("gh pr merge 9 --merge"))
        self.assertLess(pin, done)
        self.assertIn(f"gh pr merge 9 --merge --match-head-commit {HEAD}", lines)
        self.assertFalse(any(" rm " in l or l.startswith(("git push origin HEAD", "git worktree add"))
                             for l in lines))

    def test_missing_ref_adds_a_note_and_does_not_block(self):
        runner = FakeRunner(self.ref_replies(None))
        notes = []
        self.assertEqual(merge.merge(9, make_config(), runner, notes), [])
        self.assertTrue(any("not pinned" in n for n in notes))
        self.assertIn(f"gh pr merge 9 --merge --match-head-commit {HEAD}", runner.lines())
        self.assertFalse(any(l.startswith("gh issue edit 4 --body-file") for l in runner.lines()))

    def test_failed_push_restores_the_link_to_the_branch(self):
        class PushFails(FakeRunner):
            def __call__(self, argv, cwd=None):
                result = super().__call__(argv, cwd)
                if argv[:4] == ["gh", "issue", "edit", "4"] and "--body-file" in argv:
                    # the pin is now on the issue, so later reads see the pinned link
                    self.replies["gh issue view 4 --json body"] = pinned
                if argv[:2] == ["git", "push"]:
                    raise merge.RunError("rejected")
                return result
        pinned = self.plan_replies(HEAD)["gh issue view 4 --json body"]
        runner = PushFails(self.plan_replies("issue-4-flag"))
        with self.assertRaises(merge.RunError):
            merge.merge(9, make_config(), runner)
        edits = [c for c in runner.calls if c[:4] == ["gh", "issue", "edit", "4"]]
        self.assertEqual(len(edits), 2)
        self.assertFalse(any(line.startswith("gh pr merge") for line in runner.lines()))
        self.assertIn("git worktree remove --force", " ".join(runner.lines()))

    def test_unsafe_link_is_refused_before_any_git_command_or_removal(self):
        for ref, path in [("--upload-pack=x", self.PLAN), ("issue-4-flag", "../../x.md"),
                          ("issue-4-flag", "/etc/x.md")]:
            replies = self.replies(make_pr())
            replies["gh issue view 4 --json body"] = json.dumps({"body": (
                f"Plan: [{path}](https://github.com/octo/tool/blob/{ref}/{path})\n\n## Context\nx\n")})
            runner = FakeRunner(replies)
            with self.subTest(ref=ref, path=path), self.assertRaises(merge.checklist.TaskError):
                merge.merge(9, make_config(), runner)
            lines = runner.lines()
            self.assertFalse(any(l.startswith(("git fetch", "git show", "git rm", "git worktree add",
                                               "git push", "gh pr merge", "gh issue edit"))
                                 for l in lines))

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
        self.assertLess(self.comment_index(lines, 4), lines.index("gh issue close 4"))
        self.assertIn("gh issue edit 4 --add-label status:verification --remove-label status:in-review", lines)
        self.assertIn("git worktree remove --force /wt/issue-4", lines)
        self.assertIn("git branch -D issue-4-flag", lines)
        self.assertIn("git push origin --delete issue-4-flag", lines)
        self.assertFalse(any("/repo" in line and "remove" in line for line in lines))

    def test_missing_local_branch_is_not_deleted(self):
        replies = self.replies(make_pr())
        replies["git branch --list issue-4-flag"] = ""
        runner = FakeRunner(replies)
        self.assertEqual(merge.merge(9, make_config(), runner), [])
        self.assertFalse(any(line.startswith("git branch -D") for line in runner.lines()))
        self.assertIn("git push origin --delete issue-4-flag", runner.lines())

    def test_failed_delete_of_an_existing_branch_propagates(self):
        inner = FakeRunner(self.replies(make_pr()))

        def runner(argv, cwd=None, input=None):
            if argv[:3] == ["git", "branch", "-D"]:
                raise RunError("git branch -D failed")
            return inner(argv, cwd, input)

        with self.assertRaises(merge.PostMergeError) as raised:
            merge.merge(9, make_config(), runner)
        self.assertIsInstance(raised.exception.__cause__, RunError)

    @staticmethod
    def comment_index(lines, issue):
        return next(i for i, line in enumerate(lines)
                    if line.startswith(f"gh issue comment {issue} --body-file"))

    def test_close_posts_an_orchestrator_marker_first(self):
        runner = FakeRunner(self.replies(make_pr()))
        merge.merge(9, make_config(), runner)
        bodies = [call for call in runner.calls if call[:4] == ["gh", "issue", "comment", "4"]]
        self.assertEqual(len(bodies), 1)

    def test_already_closed_issue_is_not_closed_again(self):
        runner = FakeRunner(self.replies(make_pr(), issue_state="CLOSED"))
        merge.merge(9, make_config(), runner)
        self.assertNotIn("gh issue close 4", runner.lines())
        self.assertFalse(any(line.startswith("gh issue comment 4") for line in runner.lines()))

    def test_already_merged_pr_finishes_the_follow_up_steps(self):
        runner = FakeRunner(self.replies(make_pr(state="MERGED", mergeable="UNKNOWN")))
        self.assertEqual(merge.merge(9, make_config(), runner), [])
        lines = runner.lines()
        self.assertFalse(any(line.startswith("gh pr merge") for line in lines))
        self.assertIn("gh issue close 4", lines)
        self.assertLess(self.comment_index(lines, 4), lines.index("gh issue close 4"))
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

    def secret_tree(self, kind):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for name in (".env", ".env.example", ".env.sample", ".env.template", "notes.txt"):
            open(os.path.join(tmp.name, name), "w").close()
        replies = self.replies(make_pr())
        if kind == "branch":
            replies["git worktree list"] = replies["git worktree list"].replace("/wt/issue-4", tmp.name)
            replies[f"git -C {tmp.name} status --porcelain"] = " M src/tool.py\n"
        else:
            replies["git worktree list"] += f"\nworktree {tmp.name}\nHEAD {HEAD}\ndetached\n\n"
            replies[f"git -C {tmp.name} status --porcelain"] = "?? scratch.txt\n"
        return tmp.name, replies

    def test_kept_branch_worktree_note_names_the_secret_file(self):
        path, replies = self.secret_tree("branch")
        notes = []
        merge.merge(9, make_config(), FakeRunner(replies), notes)
        note = next(n for n in notes if path in n)
        self.assertIn("it holds .env; delete it by hand", note)
        self.assertNotIn(".env.example", note)

    def test_kept_task_worktree_note_names_the_secret_file(self):
        path, replies = self.secret_tree("task")
        notes = []
        merge.merge(9, make_config(), FakeRunner(replies), notes)
        note = next(n for n in notes if path in n)
        self.assertIn("it holds .env; delete it by hand", note)

    def test_kept_worktree_without_a_secret_file_has_no_mention(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, ".env.example"), "w").close()
            self.assertEqual(merge._secret_files(tmp), [])
            self.assertEqual(merge._secret_files(tmp + "/missing"), [])
        notes = []
        replies = self.replies(make_pr())
        replies["git -C /wt/issue-4 status --porcelain"] = " M src/tool.py\n"
        merge.merge(9, make_config(), FakeRunner(replies), notes)
        self.assertFalse(any("holds" in n for n in notes))

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


def verify_config():
    cfg = make_config()
    cfg.data["verify"] = {"command": "make test", "timeout_minutes": 10}
    return cfg


def verified(state, commit=HEAD):
    body = review.compose("approve", commit, "Round 1.", state)
    return [{"author": {"login": OWNER}, "body": body}]


class VerifyRecordTest(unittest.TestCase):
    def found(self, comments, cfg=None):
        return merge.check(make_pr(comments=comments), cfg or verify_config(), OWNER, "main",
                           FakeRunner())

    def test_not_green_is_refused(self):
        for state in ("missing", "red", "timeout"):
            with self.subTest(state=state):
                found = self.found(verified(state))
                self.assertTrue(any(f"verification of {HEAD[:7]} is {state} in the latest review; "
                                    "the reviewer runs baton verify" in p for p in found), found)

    def test_green_is_allowed(self):
        self.assertEqual(self.found(verified("green")), [])

    def test_no_command_changes_nothing(self):
        self.assertEqual(self.found(verified("red"), make_config()), [])

    def test_approval_of_an_older_commit_is_missing(self):
        old = "d" * 40
        runner = FakeRunner({f"git merge-base origin/main {old}": "b1\n",
                             f"git merge-base origin/main {HEAD}": "b2\n",
                             f"git diff --binary b1 {old}": "+a\n",
                             f"git diff --binary b2 {HEAD}": "+a\n"})
        pr = make_pr(comments=verified("green", old))
        found = merge.check(pr, verify_config(), OWNER, "main", runner)
        self.assertTrue(any(f"verification of {HEAD[:7]} is missing" in p for p in found), found)


class VerifyLogsTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, ignore_errors=True))
        old = os.environ.get("BATON_HOME")
        os.environ["BATON_HOME"] = self.home
        self.addCleanup(lambda: os.environ.__setitem__("BATON_HOME", old) if old is not None
                        else os.environ.pop("BATON_HOME", None))
        self.folder = pathlib.Path(self.home) / "octo-tool" / "logs" / "pr-9"
        self.folder.mkdir(parents=True)
        (self.folder / "verify-abc1234-x.log").write_text("out")

    def run_merge(self, pr, notes=None):
        helper = MergeTest()
        runner = FakeRunner(helper.replies(pr))
        return merge.merge(9, make_config(), runner, notes)

    def test_logs_removed_after_a_merge(self):
        self.assertEqual(self.run_merge(make_pr()), [])
        self.assertFalse(self.folder.exists())

    def test_logs_removed_on_a_rerun_of_a_merged_pull_request(self):
        self.run_merge(make_pr(state="MERGED"))
        self.assertFalse(self.folder.exists())

    def test_logs_kept_when_the_merge_is_refused(self):
        self.assertTrue(self.run_merge(make_pr(isDraft=True)))
        self.assertTrue(self.folder.exists())

    def test_removal_failure_is_a_note_not_a_failure(self):
        from unittest import mock
        notes = []
        with mock.patch("baton.verify.remove_logs", side_effect=OSError("busy")):
            self.assertEqual(self.run_merge(make_pr(), notes), [])
        self.assertTrue(any("verification logs" in note and "busy" in note for note in notes))


class CliCleanupTest(unittest.TestCase):
    def run_cli(self, branch_list_reply):
        import contextlib
        import io
        from unittest import mock
        from baton import cli

        def fake_run(argv, cwd=None, input=None):
            if argv[:3] == ["git", "branch", "-D"]:
                raise RunError("git branch -D failed: boom")
            return branch_list_reply

        def fake_merge(number, cfg, runner, notes):
            if runner(["git", "branch", "--list", "b"]).strip():
                runner(["git", "branch", "-D", "b"])
            return []

        err = io.StringIO()
        with mock.patch.object(cli, "_require_config", return_value=make_config()), \
                mock.patch.object(cli.run, "run", fake_run), \
                mock.patch.object(cli.merge, "merge", fake_merge), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = cli.cmd_merge_pr(["9"])
        return code, err.getvalue()

    def test_real_delete_failure_of_an_existing_branch_is_reported(self):
        code, err = self.run_cli("  b\n")
        self.assertEqual(code, 0)
        self.assertIn("cleanup skipped: git branch -D failed", err)

    def test_missing_branch_prints_nothing(self):
        code, err = self.run_cli("")
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
