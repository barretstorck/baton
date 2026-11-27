import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from baton import identity
from baton.forge.forgejo import Forgejo, ForgejoError, Unsupported

from tests.fakes import FakeTransport

TOKEN = "s3cret-token"


def make(replies=None, token=TOKEN):
    transport = FakeTransport(replies)
    return Forgejo("https://f.example/", "o", "r", token=token, transport=transport), transport


class TransportTest(unittest.TestCase):
    def test_token_in_header_not_url(self):
        forge, t = make({"GET /api/v1/x": (200, {"a": 1})})
        self.assertEqual(forge._request("GET", "/x", params={"q": "1"}), {"a": 1})
        forge._request("GET", "/x")
        for _, url, headers, _ in t.calls:
            self.assertEqual(headers["Authorization"], f"token {TOKEN}")
            self.assertNotIn(TOKEN, url)
        self.assertEqual(t.lines()[0], "GET /api/v1/x?q=1")

    def test_body_is_json(self):
        forge, t = make({"POST /api/v1/x": (201, "")})
        self.assertIsNone(forge._request("POST", "/x", body={"a": 1}))
        self.assertEqual(t.calls[0][3], b'{"a": 1}')
        self.assertEqual(t.calls[0][2]["Content-Type"], "application/json")

    def test_raw_text(self):
        forge, _ = make({"GET /api/v1/d": (200, "diff text")})
        self.assertEqual(forge._request("GET", "/d", raw=True), "diff text")

    def test_404_raises_without_token(self):
        forge, _ = make()
        with self.assertRaises(ForgejoError) as ctx:
            forge._request("GET", "/nope")
        self.assertIn("404", str(ctx.exception))
        self.assertIn("not found", str(ctx.exception))
        self.assertNotIn(TOKEN, str(ctx.exception))

    def test_paged_stops_on_short_page(self):
        full = [{"i": n} for n in range(50)]
        forge, t = make({"GET /api/v1/l?limit=50&page=1": (200, full),
                         "GET /api/v1/l?limit=50&page=2": (200, [{"i": 50}])})
        self.assertEqual(len(forge._paged("/l")), 51)
        self.assertEqual(len(t.calls), 2)

    def test_paged_stops_at_limit(self):
        full = [{"i": n} for n in range(50)]
        forge, t = make({"GET /api/v1/l": (200, full)})
        self.assertEqual(len(forge._paged("/l", limit=10)), 10)
        self.assertEqual(len(t.calls), 1)

    def test_missing_token_names_variable(self):
        forge = Forgejo("https://f.example", "o", "r", transport=FakeTransport())
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch("baton.verify._home",
                           return_value=pathlib.Path("/nonexistent-baton-home")):
            with self.assertRaises(ForgejoError) as ctx:
                forge._request("GET", "/x")
        self.assertIn("FORGEJO_TOKEN", str(ctx.exception))

    def test_unsupported_methods(self):
        forge, t = make()
        for call in (lambda: forge.issue_lock(1),
                     lambda: forge.review_edit_body(1, 2, "p")):
            with self.assertRaises(Unsupported) as ctx:
                call()
            self.assertIn("not supported on Forgejo", str(ctx.exception))
        self.assertEqual(t.calls, [])

    def test_issue_body_edits_none(self):
        forge, _ = make()
        self.assertIsNone(forge.issue_body_edits(1))


class LabelMilestoneTest(unittest.TestCase):
    def labels(self, org=(200, [{"id": 2, "name": "org-l"}])):
        return make({"GET /api/v1/repos/o/r/labels": (200, [{"id": 1, "name": "repo-l"}]),
                     "GET /api/v1/orgs/o/labels": org})

    def test_names_merged(self):
        forge, _ = self.labels()
        self.assertEqual(forge.label_names(), {"repo-l", "org-l"})

    def test_org_404_ok(self):
        forge, _ = self.labels(org=(404, {"message": "no org"}))
        self.assertEqual(forge.label_names(), {"repo-l"})

    def test_label_create_color(self):
        forge, t = make({"POST /api/v1/repos/o/r/labels": (201, {})})
        forge.label_create("n", "ff0000", "d")
        self.assertEqual(json.loads(t.calls[0][3]),
                         {"name": "n", "color": "#ff0000", "description": "d"})

    def test_label_ids(self):
        forge, _ = self.labels()
        self.assertEqual(forge._label_ids(["org-l", "repo-l"]), [2, 1])
        with self.assertRaises(ForgejoError) as ctx:
            forge._label_ids(["nope"])
        self.assertIn("nope", str(ctx.exception))

    def test_milestones_shape(self):
        forge, t = make({"GET /api/v1/repos/o/r/milestones": (200, [
            {"id": 9, "title": "M", "description": "d", "state": "open",
             "due_on": "2026-01-01T00:00:00Z", "created_at": "2025-12-01T00:00:00Z",
             "closed_at": None}])})
        self.assertEqual(forge.milestones(), [{"number": 9, "title": "M", "description": "d",
                                               "state": "open", "due_on": "2026-01-01T00:00:00Z",
                                               "created_at": "2025-12-01T00:00:00Z",
                                               "closed_at": None}])
        self.assertIn("state=all", t.lines()[0])
        self.assertEqual(json.loads(forge.milestones_text("o", "r", "open"))[0]["number"], 9)
        self.assertEqual(forge._milestone_id("M"), 9)
        with self.assertRaises(ForgejoError):
            forge._milestone_id("X")

    def test_milestone_create_due_on_only_when_given(self):
        forge, t = make({"POST /api/v1/repos/o/r/milestones": (201, {})})
        forge.milestone_create("A")
        forge.milestone_create("B", due_on="2026-02-01T00:00:00Z")
        self.assertEqual(json.loads(t.calls[0][3]), {"title": "A"})
        self.assertIn("due_on", json.loads(t.calls[1][3]))

    def test_milestone_edit(self):
        forge, t = make({"PATCH /api/v1/repos/o/r/milestones/4": (200, {})})
        forge.milestone_edit(4, [("state", "closed")])
        self.assertEqual(json.loads(t.calls[0][3]), {"state": "closed"})


if __name__ == "__main__":
    unittest.main()


class IssueTest(unittest.TestCase):
    ISSUE = {"number": 7, "title": "T", "body": None, "state": "open",
             "user": {"login": "u"}, "labels": [{"name": "a", "id": 1}],
             "created_at": "2026-01-01T00:00:00Z", "assignees": [{"login": "v"}]}
    LABELS = {"GET /api/v1/repos/o/r/labels": (200, [{"name": "a", "id": 4}]),
              "GET /api/v1/orgs/o/labels": (404, {"message": "no"}),
              "GET /api/v1/repos/o/r/milestones": (200, [{"id": 9, "title": "M"}])}

    def body(self, text="hello"):
        handle = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        handle.write(text)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_list_filters_label_and_milestone_client_side(self):
        mine = dict(self.ISSUE, number=1, milestone={"id": 9, "title": "M"})
        no_label = dict(self.ISSUE, number=2, labels=[], milestone={"id": 9, "title": "M"})
        other = dict(self.ISSUE, number=3, milestone={"id": 8, "title": "N"})
        bare = dict(self.ISSUE, number=4, milestone=None)
        forge, _ = make({"GET /api/v1/repos/o/r/issues": (200, [mine, no_label, other, bare])})
        got = forge.issue_list("number", "all", 50, milestone="M", label="a")
        self.assertEqual([i["number"] for i in got], [1])
        got = forge.issue_list("number", "all", 50, milestone="9")
        self.assertEqual([i["number"] for i in got], [1, 2])
        got = forge.issue_list("number", "all", 50, label="a")
        self.assertEqual([i["number"] for i in got], [1, 3, 4])

    def test_issue_view_shape(self):
        forge, t = make({"GET /api/v1/repos/o/r/issues/7/comments": (200, [
            {"id": 5, "user": {"login": "c"}, "body": "b", "created_at": "x",
             "html_url": "https://f/o/r/issues/7#issuecomment-5"}]),
            "GET /api/v1/repos/o/r/issues/7": (200, self.ISSUE)})
        got = forge.issue_view(7, "number,title,state,author,labels,body,comments")
        self.assertEqual(got["state"], "OPEN")
        self.assertEqual(got["body"], "")
        self.assertEqual(got["author"], {"login": "u"})
        self.assertEqual(got["labels"], [{"name": "a"}])
        self.assertEqual(got["comments"][0]["id"], "5")
        forge, t = make({"GET /api/v1/repos/o/r/issues/7": (200, self.ISSUE)})
        forge.issue_view(7, "number,title")
        self.assertEqual(t.lines(), ["GET /api/v1/repos/o/r/issues/7"])

    def test_unknown_field_before_request(self):
        forge, t = make()
        with self.assertRaises(Unsupported):
            forge.issue_view(7, "number,bogus")
        self.assertEqual(t.calls, [])

    def test_issue_list(self):
        forge, t = make({"GET /api/v1/repos/o/r/issues": (200, [
            dict(self.ISSUE, milestone={"id": 9, "title": "M"})])})
        got = forge.issue_list("number", "open", 10, milestone="M", label="a")
        self.assertEqual(got, [{"number": 7}])
        line = t.lines()[0]
        for part in ("type=issues", "state=open", "labels=a", "milestones=M"):
            self.assertIn(part, line)
        with self.assertRaises(Unsupported):
            forge.issue_list("number", "open", 10, search="closed:>=2026-01-01")
        full = [dict(self.ISSUE, number=n) for n in range(50)]
        forge, t = make({"GET /api/v1/repos/o/r/issues?": (200, full)})
        self.assertEqual(len(forge.issue_list("number", "all", 20)), 20)
        self.assertEqual(len(t.calls), 1)

    def test_issues_closed_since(self):
        def closed(number, stamp):
            return dict(self.ISSUE, number=number, state="closed", closed_at=stamp)

        items = [closed(1, "2026-01-02T05:00:00Z"), closed(2, "2026-01-01T23:00:00Z"),
                 closed(3, "2026-01-02T00:30:00+02:00"),
                 dict(self.ISSUE, number=4, state="closed", closed_at=None)]
        forge, t = make({"GET /api/v1/repos/o/r/issues": (200, items)})
        got = forge.issues_closed_since("number,state", "2026-01-02", 200)
        self.assertEqual(got, [{"number": 1, "state": "CLOSED"}])
        line = t.lines()[0]
        for part in ("type=issues", "state=closed", "since=2026-01-02T00%3A00%3A00Z"):
            self.assertIn(part, line)
        self.assertNotIn("q=", line)
        forge, t = make()
        with self.assertRaises(Unsupported):
            forge.issues_closed_since("number,bogus", "2026-01-02", 200)
        self.assertEqual(t.calls, [])

    def test_search_sends_q_and_ranks(self):
        items = [dict(self.ISSUE, number=1, title="other", body="nothing"),
                 dict(self.ISSUE, number=2, title="other", body="csv here"),
                 dict(self.ISSUE, number=3, title="Export CSV", body="")]
        forge, t = make({"GET /api/v1/repos/o/r/issues": (200, items)})
        got = forge.issue_list("number", "open", 10, search="export csv")
        self.assertIn("q=export+csv", t.lines()[0])
        self.assertEqual(got, [{"number": 3}, {"number": 2}, {"number": 1}])

    def test_search_ranks_before_limit(self):
        items = [dict(self.ISSUE, number=n, title="x", body="") for n in range(1, 61)]
        items[55]["title"] = "needle"
        base = "GET /api/v1/repos/o/r/issues?type=issues&state=open&q=needle&limit=50&page="
        forge, t = make({base + "1": (200, items[:50]), base + "2": (200, items[50:])})
        got = forge.issue_list("number", "open", 5, search="needle")
        self.assertEqual(len(got), 5)
        self.assertEqual(got[0], {"number": 56})
        self.assertEqual(len(t.calls), 2)

    def test_issue_edit_labels(self):
        forge, t = make({"POST /api/v1/repos/o/r/issues/1/labels": (200, []),
                         "DELETE /api/v1/repos/o/r/issues/1/labels/": (204, "")})
        forge.issue_edit_labels(1, ["x"], ["a b"])
        self.assertEqual(t.lines(), ["POST /api/v1/repos/o/r/issues/1/labels",
                                     "DELETE /api/v1/repos/o/r/issues/1/labels/a%20b"])
        self.assertEqual(t.calls[0][3], b'{"labels": ["x"]}')

    def test_comment_returns_url(self):
        forge, t = make({"POST /api/v1/repos/o/r/issues/2/comments":
                         (201, {"html_url": "https://f/o/r/issues/2#issuecomment-8"})})
        self.assertEqual(forge.comment("issue", 2, self.body()),
                         "https://f/o/r/issues/2#issuecomment-8\n")

    def test_change_state_pr(self):
        forge, t = make({"PATCH /api/v1/repos/o/r/pulls/3": (201, {})})
        forge.change_state("pr", "close", 3)
        self.assertEqual(t.lines(), ["PATCH /api/v1/repos/o/r/pulls/3"])
        self.assertEqual(t.calls[0][3], b'{"state": "closed"}')

    def test_issue_create_resolves_ids(self):
        forge, t = make({**self.LABELS, "POST /api/v1/repos/o/r/issues":
                         (201, {"html_url": "https://f/o/r/issues/9"})})
        out = forge.issue_create("T", self.body(), labels=["a"], milestone="M")
        self.assertEqual(out, "https://f/o/r/issues/9\n")
        sent = json.loads(t.calls[-1][3])
        self.assertEqual((sent["labels"], sent["milestone"], sent["body"]), ([4], 9, "hello"))

    def test_item_edit_remove_milestone(self):
        forge, t = make({"PATCH /api/v1/repos/o/r/issues/4": (201, {})})
        forge.item_edit("issue", 4, remove_milestone=True)
        self.assertEqual(json.loads(t.calls[0][3]), {"milestone": 0})

    def test_timeline(self):
        forge, _ = make({"GET /api/v1/repos/o/r/issues/1/timeline": (200, [
            {"type": "assignees", "removed_assignee": False, "user": {"login": "a"},
             "assignee": {"login": "b"}, "created_at": "t1"},
            {"type": "assignees", "removed_assignee": True, "user": {"login": "a"},
             "assignee": {"login": "b"}, "created_at": "t2"},
            {"type": "comment", "created_at": "t3"}])})
        got = forge.issue_timeline(1)
        self.assertEqual([e["event"] for e in got], ["assigned", "unassigned"])
        self.assertEqual(got[0], {"event": "assigned", "by": "a", "subject": "b", "at": "t1"})
        bad, _ = make({"GET /api/v1/repos/o/r/issues/1/timeline": (500, "")})
        self.assertIsNone(bad.issue_timeline(1))

    def test_repo_view(self):
        forge, _ = make({"GET /api/v1/repos/o/r": (200, {
            "name": "r", "full_name": "o/r", "default_branch": "main",
            "owner": {"login": "o"}})})
        self.assertEqual(forge.repo_view("owner,name,nameWithOwner,defaultBranchRef"),
                         {"owner": {"login": "o"}, "name": "r", "nameWithOwner": "o/r",
                          "defaultBranchRef": {"name": "main"}})


class PullRequestTest(unittest.TestCase):
    PR = {"number": 3, "title": "T", "body": None, "state": "closed", "merged": True,
          "draft": False, "mergeable": True, "changed_files": 1,
          "html_url": "https://f/o/r/pulls/3", "user": {"login": "u"},
          "requested_reviewers": [{"login": "rv"}],
          "head": {"ref": "br", "sha": "abc", "repo": {"full_name": "o/r"}},
          "base": {"ref": "main", "repo": {"full_name": "o/r"}}}

    def body(self, text="hello"):
        handle = tempfile.NamedTemporaryFile("w", delete=False)
        handle.write(text)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_pr_view_all_fields(self):
        from baton.merge import PR_FIELDS
        fields = PR_FIELDS
        forge, _ = make({
            "GET /api/v1/repos/o/r/pulls/3/files": (200, [
                {"filename": "a.py", "additions": 2, "deletions": 1,
                 "status": "changed"}]),
            "GET /api/v1/repos/o/r/pulls/3/commits": (200, [
                {"sha": "abc", "commit": {"message": "head\n\nbody"}}]),
            "GET /api/v1/repos/o/r/commits/abc/status": (200, {"statuses": [
                {"context": "ci", "status": "success", "target_url": "u"}]}),
            "GET /api/v1/repos/o/r/issues/3/comments": (200, []),
            "GET /api/v1/repos/o/r/branches/main": (200, {"enable_status_check": False}),
            "GET /api/v1/repos/o/r/pulls/3": (200, self.PR)})
        got = forge.pr_view(3, fields)
        self.assertEqual(set(got), set(fields.split(",")))
        self.assertEqual(got["state"], "MERGED")
        self.assertEqual(got["mergeable"], "MERGEABLE")
        self.assertFalse(got["isCrossRepository"])
        self.assertEqual(got["files"], [{"path": "a.py", "additions": 2, "deletions": 1,
                                           "previousPath": None}])
        self.assertEqual(got["commits"], [{"oid": "abc", "messageHeadline": "head",
                                           "messageBody": "body"}])
        self.assertEqual(got["statusCheckRollup"], [{"__typename": "StatusContext",
                         "context": "ci", "state": "SUCCESS", "targetUrl": "u"}])
        self.assertEqual(got["closingIssuesReferences"], [])

    def test_pr_files_rename_source(self):
        forge, _ = make({"GET /api/v1/repos/o/r/pulls/3/files": (200, [
            {"filename": "b", "status": "renamed", "previous_filename": "a"},
            {"filename": "d", "status": "renamed"},
            {"filename": "e"}])})
        got = forge._pr_files(3)
        self.assertEqual(got[0]["previousPath"], "a")
        self.assertNotIn("previousPath", got[1])
        self.assertNotIn("previousPath", got[2])

    def shape(self, **changes):
        forge, _ = make()
        return forge._pr_shape({**self.PR, **changes}, ["isDraft", "isCrossRepository"])

    def test_rollup_reads_every_page(self):
        first = [{"context": f"c{i}", "status": "success"} for i in range(50)]
        forge, _ = make({
            "GET /api/v1/repos/o/r/commits/abc/status?limit=50&page=1":
                (200, {"statuses": first, "total_count": 51}),
            "GET /api/v1/repos/o/r/commits/abc/status?limit=50&page=2":
                (200, {"statuses": [{"context": "last", "status": "failure"}],
                       "total_count": 51})})
        got = forge._pr_rollup("abc")
        self.assertEqual(len(got), 51)
        self.assertEqual(got[-1]["state"], "FAILURE")

    def test_rollup_short_total_fails_closed(self):
        forge, _ = make({"GET /api/v1/repos/o/r/commits/abc/status": (200, {
            "statuses": [{"context": "ci", "status": "success"}], "total_count": 3})})
        got = forge._pr_rollup("abc")
        self.assertEqual(got[-1]["state"], "ERROR")

    def test_rollup_failing_state_without_statuses(self):
        forge, _ = make({"GET /api/v1/repos/o/r/commits/abc/status": (200, {
            "statuses": [], "state": "failure"})})
        self.assertEqual(forge._pr_rollup("abc")[0]["state"], "ERROR")

    def rollup_with(self, branch, statuses):
        replies = {"GET /api/v1/repos/o/r/commits/abc/status": (200, {"statuses": statuses})}
        if branch is not None:
            replies["GET /api/v1/repos/o/r/branches/main"] = (200, branch)
        forge, _ = make(replies)
        return forge._pr_rollup("abc", "main")

    def test_required_context_matched_by_glob(self):
        got = self.rollup_with({"enable_status_check": True, "status_check_contexts": ["ci/*"]},
                               [{"context": "ci/test", "status": "success"}])
        self.assertEqual([c["context"] for c in got], ["ci/test"])

    def test_required_context_missing_is_pending(self):
        got = self.rollup_with({"enable_status_check": True, "status_check_contexts": ["ci/*"]},
                               [])
        self.assertEqual(got, [{"__typename": "StatusContext", "context": "ci/*",
                                "state": "PENDING", "targetUrl": None}])

    def test_required_contexts_off(self):
        got = self.rollup_with({"enable_status_check": False, "status_check_contexts": ["x"]},
                               [])
        self.assertEqual(got, [])

    def test_branch_read_failure_fails_closed(self):
        got = self.rollup_with(None, [{"context": "ci", "status": "success"}])
        self.assertEqual(got[-1]["context"], "baton:required-contexts-unknown")
        self.assertEqual(got[-1]["state"], "ERROR")

    def test_cross_repository_fails_closed(self):
        self.assertFalse(self.shape()["isCrossRepository"])
        self.assertTrue(self.shape(head={"ref": "br", "sha": "abc"},
                                   base={"ref": "main"})["isCrossRepository"])
        self.assertTrue(self.shape(head={"ref": "refs/pull/3/head", "sha": "abc",
                                         "repo": {"full_name": "o/r"}})["isCrossRepository"])
        self.assertTrue(self.shape(flow=1)["isCrossRepository"])

    def test_draft_fails_closed(self):
        self.assertFalse(self.shape()["isDraft"])
        self.assertTrue(self.shape(title=" wip: x")["isDraft"])
        self.assertTrue(self.shape(title="[WIP] x")["isDraft"])
        no_draft = {k: v for k, v in self.PR.items() if k != "draft"}
        forge, _ = make()
        self.assertTrue(forge._pr_shape(no_draft, ["isDraft"])["isDraft"])

    def test_closing_references_drop_pull_requests_and_missing(self):
        forge, _ = make({"GET /api/v1/repos/o/r/pulls/3/commits": (200, []),
                         "GET /api/v1/repos/o/r/issues/4": (200, {"number": 4}),
                         "GET /api/v1/repos/o/r/issues/5": (200, {"number": 5, "pull_request": {}}),
                         "GET /api/v1/repos/o/r/issues/6": (404, "")})
        body = "Closes #3, closes #4, fixes #5, resolves #6"
        got = forge._pr_shape({**self.PR, "body": body}, ["closingIssuesReferences"])
        self.assertEqual(got["closingIssuesReferences"], [{"number": 4}])

    def test_closing_references_from_body_and_commits(self):
        forge, _ = make({"GET /api/v1/repos/o/r/pulls/3/commits": (200, [
            {"sha": "a", "commit": {"message": "Add x\n\nCloses: #7"}},
            {"sha": "b", "commit": {"message": "see #9"}}]),
            "GET /api/v1/repos/o/r/issues/4": (200, {"number": 4}),
            "GET /api/v1/repos/o/r/issues/7": (200, {"number": 7})})
        got = forge._pr_shape({**self.PR, "body": "Fixes #4"}, ["closingIssuesReferences"])
        self.assertEqual(got["closingIssuesReferences"], [{"number": 4}, {"number": 7}])
        forge, _ = make({"GET /api/v1/repos/o/r/pulls/3/commits": (200, [
            {"sha": "b", "commit": {"message": "see #9"}}])})
        got = forge._pr_shape({**self.PR, "body": "no keyword"}, ["closingIssuesReferences"])
        self.assertEqual(got["closingIssuesReferences"], [])

    def test_pr_merge(self):
        forge, t = make({"POST /api/v1/repos/o/r/pulls/3/merge": (200, "")})
        forge.pr_merge(3, "abc")
        self.assertEqual(json.loads(t.calls[0][3]), {"Do": "merge", "head_commit_id": "abc"})
        bad, _ = make({"POST /api/v1/repos/o/r/pulls/3/merge": (405, {"message": "no"})})
        with self.assertRaises(ForgejoError) as ctx:
            bad.pr_merge(3, "abc")
        self.assertIn("405", str(ctx.exception))

    def test_pr_create(self):
        transport = FakeTransport({
            "GET /api/v1/repos/o/r": (200, {"default_branch": "main"}),
            "POST /api/v1/repos/o/r/pulls": (201, {"html_url": "https://f/o/r/pulls/5"})})
        seen = []
        forge = Forgejo("https://f.example", "o", "r", token=TOKEN, transport=transport,
                        runner=lambda argv: seen.append(argv) or "feat\n")
        self.assertEqual(forge.pr_create("T", self.body()), "https://f/o/r/pulls/5\n")
        self.assertEqual(seen, [["git", "rev-parse", "--abbrev-ref", "HEAD"]])
        sent = json.loads(transport.calls[-1][3])
        self.assertEqual((sent["head"], sent["base"], sent["body"]), ("feat", "main", "hello"))
        forge.pr_create("T", self.body(), draft=True)
        self.assertEqual(json.loads(transport.calls[-1][3])["title"], "WIP: T")

    def ready(self, title, **kw):
        forge, t = make({"GET /api/v1/repos/o/r/pulls/3": (200, {"title": title}),
                         "PATCH /api/v1/repos/o/r/pulls/3": (201, {})})
        self.assertEqual(forge.pr_ready(3, **kw), "")
        return [json.loads(c[3]) for c in t.calls if c[0] == "PATCH"]

    def test_pr_ready_strips_prefix(self):
        self.assertEqual(self.ready("wip: T"), [{"title": "T"}])

    def test_pr_ready_undo_adds_prefix(self):
        self.assertEqual(self.ready("T", undo=True), [{"title": "WIP: T"}])

    def test_pr_ready_unchanged_sends_nothing(self):
        self.assertEqual(self.ready("T"), [])
        self.assertEqual(self.ready("WIP: T", undo=True), [])

    def test_draft_custom_prefix(self):
        forge = Forgejo("https://f.example", "o", "r", token=TOKEN,
                        transport=FakeTransport(), draft_prefix="[draft]")
        self.assertTrue(forge._pr_shape(dict(self.PR, title="[Draft] x"), ["isDraft"])["isDraft"])

    def test_pr_review_approve(self):
        forge, t = make({"POST /api/v1/repos/o/r/pulls/3/reviews": (200, {})})
        forge.pr_review(3, "approve", self.body())
        self.assertEqual(json.loads(t.calls[0][3]), {"event": "APPROVED", "body": "hello"})

    def test_review_items(self):
        forge, _ = make({"GET /api/v1/repos/o/r/pulls/3/reviews": (200, [
            {"id": 1, "user": {"login": "a"}, "body": "x", "submitted_at": "t2", "html_url": "u2"},
            {"id": 2, "user": {"login": "a"}, "body": "y", "submitted_at": "t1", "html_url": "u1"},
            {"id": 3, "user": {"login": "a"}, "body": "z", "html_url": "u3"}])})
        got = forge.pr_review_items(3)
        self.assertEqual([i["id"] for i in got], ["1", "2"])
        self.assertEqual(got[0], {"id": "1", "author": {"login": "a", "type": "User"},
                                  "body": "x", "createdAt": "t2", "url": "u2"})
        self.assertEqual(forge.review_urls(3, "a"), ["u1", "u2"])

    def test_pr_list_and_reviewer_remove(self):
        forge, t = make({"GET /api/v1/repos/o/r/pulls": (200, [self.PR]),
                         "DELETE /api/v1/repos/o/r/pulls/3/requested_reviewers": (204, "")})
        self.assertEqual(json.loads(forge.pr_list_text("number,state", "open", 5)),
                         [{"number": 3, "state": "MERGED"}])
        forge.review_request_remove(3, "rv")
        self.assertEqual(json.loads(t.calls[-1][3]), {"reviewers": ["rv"]})


class CiTest(unittest.TestCase):
    RUNS = {"workflow_runs": [
        {"id": 1, "status": "success", "commit_sha": "abc", "prettyref": "feat",
         "workflow_id": "ci.yml", "event": "push", "html_url": "u1"},
        {"id": 2, "status": "waiting", "commit_sha": "abc", "prettyref": "feat",
         "workflow_id": "ci.yml", "event": "push", "html_url": "u2"},
        {"id": 3, "status": "success", "commit_sha": "abc", "prettyref": "other",
         "workflow_id": "ci.yml", "event": "push", "html_url": "u3"}]}

    def test_run_list(self):
        forge, t = make({"GET /api/v1/repos/o/r/actions/runs": (200, self.RUNS)})
        got = json.loads(forge.run_list_text("feat", "abc", "push", 5,
                                             "databaseId,status,conclusion"))
        self.assertEqual(got, [{"databaseId": 1, "status": "completed", "conclusion": "success"},
                               {"databaseId": 2, "status": "queued", "conclusion": ""}])
        self.assertEqual(t.lines()[0],
                         "GET /api/v1/repos/o/r/actions/runs?head_sha=abc&event=push&limit=5")

    def test_run_view(self):
        forge, _ = make({"GET /api/v1/repos/o/r/actions/runs/1": (
            200, self.RUNS["workflow_runs"][0])})
        self.assertEqual(forge.run_view(1, "headSha,workflowName"),
                         {"headSha": "abc", "workflowName": "ci.yml"})
        with self.assertRaises(Unsupported):
            forge.run_view(1, "jobs")

    def test_workflow_fallback(self):
        forge, t = make({
            "GET /api/v1/repos/o/r/contents/.github/workflows": (200, [{"name": "ci.yml"}]),
            "GET /api/v1/repos/o/r/raw/.github/workflows/ci.yml": (200, "on: push")})
        self.assertEqual(json.loads(forge.workflow_entries_text("o", "r", "abc")),
                         [{"name": "ci.yml"}])
        self.assertEqual(forge.workflow_file_text("o", "r", "ci.yml", "abc"), "on: push")
        self.assertIn("contents/.forgejo/workflows?ref=abc", t.lines()[0])
        empty, _ = make()
        with self.assertRaises(ForgejoError) as ctx:
            empty.workflow_entries_text("o", "r", "abc")
        self.assertIn("404", str(ctx.exception))

    def test_workflow_prefers_forgejo(self):
        forge, _ = make({"GET /api/v1/repos/o/r/raw/.forgejo/workflows/ci.yml": (200, "f")})
        self.assertEqual(forge.workflow_file_text("o", "r", "ci.yml", "abc"), "f")

    def test_actions_permissions(self):
        forge, _ = make({"GET /api/v1/repos/o/r": (200, {"has_actions": False})})
        self.assertEqual(json.loads(forge.actions_permissions_text("o", "r")),
                         {"enabled": False})

    def test_branch_head(self):
        forge, _ = make({"GET /api/v1/repos/o/r/branches/a%2Fb": (
            200, {"commit": {"id": "abc", "timestamp": "T"}})})
        self.assertEqual(forge.branch_head("o", "r", "a/b"),
                         {"sha": "abc", "commit": {"committer": {"date": "T"}}})

    def test_last_push(self):
        feed = [{"op_type": "create_issue", "ref_name": ""},
                {"op_type": "commit_repo", "ref_name": "refs/heads/other", "created": "t0"},
                {"op_type": "commit_repo", "ref_name": "refs/heads/feat", "created": "t1"}]
        forge, _ = make({"GET /api/v1/repos/o/r/activities/feeds": (200, feed)})
        self.assertEqual(forge.last_push("o", "r", "feat"), "t1")
        self.assertIsNone(forge.last_push("o", "r", "none"))


class RedirectTest(unittest.TestCase):
    def test_redirect_never_carries_token(self):
        import http.server
        import threading
        from baton.forge.forgejo import urllib_transport
        seen = []

        class Target(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass

        target = http.server.HTTPServer(("127.0.0.1", 0), Target)
        port = target.server_address[1]

        class Source(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{port}/x")
                self.end_headers()

            def log_message(self, *a):
                pass

        source = http.server.HTTPServer(("127.0.0.1", 0), Source)
        for srv in (target, source):
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            self.addCleanup(srv.server_close)
            self.addCleanup(srv.shutdown)
        status, _, _ = urllib_transport(
            "GET", f"http://127.0.0.1:{source.server_address[1]}/a",
            {"Authorization": f"token {TOKEN}"}, None)
        self.assertEqual(status, 302)
        self.assertEqual(seen, [])


def _role_token(kind, owner, repo, role, home=None):
    return {"reviewer": "tok-reviewer", "baton": "tok-baton"}[identity.route(role)]


class RoleTokenTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.body = os.path.join(self.dir.name, "body.md")
        pathlib.Path(self.body).write_text("hi")

    def send(self, call, fake=_role_token, replies=None):
        forge, t = make(replies or {"POST": (201, {"html_url": "u"}), "DELETE": (204, "")})
        with mock.patch("baton.forge.forgejo.identity.forge_role_token", fake):
            call(forge)
        return t

    def auth(self, t):
        return [headers["Authorization"] for _, _, headers, _ in t.calls]

    def test_reviewer_comment(self):
        t = self.send(lambda f: f.comment("issue", 5, self.body, role="reviewer"))
        self.assertEqual(self.auth(t), ["token tok-reviewer"])

    def test_other_roles_use_baton(self):
        for role in ("developer", "orchestrator"):
            t = self.send(lambda f: f.issue_edit_labels(5, ["a"], role=role))
            self.assertEqual(self.auth(t), ["token tok-baton"])

    def test_no_role_uses_maintainer(self):
        t = self.send(lambda f: f.comment("issue", 5, self.body))
        self.assertEqual(self.auth(t), [f"token {TOKEN}"])

    def test_none_falls_back_to_maintainer(self):
        t = self.send(lambda f: f.comment("issue", 5, self.body, role="reviewer"),
                      fake=lambda *a, **k: None)
        self.assertEqual(self.auth(t), [f"token {TOKEN}"])

    def test_error_sends_nothing(self):
        def boom(*a, **k):
            raise identity.IdentityError("no table")
        forge, t = make()
        with mock.patch("baton.forge.forgejo.identity.forge_role_token", boom):
            with self.assertRaises(ForgejoError) as ctx:
                forge.comment("issue", 5, self.body, role="reviewer")
        self.assertIn("Forgejo token: no table", str(ctx.exception))
        self.assertEqual(t.calls, [])

    def test_no_sudo_header(self):
        forge, t = make({"POST": (201, {"html_url": "u"}), "GET": (200, [])})
        with mock.patch("baton.forge.forgejo.identity.forge_role_token", _role_token):
            forge.comment("issue", 5, self.body, role="reviewer")
            forge.comment("issue", 5, self.body)
            forge.issue_edit_labels(5, ["a"], role="developer")
            forge.label_names()
        self.assertGreaterEqual(len(t.calls), 4)
        for _, _, headers, _ in t.calls:
            self.assertNotIn("sudo", [k.lower() for k in headers])


class NeutralReadsTest(unittest.TestCase):
    def test_visibility(self):
        for private, want in ((True, "PRIVATE"), (False, "PUBLIC")):
            forge, _ = make({"GET /api/v1/repos/o/r": (200, {"private": private})})
            self.assertEqual(forge.repo_view("visibility"), {"visibility": want})

    def test_label_list_includes_org_label(self):
        forge, _ = make({"GET /api/v1/repos/o/r/labels": (200, [
            {"id": 1, "name": "repo-l", "description": "mine"}]),
            "GET /api/v1/orgs/o/labels": (200, [{"id": 2, "name": "org-l"}])})
        self.assertEqual(forge.label_list(), [{"name": "repo-l", "description": "mine"},
                                              {"name": "org-l", "description": ""}])

    def test_pr_list_head_filter(self):
        prs = [{"number": n, "head": {"ref": ref}} for n, ref in ((1, "a"), (2, "b"), (3, "b"))]
        forge, _ = make({"GET /api/v1/repos/o/r/pulls": (200, prs)})
        out = json.loads(forge.pr_list_text("number", "open", 1, head="b"))
        self.assertEqual(out, [{"number": 2}])
        out = json.loads(forge.pr_list_text("number", "open", 10))
        self.assertEqual(len(out), 3)

    def test_run_log_failed_unsupported(self):
        forge, _ = make({})
        with self.assertRaises(Unsupported):
            forge.run_log_failed(1)
