import copy
import json
import pathlib
import unittest
from datetime import datetime, timezone
from baton import config, review, sprint
from baton import run as run_module
from tests.fakes import FakeRunner

OWNER = "octo"
Q = "<!-- baton:developer:question -->\n**Baton developer: question**\n\nWhich?"


def cfg(**developer):
    data = copy.deepcopy(config.DEFAULTS)
    data["developer"].update(developer)
    return config.Config(root=pathlib.Path("."), data=data)


def issue(number, status, *labels, files="", blocked="", state="OPEN", comments=(), author=OWNER):
    body = "## Context\nx\n"
    if files:
        body += f"Expected files: {files}\n"
    if blocked:
        body += f"Blocked by: {blocked}\n"
    names = ([f"status:{status}"] if status else []) + list(labels)
    return {"number": number, "title": f"Issue {number}", "state": state, "body": body,
            "author": {"login": author}, "labels": [{"name": n} for n in names],
            "comments": list(comments)}


def comment(login, body, n):
    return {"author": {"login": login}, "body": body, "url": f"https://example.com/c/{n}"}


def run(conclusion="success", status="completed", sha="aaa"):
    return {"status": status, "conclusion": conclusion, "headSha": sha, "workflowName": "test"}


def commit_reply(date="2020-01-01T00:00:00Z", sha="aaa"):
    return json.dumps({"sha": sha, "commit": {"committer": {"date": date}}})


def replies(issues, prs=(), runs=(run(),), milestones=None, extra=None):
    milestones = milestones if milestones is not None else [
        {"number": 3, "title": "Sprint 2026-10-01-1", "description": "Goal: speed\nOrder: #5, #2",
         "due_on": "2026-10-02T00:00:00Z"}]
    out = {
        "gh repo view": json.dumps({"owner": {"login": OWNER}, "name": "proj",
                                    "defaultBranchRef": {"name": "main"}}),
        "gh api repos/octo/proj/milestones": json.dumps(milestones),
        "gh issue list": json.dumps(list(issues)),
        "gh pr list": json.dumps(list(prs)),
        "gh run list": json.dumps(list(runs)),
        "gh api repos/octo/proj/commits/main": commit_reply(),
        "gh api repos/octo/proj/contents/.github/workflows?ref=": "[]",
    }
    out.update(extra or {})
    return out


def snap(issues, config_=None, **kwargs):
    return sprint.snapshot(config_ or cfg(), FakeRunner(replies(issues, **kwargs)))


class StrictReadsTest(unittest.TestCase):
    def broken(self, key, reply):
        data = replies([issue(1, "ready")])
        data[key] = reply
        return sprint.snapshot(cfg(), FakeRunner(data))

    def test_empty_replies_raise(self):
        for key, what in (("gh issue list", "issues"), ("gh pr list", "pull requests"),
                          ("gh api repos/octo/proj/milestones", "milestones"),
                          ("gh run list", "workflow runs")):
            with self.assertRaises(sprint.SnapshotError) as caught:
                self.broken(key, "")
            self.assertIn(f"could not read {what}", str(caught.exception))

    def test_invalid_json_and_non_list_raise(self):
        with self.assertRaises(sprint.SnapshotError):
            self.broken("gh issue list", "{oops")
        with self.assertRaises(sprint.SnapshotError):
            self.broken("gh pr list", "{}")

    def test_real_empty_lists_are_valid(self):
        result = snap([], prs=[])
        self.assertEqual(result["dispatchable"], [])
        self.assertEqual(result["sprint"]["number"], 3)

    def test_runner_error_propagates(self):
        class Boom:
            def __call__(self, argv, cwd=None):
                raise run_module.RunError("gh failed")
        with self.assertRaises(run_module.RunError):
            sprint.snapshot(cfg(), Boom())


class BranchStateTest(unittest.TestCase):
    def test_states(self):
        self.assertEqual(sprint.branch_state([]), "none")
        self.assertEqual(sprint.branch_state([run()]), "green")
        self.assertEqual(sprint.branch_state([run("failure")]), "red")
        self.assertEqual(sprint.branch_state([run("", "in_progress")]), "pending")

    def test_only_the_latest_commit_counts(self):
        self.assertEqual(sprint.branch_state([run(sha="new"), run("failure", sha="old")]), "green")
        self.assertEqual(sprint.branch_state([run(sha="new"), run("failure", sha="new")]), "red")


class OverlapTest(unittest.TestCase):
    def test_same_file_and_directory_prefix_overlap(self):
        self.assertTrue(sprint.overlaps(["src/a.py"], ["src/a.py", "x"]))
        self.assertTrue(sprint.overlaps(["src/"], ["src/a.py"]))
        self.assertFalse(sprint.overlaps(["src/a.py"], ["src/b.py"]))
        self.assertFalse(sprint.overlaps([], ["src/b.py"]))


class SnapshotTest(unittest.TestCase):
    def test_no_open_sprint(self):
        result = sprint.snapshot(cfg(), FakeRunner(replies([], milestones=[])))
        self.assertIsNone(result["sprint"])
        self.assertEqual(result["dispatchable"], [])

    def test_basic_shape_and_order_line(self):
        result = snap([issue(2, "ready", "deliverable", files="a.py"),
                       issue(5, "ready", "deliverable", files="b.py"),
                       issue(7, "ready", "stretch", files="c.py")], config_=cfg(max_parallel=4))
        self.assertEqual(result["sprint"]["title"], "Sprint 2026-10-01-1")
        self.assertEqual(result["default_branch"], {"name": "main", "state": "green", "grace_ends_in_seconds": None})
        self.assertEqual(result["dispatchable"], [5, 2, 7])
        self.assertEqual(result["free_slots"], 4)
        self.assertFalse(result["finished"])

    def test_slots_account_for_work_in_flight(self):
        result = snap([issue(1, "in-dev", "deliverable", files="z.py"),
                       issue(2, "ready", "deliverable", files="a.py"),
                       issue(3, "ready", "deliverable", files="b.py")], config_=cfg(max_parallel=2))
        self.assertEqual(result["free_slots"], 1)
        self.assertEqual(result["dispatchable"], [2])
        self.assertEqual(result["in_dev"], [1])

    def test_open_blockers_and_file_overlap_are_skipped(self):
        result = snap([issue(1, "in-review", "deliverable", files="a.py"),
                       issue(2, "ready", "deliverable", files="a.py"),
                       issue(3, "ready", "deliverable", files="b.py", blocked="#1"),
                       issue(4, "ready", "deliverable", files="c.py", blocked="#9"),
                       issue(5, "ready", "deliverable", files="d.py"),
                       issue(6, "ready", "deliverable", files="d.py")],
                      config_=cfg(max_parallel=5),
                      extra={"gh issue view 9": json.dumps({"state": "CLOSED"})})
        self.assertEqual(result["dispatchable"], [5, 4])
        skipped = {item["number"]: item["reason"] for item in result["held"]}
        self.assertIn("overlap", skipped[2])
        self.assertIn("#1", skipped[3])
        self.assertIn("overlap", skipped[6])

    def test_blocked_issue_with_an_answer_is_listed_as_answered(self):
        result = snap([issue(1, "blocked", "deliverable",
                             comments=[comment(OWNER, Q, 1), comment(OWNER, "Use A.", 2)]),
                       issue(2, "blocked", "deliverable", comments=[comment(OWNER, Q, 3)])])
        self.assertEqual([item["number"] for item in result["answered"]], [1])
        self.assertEqual(result["answered"][0]["answers"][0]["url"], "https://example.com/c/2")
        self.assertEqual(result["waiting_on_maintainer"], [2])

    def test_pull_requests_are_attached_to_their_issue(self):
        head = "f" * 40
        approved = {"author": {"login": OWNER}, "body": review.compose("approve", head, "ok")}
        prs = [{"number": 30, "headRefName": "issue-1-x", "isDraft": False, "mergeable": "MERGEABLE",
                "headRefOid": head, "comments": [approved],
                "closingIssuesReferences": [{"number": 1}],
                "statusCheckRollup": [{"__typename": "CheckRun", "name": "t",
                                       "status": "COMPLETED", "conclusion": "SUCCESS"}]}]
        result = snap([issue(1, "in-review", "deliverable")], prs=prs)
        self.assertEqual(result["in_review"], [{"number": 1, "pr": 30, "draft": False,
                                                "ci": "green", "mergeable": "MERGEABLE",
                                                "review": "approved", "rounds": 0,
                                                "verify": "none"}])

    def _review_entry(self, bodies):
        head = "f" * 40
        comments = [{"author": {"login": OWNER}, "body": review.compose(verdict, head, text)}
                    for verdict, text in bodies]
        prs = [{"number": 30, "headRefName": "issue-1-x", "isDraft": False, "mergeable": "MERGEABLE",
                "headRefOid": head, "comments": comments, "closingIssuesReferences": [{"number": 1}],
                "statusCheckRollup": []}]
        return snap([issue(1, "in-review", "deliverable")], prs=prs)["in_review"][0]

    def test_blocked_review_is_reported_and_is_no_round(self):
        entry = self._review_entry([("changes", "x"),
                                    ("blocked", "Cause: environment\n\n## Reason\nno network")])
        self.assertEqual((entry["review"], entry["rounds"]), ("blocked", 1))

    def test_repeated_failure_is_reported(self):
        failing = "## Failures\n- t1: caused\n"
        entry = self._review_entry([("changes", failing), ("changes", failing)])
        self.assertEqual((entry["review"], entry["rounds"]), ("repeated", 2))

    def test_lagging_check_run_on_a_finished_workflow_run_is_green(self):
        head = "f" * 40
        prs = [{"number": 30, "headRefName": "issue-1-x", "isDraft": False, "mergeable": "MERGEABLE",
                "headRefOid": head, "comments": [], "closingIssuesReferences": [{"number": 1}],
                "statusCheckRollup": [{"__typename": "CheckRun", "name": "build",
                                       "status": "IN_PROGRESS", "conclusion": "",
                                       "detailsUrl": "https://github.com/octo/proj/actions/runs/123/job/4"}]}]
        run_view = json.dumps({"status": "completed", "conclusion": "success", "workflowName": "ci",
                               "headSha": head,
                               "url": "https://github.com/octo/proj/actions/runs/123"})
        result = snap([issue(1, "in-review", "deliverable")], prs=prs,
                      extra={"gh run view 123": run_view})
        self.assertEqual(result["in_review"][0]["ci"], "green")

    def test_review_state_none_and_not_required(self):
        pr = {"number": 20, "headRefName": "issue-1-x", "headRefOid": "f" * 40, "isDraft": True,
              "mergeable": "MERGEABLE", "statusCheckRollup": [], "comments": [],
              "closingIssuesReferences": [{"number": 1}]}
        entry = snap([issue(1, "in-review")], prs=[pr])["in_review"][0]
        self.assertEqual((entry["review"], entry["rounds"]), ("none", 0))
        off = cfg()
        off.data["review"]["required"] = False
        entry = snap([issue(1, "in-review")], config_=off, prs=[pr])["in_review"][0]
        self.assertEqual(entry["review"], "not-required")

    def test_parallel_line_sets_the_slots(self):
        ms = [{"number": 3, "title": "Sprint 2026-10-01-1", "description": "Goal: x\nParallel: 1",
               "due_on": None}]
        result = snap([issue(1, "ready"), issue(2, "ready")], milestones=ms)
        self.assertEqual((result["parallel"], result["free_slots"]), (1, 1))
        self.assertEqual(result["dispatchable"], [1])

    def test_without_a_parallel_line_the_brief_sets_the_slots(self):
        result = snap([issue(1, "ready"), issue(2, "ready"), issue(3, "ready")],
                      config_=cfg(max_parallel=2))
        self.assertEqual((result["parallel"], result["free_slots"]), (2, 2))

    def test_red_default_branch_only_lets_the_repair_issue_through(self):
        result = snap([issue(1, "ready", "deliverable", files="a.py"),
                       issue(8, "ready", "deliverable", "broken-default-branch", files="ci.yml")],
                      runs=[run("failure")])
        self.assertEqual(result["default_branch"]["state"], "red")
        self.assertEqual(result["repair_issue"], 8)
        self.assertEqual(result["dispatchable"], [8])

    def test_red_default_branch_without_a_repair_issue(self):
        result = snap([issue(1, "ready", "deliverable", files="a.py")], runs=[run("failure")])
        self.assertIsNone(result["repair_issue"])
        self.assertEqual(result["dispatchable"], [])

    def test_issue_by_someone_else_is_never_dispatchable(self):
        result = snap([issue(1, "ready", "deliverable", files="a.py", author="mallory")])
        self.assertEqual(result["dispatchable"], [])
        self.assertIn("maintainer", result["held"][0]["reason"])

    def test_finished_when_everything_is_complete_or_waiting(self):
        result = snap([issue(1, "complete", "deliverable", state="CLOSED"),
                       issue(2, "blocked", "deliverable", comments=[comment(OWNER, Q, 3)]),
                       issue(3, "refinement", "deliverable", "needs-maintainer")])
        self.assertTrue(result["finished"])
        self.assertEqual(result["waiting_on_maintainer"], [2, 3])

    def test_mapped_status_labels_are_understood(self):
        config_ = cfg()
        config_.data["labels"]["map"]["status:in-dev"] = "In Progress"
        one = issue(1, None, "In Progress", "deliverable")
        result = sprint.snapshot(config_, FakeRunner(replies([one])))
        self.assertEqual(result["in_dev"], [1])

    def test_named_milestone_is_used(self):
        milestones = [{"number": 3, "title": "Sprint A", "description": "", "due_on": None},
                      {"number": 4, "title": "Sprint B", "description": "", "due_on": None}]
        runner = FakeRunner(replies([], milestones=milestones))
        self.assertEqual(sprint.snapshot(cfg(), runner, "Sprint B")["sprint"]["title"], "Sprint B")
        self.assertEqual(sprint.snapshot(cfg(), FakeRunner(replies([], milestones=milestones)))
                         ["open_sprints"], ["Sprint A", "Sprint B"])


    def test_progress_for_in_dev_and_in_review(self):
        plan = ("## Plan\nWritten against: " + "c" * 40 +
                "\n\n- [x] **1. A**\n- [ ] **2. B**\n")
        dev = issue(4, "in-dev")
        dev["body"] += plan
        pr = {"number": 40, "headRefName": "issue-4-a", "headRefOid": "d" * 40,
              "isDraft": True, "mergeable": "MERGEABLE", "statusCheckRollup": [],
              "closingIssuesReferences": [{"number": 4}],
              "comments": [{"author": {"login": OWNER}, "url": "u#issuecomment-1",
                            "body": review.compose("changes", "d" * 40,
                                                   "## Tasks\n- [ ] **1.1 Fix**\n")}]}
        found = snap([dev, issue(5, "ready")], prs=[pr])
        self.assertEqual(found["progress"]["4"],
                         {"pr": 40, "spike": False, "unplanned": False,
                          "plan": {"done": 1, "total": 2, "next": "2"},
                          "review": {"round": 1, "done": 0, "total": 1, "next": "1.1"},
                          "review_without_tasks": False})
        self.assertEqual(found["progress"]["5"]["plan"], None)
        self.assertTrue(found["progress"]["5"]["unplanned"])
        self.assertFalse(found["progress"]["4"]["unplanned"])

    def test_progress_reads_a_ref_plan(self):
        dev = issue(4, "in-dev")
        dev["body"] = ("Plan: [docs/baton/plans/4-a.md]"
                       "(https://github.com/octo/proj/commits/refs/plans/4)\n\n" + dev["body"])
        plan = ("## Plan\nWritten against: " + "c" * 40 +
                "\n\n- [x] **1. A**\n- [x] **2. B**\n- [ ] **3. C**\n")
        extra = {"git show --end-of-options refs/baton/plans/4:docs/baton/plans/4-a.md": plan}
        runner = FakeRunner(replies([dev], extra=extra))
        found = sprint.snapshot(cfg(), runner)
        self.assertEqual(found["progress"]["4"]["plan"], {"done": 2, "total": 3, "next": "3"})
        self.assertFalse(found["progress"]["4"]["unplanned"])
        self.assertIn("git fetch --quiet origin +refs/plans/4:refs/baton/plans/4", runner.lines())

    def test_resumed_ready_issue_reports_its_next_task(self):
        ready = issue(6, "ready")
        ready["body"] += ("## Plan\nWritten against: " + "c" * 40 +
                          "\n\n- [x] **1. A**\n- [x] **2. B**\n- [ ] **3. C**\n")
        found = snap([ready])
        self.assertEqual(found["progress"]["6"]["plan"]["next"], "3")

    def test_spikes_are_never_unplanned(self):
        found = snap([issue(7, "in-dev", "type:spike")])
        self.assertFalse(found["progress"]["7"]["unplanned"])
        self.assertTrue(found["progress"]["7"]["spike"])

    def test_review_without_tasks_is_flagged(self):
        dev = issue(4, "in-review")
        pr = {"number": 40, "headRefName": "issue-4-a", "headRefOid": "d" * 40,
              "isDraft": True, "mergeable": "MERGEABLE", "statusCheckRollup": [],
              "closingIssuesReferences": [{"number": 4}],
              "comments": [{"author": {"login": OWNER}, "url": "u#issuecomment-1",
                            "body": review.compose("changes", "d" * 40, "1. Fix the thing.")}]}
        found = snap([dev], prs=[pr], extra={"git ": ""})
        self.assertTrue(found["progress"]["4"]["review_without_tasks"])


class LinkedPlanSnapshotTest(unittest.TestCase):
    LINK = ("Plan: [docs/baton/plans/4-x.md]"
            "(https://github.com/o/r/blob/issue-4-x/docs/baton/plans/4-x.md)")

    def test_progress_reads_the_linked_file(self):
        one = issue(4, "in-dev")
        one["body"] = self.LINK + "\n\n" + one["body"]
        two = issue(5, "in-dev")
        two["body"] = self.LINK.replace("4-x", "5-x") + "\n\n" + two["body"]
        file_text = "## Plan\n\n- [x] **1. A**\n- [ ] **2. B**\n"
        extra = {"git show --end-of-options origin/issue-4-x:docs/baton/plans/4-x.md": file_text}
        runner = FakeRunner(replies([one, two], extra=extra))
        found = sprint.snapshot(cfg(), runner)
        self.assertEqual(found["progress"]["4"]["plan"], {"done": 1, "total": 2, "next": "2"})
        self.assertFalse(found["progress"]["4"]["unplanned"])
        self.assertEqual(sum(1 for c in runner.lines() if c == "git fetch --quiet --end-of-options origin issue-4-x"), 1)
        self.assertTrue(found["progress"]["5"]["unplanned"])


class LockTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.path = pathlib.Path(tempfile.mkdtemp()) / "sprint.lock"

    def test_second_acquire_fails_until_release(self):
        self.assertTrue(sprint.lock_acquire(self.path, "run-1", ttl_hours=6))
        self.assertFalse(sprint.lock_acquire(self.path, "run-2", ttl_hours=6))
        sprint.lock_release(self.path)
        self.assertTrue(sprint.lock_acquire(self.path, "run-2", ttl_hours=6))

    def test_stale_lock_is_taken_over(self):
        import os
        self.assertTrue(sprint.lock_acquire(self.path, "run-1", ttl_hours=6))
        old = self.path.stat().st_mtime - 7 * 3600
        os.utime(self.path, (old, old))
        self.assertTrue(sprint.lock_acquire(self.path, "run-2", ttl_hours=6))
        self.assertIn("run-2", self.path.read_text())

    def test_release_without_lock_is_harmless(self):
        sprint.lock_release(self.path)


class PrCiTests(unittest.TestCase):
    def ci(self, rollup, allow_no_ci=False):
        c = cfg()
        c.data["merge"]["allow_no_ci"] = allow_no_ci
        return sprint._pr_ci({"statusCheckRollup": rollup}, c)

    @staticmethod
    def check(conclusion, status="COMPLETED", started="2026-10-22T10:00:00Z"):
        return {"__typename": "CheckRun", "name": "t", "workflowName": "ci", "status": status,
                "conclusion": conclusion, "startedAt": started}

    def test_empty_rollup_is_pending_unless_no_ci_is_allowed(self):
        self.assertEqual(self.ci([]), "pending")
        self.assertEqual(self.ci([], allow_no_ci=True), "none")

    def test_only_a_skipped_run_is_pending(self):
        self.assertEqual(self.ci([self.check("SKIPPED")]), "pending")

    def test_skipped_then_success_is_green(self):
        rollup = [self.check("SKIPPED"), self.check("SUCCESS", started="2026-10-22T11:00:00Z")]
        self.assertEqual(self.ci(rollup), "green")

    def test_failed_run_is_red(self):
        self.assertEqual(self.ci([self.check("FAILURE")]), "red")

    def test_unfinished_run_is_pending(self):
        self.assertEqual(self.ci([self.check("", status="IN_PROGRESS")]), "pending")

    def named(self, name, conclusion, status="COMPLETED"):
        return {**self.check(conclusion, status=status), "name": name}

    def test_only_cancelled_runs_are_cancelled(self):
        self.assertEqual(self.ci([self.check("CANCELLED")]), "cancelled")
        self.assertEqual(self.ci([self.named("a", "CANCELLED"), self.named("b", "CANCELLED")]),
                         "cancelled")

    def test_cancelled_with_another_failure_is_red(self):
        self.assertEqual(self.ci([self.named("a", "CANCELLED"), self.named("b", "FAILURE")]), "red")
        self.assertEqual(self.ci([self.named("a", "CANCELLED"), self.named("b", "TIMED_OUT")]), "red")

    def test_cancelled_with_an_unfinished_run_is_pending(self):
        rollup = [self.named("a", "CANCELLED"), self.named("b", "", status="IN_PROGRESS")]
        self.assertEqual(self.ci(rollup), "pending")

    def test_cancelled_then_a_later_success_is_green(self):
        rollup = [self.check("CANCELLED"), self.check("SUCCESS", started="2026-10-22T11:00:00Z")]
        self.assertEqual(self.ci(rollup), "green")


def vcfg(command="make test", required=True):
    out = cfg()
    out.data["verify"]["command"] = command
    out.data["review"]["required"] = required
    return out


HEAD = "f" * 40


def reviewed(verify, commit=HEAD):
    return {"author": {"login": OWNER}, "body": review.compose("approve", commit, "ok", verify)}


class VerifyStateTests(unittest.TestCase):
    def state(self, comments, config_=None, owner=OWNER, runner=None):
        pr = {"isDraft": False, "statusCheckRollup": [], "headRefOid": HEAD, "comments": comments}
        return sprint.pr_state(pr, config_ or vcfg(), runner or FakeRunner({}), owner)["verify"]

    def test_none_without_a_command_or_when_reviews_are_off(self):
        self.assertEqual(self.state([reviewed("green")], vcfg("")), "none")
        self.assertEqual(self.state([reviewed("green")], vcfg(required=False)), "none")

    def test_missing_without_a_review_or_for_another_head(self):
        self.assertEqual(self.state([]), "missing")
        self.assertEqual(self.state([reviewed("green", "a" * 40)]), "missing")

    def test_marker_result_is_read(self):
        for value in ("green", "red", "timeout"):
            self.assertEqual(self.state([reviewed(value)]), value)

    def test_owner_is_looked_up_only_when_needed(self):
        runner = FakeRunner({"gh repo view --json owner": json.dumps({"owner": {"login": OWNER}})})
        self.assertEqual(self.state([reviewed("red")], owner=None, runner=runner), "red")
        self.assertEqual(self.state([reviewed("red")], vcfg(""), owner=None), "none")

    def test_in_review_entry_carries_verify(self):
        pr = {"number": 30, "headRefName": "issue-1-x", "isDraft": False, "mergeable": "MERGEABLE",
              "headRefOid": HEAD, "comments": [reviewed("timeout")],
              "closingIssuesReferences": [{"number": 1}], "statusCheckRollup": []}
        entry = snap([issue(1, "in-review")], config_=vcfg(), prs=[pr])["in_review"][0]
        self.assertEqual(entry["verify"], "timeout")


class PrStateTests(unittest.TestCase):
    check = staticmethod(PrCiTests.check)

    def state(self, rollup, draft=False, runner=None, **extra):
        pr = {"isDraft": draft, "statusCheckRollup": rollup, **extra}
        return sprint.pr_state(pr, cfg(), runner)

    def test_replaced_skipped_run_is_ignored_on_a_ready_pull_request(self):
        rollup = [self.check("SKIPPED"), self.check("SUCCESS", started="2026-10-22T11:00:00Z")]
        result = self.state(rollup)
        self.assertFalse(result["draft"])
        self.assertEqual(result["ci"], "green")
        self.assertEqual(result["problems"], [])

    def test_draft_with_only_a_skipped_check_is_pending(self):
        result = self.state([self.check("SKIPPED")], draft=True)
        self.assertTrue(result["draft"])
        self.assertEqual(result["ci"], "pending")
        self.assertTrue(any("skipped" in problem for problem in result["problems"]))

    def test_cancelled_check_shows_as_cancelled(self):
        result = self.state([self.check("CANCELLED")])
        self.assertEqual(result["ci"], "cancelled")
        self.assertTrue(result["problems"])

    def test_draft_with_a_passing_check_is_still_a_draft(self):
        result = self.state([self.check("SUCCESS")], draft=True)
        self.assertTrue(result["draft"])
        self.assertEqual(result["ci"], "green")

    def test_lagging_check_run_is_settled_from_its_workflow_run(self):
        head = "f" * 40
        rollup = [{"__typename": "CheckRun", "name": "build", "status": "IN_PROGRESS",
                   "conclusion": "",
                   "detailsUrl": "https://github.com/octo/proj/actions/runs/123/job/1"}]
        run_view = json.dumps({"status": "completed", "conclusion": "success",
                               "workflowName": "ci", "headSha": head,
                               "url": "https://github.com/octo/proj/actions/runs/123"})
        result = self.state(rollup, runner=FakeRunner({"gh run view 123": run_view}),
                            headRefOid=head)
        self.assertEqual(result["ci"], "green")


class PrStateCommandTests(unittest.TestCase):
    def test_no_number_exits_64(self):
        from baton import cli
        self.assertEqual(cli.cmd_pr_state([]), 64)

    def test_prints_draft_and_ci(self):
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        from baton import cli
        pr = {"isDraft": False, "statusCheckRollup": [PrCiTests.check("SUCCESS")]}
        runner = FakeRunner({"gh pr view 13": json.dumps(pr)})
        out = io.StringIO()
        with mock.patch.object(cli, "_require_config", return_value=cfg()), \
                mock.patch.object(cli.run, "run", runner), redirect_stdout(out):
            self.assertEqual(cli.cmd_pr_state(["13"]), 0)
        printed = json.loads(out.getvalue())
        self.assertEqual((printed["draft"], printed["ci"]), (False, "green"))

    def test_command_fetches_head_sha_so_a_lagging_run_is_green(self):
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        from baton import cli
        head = "f" * 40
        pr = {"isDraft": False, "headRefOid": head,
              "statusCheckRollup": [{"__typename": "CheckRun", "name": "build",
                                     "status": "IN_PROGRESS", "conclusion": "",
                                     "detailsUrl": "https://github.com/octo/proj/actions/runs/123/job/1"}]}
        run_view = json.dumps({"status": "completed", "conclusion": "success",
                               "workflowName": "ci", "headSha": head,
                               "url": "https://github.com/octo/proj/actions/runs/123"})
        runner = FakeRunner({
            "gh pr view 13 --json isDraft,statusCheckRollup,headRefOid": json.dumps(pr),
            "gh run view 123": run_view})
        out = io.StringIO()
        with mock.patch.object(cli, "_require_config", return_value=cfg()), \
                mock.patch.object(cli.run, "run", runner), redirect_stdout(out):
            self.assertEqual(cli.cmd_pr_state(["13"]), 0)
        self.assertEqual(json.loads(out.getvalue())["ci"], "green")


NOW = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
CI_ON_PUSH = "name: ci\non:\n  pull_request:\n  push:\n    branches: [main]\njobs: {}\n"
WORKFLOWS = "gh api repos/octo/proj/contents/.github/workflows?ref=aaa"
CI_FILE = "gh api repos/octo/proj/contents/.github/workflows/ci.yml?ref=aaa"


class DefaultBranchStateTest(unittest.TestCase):
    def state(self, runs=(), date="2026-10-22T11:59:00Z", workflows="ci.yml", text=CI_ON_PUSH):
        data = replies([], runs=runs)
        data["gh api repos/octo/proj/commits/main"] = commit_reply(date)
        data[WORKFLOWS] = json.dumps([{"name": workflows}] if workflows else [])
        data[CI_FILE] = text
        runner = FakeRunner(data)
        return sprint.default_branch_state(runner, OWNER, "proj", "main", now=NOW), runner

    def test_push_run_in_progress_is_pending(self):
        state, runner = self.state(runs=[run("", "in_progress")], date="2020-01-01T00:00:00Z")
        self.assertEqual(state, "pending")
        listing = [line for line in runner.lines() if line.startswith("gh run list")][0]
        self.assertIn("--commit aaa --event push", listing)

    def test_failed_run_is_red(self):
        self.assertEqual(self.state(runs=[run("failure")])[0], "red")

    def test_finished_run_is_green(self):
        self.assertEqual(self.state(runs=[run()])[0], "green")

    def test_no_push_workflow_is_none(self):
        self.assertEqual(self.state(text="on: pull_request\n")[0], "none")
        self.assertEqual(self.state(workflows="")[0], "none")

    def test_missing_workflows_directory_is_none(self):
        class NotFound(FakeRunner):
            def __call__(self, argv, cwd=None):
                if "contents/.github/workflows" in " ".join(argv):
                    raise run_module.RunError("gh api failed: HTTP 404: Not Found")
                return super().__call__(argv, cwd)
        data = replies([], runs=[])
        data["gh api repos/octo/proj/commits/main"] = commit_reply("2026-10-22T11:59:00Z")
        self.assertEqual(
            sprint.default_branch_state(NotFound(data), OWNER, "proj", "main", now=NOW), "none")

    def test_other_api_errors_are_not_swallowed(self):
        class Broken(FakeRunner):
            def __call__(self, argv, cwd=None):
                if "contents/.github/workflows" in " ".join(argv):
                    raise run_module.RunError("gh api failed: HTTP 500")
                return super().__call__(argv, cwd)
        data = replies([], runs=[])
        data["gh api repos/octo/proj/commits/main"] = commit_reply("2026-10-22T11:59:00Z")
        with self.assertRaises(run_module.RunError):
            sprint.default_branch_state(Broken(data), OWNER, "proj", "main", now=NOW)

    def test_no_run_on_an_old_commit_is_none(self):
        self.assertEqual(self.state(date="2026-10-22T11:50:00Z")[0], "none")

    def test_no_run_on_a_young_commit_is_pending(self):
        self.assertEqual(self.state(date="2026-10-22T11:57:00Z")[0], "pending")

    def test_unreadable_head_commit_raises(self):
        data = replies([], runs=[])
        data["gh api repos/octo/proj/commits/main"] = ""
        with self.assertRaises(sprint.SnapshotError):
            sprint.default_branch_state(FakeRunner(data), OWNER, "proj", "main", now=NOW)

    def test_snapshot_reports_pending(self):
        data = replies([issue(1, "ready")], runs=[run("", "queued")])
        result = sprint.snapshot(cfg(), FakeRunner(data))
        self.assertEqual(result["default_branch"]["state"], "pending")

    def status(self, date, runs=(), enabled=None):
        data = replies([], runs=runs)
        data["gh api repos/octo/proj/commits/main"] = commit_reply(date)
        data[WORKFLOWS] = json.dumps([{"name": "ci.yml"}])
        data[CI_FILE] = CI_ON_PUSH
        if enabled is not None:
            data["gh api repos/octo/proj/actions/permissions"] = json.dumps({"enabled": enabled})
        return sprint.default_branch_status(FakeRunner(data), OWNER, "proj", "main", now=NOW)

    def test_young_commit_without_runs_reports_the_grace_left(self):
        state, grace = self.status("2026-10-22T11:57:00Z")
        self.assertEqual((state, grace), ("pending", 120))

    def test_old_commit_has_no_grace(self):
        self.assertEqual(self.status("2026-10-22T11:50:00Z"), ("none", None))

    def test_a_run_has_no_grace(self):
        self.assertEqual(self.status("2026-10-22T11:59:00Z", runs=[run("", "queued")]),
                         ("pending", None))

    def test_disabled_actions_is_none_without_grace(self):
        self.assertEqual(self.status("2026-10-22T11:59:00Z", enabled=False), ("none", None))
        self.assertEqual(self.status("2026-10-22T11:59:00Z", enabled=True)[0], "pending")

    def test_actions_enabled_defaults_to_true(self):
        for reply in ("", "{}", "not json"):
            runner = FakeRunner({"gh api repos/octo/proj/actions/permissions": reply})
            self.assertTrue(sprint.actions_enabled(runner, OWNER, "proj"), reply)

    def test_actions_permissions_errors_other_than_404_are_raised(self):
        class Broken(FakeRunner):
            def __call__(self, argv, cwd=None):
                raise run_module.RunError("gh api failed: HTTP 500")
        with self.assertRaises(run_module.RunError):
            sprint.actions_enabled(Broken({}), OWNER, "proj")

    def test_snapshot_contains_the_grace_field(self):
        data = replies([issue(1, "ready")], runs=[])
        data["gh api repos/octo/proj/commits/main"] = commit_reply("2026-10-22T11:57:00Z")
        data[WORKFLOWS] = json.dumps([{"name": "ci.yml"}])
        data[CI_FILE] = CI_ON_PUSH
        branch = sprint.snapshot(cfg(), FakeRunner(data))["default_branch"]
        self.assertIn("grace_ends_in_seconds", branch)
        self.assertEqual(snap([issue(1, "ready")])["default_branch"]["grace_ends_in_seconds"], None)

    def test_triggers_on_push(self):
        yes = ["on: push\n", "on: [pull_request, push]\n", "on:\n  push:\n    branches: [main]\n",
               "on:\n  - push\n", "'on':\n  push:\n", "on: {push: {branches: [main]}}\n"]
        no = ["on:\n  pull_request:\n    branches: [main]\n", "on: pull_request\n",
              "on:\n  workflow_dispatch:\n  pull_request:\n    paths: [push.txt]\n", "", "name: x\n"]
        for text in yes:
            self.assertTrue(sprint.triggers_on_push(text), text)
        for text in no:
            self.assertFalse(sprint.triggers_on_push(text), text)
