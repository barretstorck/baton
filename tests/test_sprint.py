import copy
import json
import pathlib
import unittest
from baton import config, review, sprint
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
    }
    out.update(extra or {})
    return out


def snap(issues, config_=None, **kwargs):
    return sprint.snapshot(config_ or cfg(), FakeRunner(replies(issues, **kwargs)))


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
        self.assertEqual(result["default_branch"], {"name": "main", "state": "green"})
        self.assertEqual(result["dispatchable"], [5, 2, 7])
        self.assertEqual(result["free_slots"], 4)
        self.assertFalse(result["finished"])

    def test_slots_account_for_work_in_flight(self):
        result = snap([issue(1, "in-dev", "deliverable", files="z.py"),
                       issue(2, "ready", "deliverable", files="a.py"),
                       issue(3, "ready", "deliverable", files="b.py")])
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
                                                "review": "approved", "rounds": 0}])

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
        result = snap([issue(1, "ready"), issue(2, "ready"), issue(3, "ready")])
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
