import copy
import pathlib
import unittest
from baton import config, merge, review
from baton.forge.gitlab import GitLab
from tests.fakes import FakeRunner, FakeTransport

OWNER = "octo"
HEAD = "abc1234" + "0" * 33
OLD = "d" * 40
PROJ = "/api/v4/projects/o%2Fr"
JOB = {"name": "test", "stage": "test", "status": "success", "web_url": "u",
       "started_at": "t"}


def make_config():
    return config.Config(root=pathlib.Path("."), data=copy.deepcopy(config.DEFAULTS))


def served(approved=HEAD, pipeline="success", jobs=(JOB,), detailed="mergeable",
           approvals=False):
    """A GitLab backend serving merge request 9 on a fake transport."""
    notes = [{"id": 2, "author": {"username": "u"}, "body": "hi",
              "created_at": "2026-01-01T00:00:00Z"}]
    if approvals:
        notes.append({"id": 3, "system": True, "body": "approved this merge request",
                      "author": {"username": OWNER}, "created_at": "2026-01-01T00:00:00Z"})
    else:
        notes.append({"id": 1, "author": {"username": OWNER},
                      "body": review.compose("approve", approved, "Round 1."),
                      "created_at": "2026-01-01T00:00:00Z"})
    mr = {"iid": 9, "title": "T", "description": "Closes #4", "state": "opened",
          "draft": False, "has_conflicts": False, "detailed_merge_status": detailed,
          "source_branch": "issue-4-x", "sha": HEAD, "target_branch": "main",
          "source_project_id": 1, "target_project_id": 1, "changes_count": "1",
          "author": {"username": "u"}, "web_url": "https://g.example/o/r/-/merge_requests/9",
          "head_pipeline": {"id": 5, "sha": HEAD, "status": pipeline}}
    return GitLab("https://g.example", "o", "r", token="t", transport=FakeTransport({
        f"GET {PROJ}/merge_requests/9/notes": (200, notes),
        f"GET {PROJ}/merge_requests/9/diffs": (200, [
            {"old_path": "a", "new_path": "src/tool.py", "diff": "@@ -0,0 +1 @@\n+x\n"}]),
        f"GET {PROJ}/merge_requests/9/commits": (200, [
            {"id": HEAD, "title": "Add x", "message": "Add x"}]),
        f"GET {PROJ}/merge_requests/9/closes_issues": (200, [{"iid": 4}]),
        f"GET {PROJ}/pipelines/5/jobs": (200, list(jobs)),
        f"GET {PROJ}/pipelines/5/bridges": (200, []),
        f"GET {PROJ}/merge_requests/9": (200, mr)}))


def problems(forge, runner=None):
    pr = forge.pr_view(9, merge.PR_FIELDS)
    return merge.check(pr, make_config(), OWNER, "main", runner or FakeRunner())


class GitLabMergeCheckTest(unittest.TestCase):
    def test_approved_head_has_no_problems(self):
        self.assertEqual(problems(served()), [])

    def test_native_approval_without_marker_is_refused(self):
        found = problems(served(approvals=True))
        self.assertTrue(any("no reviewer approval" in p for p in found), found)

    def test_stale_marker_is_refused(self):
        runner = FakeRunner({f"git merge-base origin/main {OLD}": "b1\n",
                             f"git merge-base origin/main {HEAD}": "b2\n",
                             f"git diff --binary b1 {OLD}": "+a\n",
                             f"git diff --binary b2 {HEAD}": "+a\n+b\n"})
        found = problems(served(approved=OLD), runner)
        self.assertTrue(any("stale" in p or "no reviewer approval" in p for p in found), found)

    def test_running_pipeline_is_not_finished(self):
        found = problems(served(pipeline="running", jobs=({**JOB, "status": "running"},)))
        self.assertTrue(any("has not finished" in p for p in found), found)

    def test_failed_job_blocks(self):
        found = problems(served(pipeline="failed", jobs=({**JOB, "status": "failed"},)))
        self.assertTrue(any("concluded FAILURE" in p for p in found), found)

    def test_allowed_failure_does_not_block(self):
        failed = {**JOB, "name": "lint", "status": "failed", "allow_failure": True}
        self.assertEqual(problems(served(jobs=(JOB, failed))), [])

    def test_canceled_and_manual_block(self):
        found = problems(served(jobs=({**JOB, "name": "a", "status": "canceled"},
                                      {**JOB, "name": "b", "status": "manual"})))
        self.assertTrue(any("concluded CANCELLED" in p for p in found), found)
        self.assertTrue(any("concluded ACTION_REQUIRED" in p for p in found), found)

    def test_detailed_merge_status_is_reported(self):
        found = problems(served(detailed="discussions_not_resolved"))
        self.assertIn("pull request is not mergeable (discussions_not_resolved)", found)


if __name__ == "__main__":
    unittest.main()
