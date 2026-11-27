import copy
import pathlib
import unittest
from baton import config, merge, review
from baton.forge.forgejo import Forgejo
from tests.fakes import FakeRunner, FakeTransport

OWNER = "octo"
HEAD = "abc1234" + "0" * 33
OLD = "d" * 40
API = "/api/v1/repos/o/r"


def make_config():
    return config.Config(root=pathlib.Path("."), data=copy.deepcopy(config.DEFAULTS))


def served(body="Closes #4", approved=HEAD, required=("ci/*",)):
    """A Forgejo backend serving pull request 9 on a fake transport."""
    comments = [{"id": 1, "body": review.compose("approve", approved, "Round 1."),
                 "user": {"login": OWNER}, "created_at": "2026-01-01T00:00:00Z"}]
    pr = {"number": 9, "title": "T", "body": body, "state": "open", "merged": False,
          "draft": False, "mergeable": True, "changed_files": 1,
          "html_url": "https://f.example/o/r/pulls/9", "user": {"login": "u"},
          "head": {"ref": "issue-4-x", "sha": HEAD, "repo": {"full_name": "o/r"}},
          "base": {"ref": "main", "repo": {"full_name": "o/r"}}}
    return Forgejo("https://f.example", "o", "r", token="t", transport=FakeTransport({
        f"GET {API}/pulls/9/files": (200, [{"filename": "src/tool.py", "additions": 1, "deletions": 0}]),
        f"GET {API}/pulls/9/commits": (200, [{"sha": HEAD, "commit": {"message": "Add x"}}]),
        f"GET {API}/commits/{HEAD}/status": (200, {"statuses": [
            {"context": "ci/test", "status": "success", "target_url": None}]}),
        f"GET {API}/branches/main": (200, {"enable_status_check": True,
                                           "status_check_contexts": list(required)}),
        f"GET {API}/issues/9/comments": (200, comments),
        f"GET {API}/pulls/9": (200, pr)}))


def problems(forge, runner=None):
    pr = forge.pr_view(9, merge.PR_FIELDS)
    return merge.check(pr, make_config(), OWNER, "main", runner or FakeRunner())


class ForgejoMergeCheckTest(unittest.TestCase):
    def test_approved_head_has_no_problems(self):
        self.assertEqual(problems(served()), [])

    def test_stale_approval_is_refused(self):
        runner = FakeRunner({f"git merge-base origin/main {OLD}": "b1\n",
                             f"git merge-base origin/main {HEAD}": "b2\n",
                             f"git diff --binary b1 {OLD}": "+a\n",
                             f"git diff --binary b2 {HEAD}": "+a\n+b\n"})
        found = problems(served(approved=OLD), runner)
        self.assertTrue(any("stale" in p for p in found), found)

    def test_missing_required_context_is_refused(self):
        found = problems(served(required=("ci/*", "deploy")))
        self.assertTrue(any("deploy" in p and "has not finished" in p for p in found), found)

    def test_no_closing_keyword_is_refused(self):
        found = problems(served(body="Adds x"))
        self.assertTrue(any("must close exactly one issue" in p for p in found), found)
