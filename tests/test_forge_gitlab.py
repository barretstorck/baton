import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from baton.forge.gitlab import GitLab, GitLabError, Unsupported, urllib_transport

from tests.fakes import FakeTransport

TOKEN = "s3cret-token"


def make(replies=None, token=TOKEN, owner="o"):
    transport = FakeTransport(replies)
    return GitLab("https://g.example/", owner, "r", token=token, transport=transport), transport


class TransportTest(unittest.TestCase):
    def test_token_in_header_not_url(self):
        forge, t = make({"GET /api/v4/x": (200, {"a": 1})})
        self.assertEqual(forge._request("GET", "/x", params={"q": "1"}), {"a": 1})
        forge._request("GET", "/x")
        for _, url, headers, _ in t.calls:
            self.assertEqual(headers["PRIVATE-TOKEN"], TOKEN)
            self.assertNotIn(TOKEN, url)
        self.assertEqual(t.lines()[0], "GET /api/v4/x?q=1")

    def test_body_is_json(self):
        forge, t = make({"POST /api/v4/x": (201, "")})
        self.assertIsNone(forge._request("POST", "/x", body={"a": 1}))
        self.assertEqual(t.calls[0][3], b'{"a": 1}')
        self.assertEqual(t.calls[0][2]["Content-Type"], "application/json")

    def test_raw_text(self):
        forge, _ = make({"GET /api/v4/d": (200, "diff text")})
        self.assertEqual(forge._request("GET", "/d", raw=True), "diff text")

    def test_404_raises_without_token_or_query(self):
        forge, _ = make()
        with self.assertRaises(GitLabError) as ctx:
            forge._request("GET", "/nope", params={"secret": "q"})
        self.assertIn("404", str(ctx.exception))
        self.assertIn("not found", str(ctx.exception))
        self.assertNotIn(TOKEN, str(ctx.exception))
        self.assertNotIn("secret", str(ctx.exception))

    def test_error_field_is_used(self):
        forge, _ = make({"GET /api/v4/x": (401, {"error": "invalid_token"})})
        with self.assertRaises(GitLabError) as ctx:
            forge._request("GET", "/x")
        self.assertIn("invalid_token", str(ctx.exception))

    def test_paged_stops_on_short_page(self):
        full = [{"i": n} for n in range(100)]
        forge, t = make({"GET /api/v4/l?per_page=100&page=1": (200, full),
                         "GET /api/v4/l?per_page=100&page=2": (200, [{"i": 100}])})
        self.assertEqual(len(forge._paged("/l")), 101)
        self.assertEqual(len(t.calls), 2)

    def test_paged_stops_at_limit(self):
        full = [{"i": n} for n in range(100)]
        forge, t = make({"GET /api/v4/l": (200, full)})
        self.assertEqual(len(forge._paged("/l", limit=10)), 10)
        self.assertEqual(len(t.calls), 1)

    def test_nested_owner_project_path(self):
        forge, _ = make(owner="a/b")
        self.assertEqual(forge._project(), "/projects/a%2Fb%2Fr")
        self.assertEqual(forge._project("x", "y"), "/projects/x%2Fy")

    def test_missing_token_names_variable(self):
        forge = GitLab("https://g.example", "o", "r", transport=FakeTransport())
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch("baton.verify._home",
                           return_value=pathlib.Path("/nonexistent-baton-home")):
            with self.assertRaises(GitLabError) as ctx:
                forge._request("GET", "/x")
        self.assertIn("GITLAB_TOKEN", str(ctx.exception))

    def test_unsupported_methods(self):
        forge, t = make()
        for call in (lambda: forge.review_edit_body(1, 2, "p"),
                     lambda: forge.comment_edit_body("3", "p"),
                     lambda: forge.pr_ready(1),
                     lambda: forge.workflow_entries_text("o", "r"),
                     lambda: forge.workflow_file_text("o", "r", "f"),
                     lambda: forge.actions_permissions_text("o", "r")):
            with self.assertRaises(Unsupported) as ctx:
                call()
            self.assertIn("not supported on GitLab", str(ctx.exception))
        self.assertEqual(t.calls, [])

    def test_empty_reads(self):
        forge, t = make()
        self.assertIsNone(forge.issue_body_edits(1))
        self.assertIsNone(forge.issue_timeline(1))
        self.assertEqual(forge.comment_author_types(1), {})
        self.assertEqual(forge._login({"username": "bob"}), {"login": "bob"})
        self.assertEqual(t.calls, [])

    def test_default_sleep(self):
        import time
        self.assertIs(make()[0].sleep, time.sleep)


class RedirectTest(unittest.TestCase):
    def test_redirect_never_carries_token(self):
        import http.server
        import threading
        seen = []

        class Target(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get("PRIVATE-TOKEN"))
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
            {"PRIVATE-TOKEN": TOKEN}, None)
        self.assertEqual(status, 302)
        self.assertEqual(seen, [])

    def test_connection_error_names_no_query(self):
        with self.assertRaises(GitLabError) as ctx:
            urllib_transport("GET", "http://127.0.0.1:1/p?secret=1", {}, None)
        self.assertIn("GitLab GET http://127.0.0.1:1/p", str(ctx.exception))
        self.assertNotIn("secret", str(ctx.exception))


class LabelMilestoneTest(unittest.TestCase):
    P = "/api/v4/projects/o%2Fr"

    def test_labels_include_group_labels(self):
        forge, t = make({f"GET {self.P}/labels": (200, [{"name": "bug"}, {"name": "grp"}])})
        self.assertEqual(forge.label_names(), {"bug", "grp"})
        self.assertIn("include_ancestor_groups=true", t.lines()[0])

    def test_label_color_gets_hash(self):
        forge, t = make({f"POST {self.P}/labels": (201, {})})
        self.assertEqual(forge.label_create("x", "ff0000", "d"), "")
        self.assertEqual(json.loads(t.calls[0][3]),
                         {"name": "x", "color": "#ff0000", "description": "d"})

    def test_milestones_ancestors_and_due(self):
        forge, t = make({f"GET {self.P}/milestones": (200, [
            {"id": 5, "title": "g", "description": None, "state": "active",
             "due_date": "2026-12-01"}])})
        got = forge.milestones()
        self.assertEqual(got, [{"number": 5, "title": "g", "description": "",
                                "state": "open", "due_on": "2026-12-01T00:00:00Z"}])
        self.assertIn("include_ancestors=true", t.lines()[0])
        self.assertNotIn("state=", t.lines()[0])
        forge.milestones_text("o", "r", "open")
        self.assertIn("state=active", t.lines()[1])

    def test_create_sends_due_date_only_when_given(self):
        forge, t = make({f"POST {self.P}/milestones": (201, {})})
        forge.milestone_create("a")
        forge.milestone_create("b", "d", "2026-12-01T00:00:00Z")
        self.assertEqual(json.loads(t.calls[0][3]), {"title": "a"})
        self.assertEqual(json.loads(t.calls[1][3]),
                         {"title": "b", "description": "d", "due_date": "2026-12-01"})

    def test_edit_maps_state(self):
        forge, t = make({f"PUT {self.P}/milestones/5": (200, {})})
        forge.milestone_edit(5, {"state": "closed"})
        forge.milestone_edit(5, {"state": "open"})
        self.assertEqual(json.loads(t.calls[0][3]), {"state_event": "close"})
        self.assertEqual(json.loads(t.calls[1][3]), {"state_event": "activate"})

    def test_milestone_id(self):
        forge, _ = make({f"GET {self.P}/milestones": (200, [
            {"id": 9, "title": "m", "state": "active"}])})
        self.assertEqual(forge._milestone_id("m"), 9)
        with self.assertRaises(GitLabError) as ctx:
            forge._milestone_id("nope")
        self.assertIn("nope", str(ctx.exception))


PROJ = "/api/v4/projects/o%2Fr"


def body_of(transport, index=-1):
    return json.loads(transport.calls[index][3])


class IssueTest(unittest.TestCase):
    ISSUE = {"iid": 7, "id": 900, "title": "T", "description": "D", "state": "opened",
             "author": {"username": "ann"}, "labels": ["bug", "x"],
             "created_at": "2026-01-01T00:00:00Z", "assignees": [{"username": "bob"}],
             "web_url": "https://g.example/o/r/-/issues/7"}
    NOTES = [{"id": 1, "author": {"username": "ann"}, "body": "hi", "created_at": "c1"},
             {"id": 2, "author": {"username": "ann"}, "body": "added label", "system": True,
              "created_at": "c2"}]

    def test_view_shape(self):
        forge, t = make({f"GET {PROJ}/issues/7/notes": (200, self.NOTES),
                         f"GET {PROJ}/issues/7": (200, self.ISSUE)})
        view = forge.issue_view(7, "number,title,body,state,author,labels,createdAt,"
                                   "assignees,comments")
        self.assertEqual(view["number"], 7)
        self.assertEqual(view["state"], "OPEN")
        self.assertEqual(view["labels"], [{"name": "bug"}, {"name": "x"}])
        self.assertEqual(view["assignees"], [{"login": "bob"}])
        self.assertEqual(view["author"], {"login": "ann"})
        self.assertEqual(len(view["comments"]), 1)
        self.assertEqual(view["comments"][0]["id"], "1")
        self.assertTrue(view["comments"][0]["url"].endswith("#note_1"))
        self.assertIn("sort=asc", t.lines()[-1])

    def test_closed_state(self):
        forge, _ = make({f"GET {PROJ}/issues/7": (200, {**self.ISSUE, "state": "closed"})})
        self.assertEqual(forge.issue_view(7, "state"), {"state": "CLOSED"})

    def test_unknown_field_raises_before_request(self):
        forge, t = make()
        with self.assertRaises(Unsupported):
            forge.issue_view(7, "number,bogus")
        self.assertEqual(t.calls, [])

    def test_issue_exists(self):
        forge, _ = make({f"GET {PROJ}/issues/7": (200, self.ISSUE)})
        self.assertTrue(forge.issue_exists(7))
        self.assertFalse(forge.issue_exists(8))

    def test_list_parameters_and_state_mapping(self):
        forge, t = make({f"GET {PROJ}/issues": (200, [self.ISSUE])})
        out = forge.issue_list("number", "open", 5, milestone="M", label="bug")
        self.assertEqual(out, [{"number": 7}])
        line = t.lines()[0]
        self.assertIn("state=opened", line)
        self.assertIn("labels=bug", line)
        self.assertIn("milestone=M", line)
        forge.issue_list("number", "closed", 5)
        self.assertIn("state=closed", t.lines()[-1])
        forge.issue_list("number", "all", 5)
        self.assertIn("state=all", t.lines()[-1])

    def test_list_search_unsupported(self):
        forge, t = make()
        with self.assertRaises(Unsupported):
            forge.issue_list("number", "open", 5, search="x")
        self.assertEqual(t.calls, [])

    def test_edit_labels_one_put(self):
        forge, t = make({f"PUT {PROJ}/issues/7": (200, {})})
        forge.issue_edit_labels(7, ["a", "b"], ["c", "d"])
        self.assertEqual(t.lines(), [f"PUT {PROJ}/issues/7"])
        self.assertEqual(body_of(t), {"add_labels": "a,b", "remove_labels": "c,d"})

    def test_edit_body(self):
        forge, t = make({f"PUT {PROJ}/issues/7": (200, {})})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "b.txt")
            pathlib.Path(path).write_text("new body")
            forge.issue_edit_body(7, path)
        self.assertEqual(body_of(t), {"description": "new body"})

    def _comment(self, target):
        forge, t = make({f"POST {PROJ}/issues/7/notes": (201, {"id": 55}),
                         f"POST {PROJ}/merge_requests/7/notes": (201, {"id": 56})})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "c.txt")
            pathlib.Path(path).write_text("hello")
            out = forge.comment(target, 7, path)
        return out, t

    def test_comment_on_issue(self):
        out, t = self._comment("issue")
        self.assertEqual(out, "https://g.example/o/r/-/issues/7#note_55\n")
        self.assertEqual(body_of(t), {"body": "hello"})

    def test_comment_on_pr_posts_to_merge_requests(self):
        out, t = self._comment("pr")
        self.assertEqual(t.lines(), [f"POST {PROJ}/merge_requests/7/notes"])
        self.assertEqual(out, "https://g.example/o/r/-/merge_requests/7#note_56\n")

    def test_state_events(self):
        forge, t = make({f"PUT {PROJ}/issues/7": (200, {}),
                         f"PUT {PROJ}/merge_requests/8": (200, {})})
        forge.issue_close(7, "completed")
        self.assertEqual(body_of(t), {"state_event": "close"})
        forge.change_state("pr", "reopen", 8)
        self.assertEqual(t.lines()[-1], f"PUT {PROJ}/merge_requests/8")
        self.assertEqual(body_of(t), {"state_event": "reopen"})

    def test_issue_lock(self):
        forge, t = make({f"PUT {PROJ}/issues/7": (200, {})})
        forge.issue_lock(7)
        self.assertEqual(body_of(t), {"discussion_locked": True})

    def test_issue_create_resolves_milestone(self):
        forge, t = make({f"GET {PROJ}/milestones": (200, [{"id": 31, "title": "M",
                                                          "state": "active"}]),
                         f"POST {PROJ}/issues": (201, {"web_url": "https://g.example/o/r/-/issues/9"})})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "i.txt")
            pathlib.Path(path).write_text("body")
            out = forge.issue_create("Title", path, labels=["a", "b"], milestone="M")
        self.assertEqual(out, "https://g.example/o/r/-/issues/9\n")
        self.assertEqual(body_of(t), {"title": "Title", "description": "body",
                                      "labels": "a,b", "milestone_id": 31})

    def test_item_edit_remove_milestone(self):
        forge, t = make({f"PUT {PROJ}/merge_requests/7": (200, {})})
        forge.item_edit("pr", 7, title="N", add_labels=["a"], remove_labels=["b"],
                        remove_milestone=True)
        self.assertEqual(t.lines(), [f"PUT {PROJ}/merge_requests/7"])
        self.assertEqual(body_of(t), {"title": "N", "milestone_id": 0, "add_labels": "a",
                                      "remove_labels": "b"})

    def test_item_edit_nothing_sends_nothing(self):
        forge, t = make()
        forge.item_edit("issue", 7)
        self.assertEqual(t.calls, [])

    def test_repo_view_nested_namespace(self):
        forge, t = make({"GET /api/v4/projects/a%2Fb%2Fr": (200, {
            "path": "r", "path_with_namespace": "a/b/r", "default_branch": "main",
            "namespace": {"full_path": "a/b"}})}, owner="a/b")
        view = forge.repo_view("owner,name,nameWithOwner,defaultBranchRef")
        self.assertEqual(view, {"owner": {"login": "a/b"}, "name": "r",
                                "nameWithOwner": "a/b/r", "defaultBranchRef": {"name": "main"}})

    def test_user(self):
        forge, _ = make({"GET /api/v4/users?username=ann": (200, [{"id": 1, "username": "ann"}])})
        self.assertEqual(forge.user("ann")["login"], "ann")
        with self.assertRaises(GitLabError):
            forge.user("zed")


class MergeRequestTest(unittest.TestCase):
    MR = {"iid": 7, "title": "T", "description": "D", "state": "opened", "draft": False,
          "has_conflicts": False, "detailed_merge_status": "mergeable",
          "source_branch": "feat", "sha": "abc", "target_branch": "main",
          "source_project_id": 1, "target_project_id": 1, "changes_count": "2",
          "author": {"username": "ann"}, "web_url": "https://g.example/o/r/-/merge_requests/7",
          "reviewers": [{"username": "bob"}]}

    def view(self, fields, **changes):
        forge, t = make({
            f"GET {PROJ}/merge_requests/7/notes": (200, [
                {"id": 3, "author": {"username": "c"}, "body": "hi", "created_at": "t"},
                {"id": 4, "system": True}]),
            f"GET {PROJ}/merge_requests/7/diffs": (200, [
                {"old_path": "a", "new_path": "a", "diff": "@@ -1 +1,2 @@\n-x\n+y\n+z\n"}]),
            f"GET {PROJ}/merge_requests/7/commits": (200, [
                {"id": "c1", "title": "Head", "message": "Head\n\nBody\nmore"}]),
            f"GET {PROJ}/merge_requests/7/closes_issues": (200, [{"iid": 4}]),
            f"GET {PROJ}/merge_requests/7": (200, {**self.MR, **changes})})
        return forge.pr_view(7, fields), t

    def test_every_field(self):
        view, _ = self.view(",".join(GitLab._PR_FIELDS))
        self.assertEqual(view["number"], 7)
        self.assertEqual(view["body"], "D")
        self.assertEqual(view["state"], "OPEN")
        self.assertFalse(view["isDraft"])
        self.assertEqual(view["mergeable"], "MERGEABLE")
        self.assertEqual((view["headRefName"], view["headRefOid"], view["baseRefName"]),
                         ("feat", "abc", "main"))
        self.assertFalse(view["isCrossRepository"])
        self.assertEqual(view["changedFiles"], 2)
        self.assertEqual(view["author"], {"login": "ann"})
        self.assertEqual(view["url"], self.MR["web_url"])
        self.assertEqual(view["reviewRequests"], [{"login": "bob"}])
        self.assertEqual([c["id"] for c in view["comments"]], ["3"])
        self.assertEqual(view["files"], [{"path": "a", "additions": 2, "deletions": 1}])
        self.assertEqual(view["commits"], [{"oid": "c1", "messageHeadline": "Head",
                                            "messageBody": "Body\nmore"}])
        self.assertEqual(view["closingIssuesReferences"], [{"number": 4}])

    def closes(self, replies):
        sleeps, queue = [], list(replies)
        forge, t = make()
        forge.sleep = sleeps.append

        def transport(method, url, headers, data):
            t.calls.append((method, url, headers, data))
            body = queue.pop(0) if len(queue) > 1 else queue[0]
            return 200, {}, json.dumps(body).encode()
        forge.transport = transport
        return forge._closes_issues(7), sleeps, t

    def test_closes_issues_retries_until_filled(self):
        got, sleeps, t = self.closes([[], [], [{"iid": 4}]])
        self.assertEqual(got, [{"number": 4}])
        self.assertEqual(sleeps, [2, 2])
        self.assertEqual(len(t.calls), 3)

    def test_closes_issues_always_empty(self):
        got, sleeps, t = self.closes([[]])
        self.assertEqual(got, [])
        self.assertEqual(sleeps, [2, 2, 2])
        self.assertEqual(len(t.calls), 4)

    def test_closes_issues_no_sleep_when_filled(self):
        got, sleeps, _ = self.closes([[{"iid": 1}, {"iid": 2}]])
        self.assertEqual(got, [{"number": 1}, {"number": 2}])
        self.assertEqual(sleeps, [])

    def test_states(self):
        for state, want in (("opened", "OPEN"), ("merged", "MERGED"), ("closed", "CLOSED"),
                            ("locked", "CLOSED")):
            self.assertEqual(self.view("state", state=state)[0]["state"], want)

    def test_mergeable(self):
        self.assertEqual(self.view("mergeable", has_conflicts=True)[0]["mergeable"], "CONFLICTING")
        self.assertEqual(self.view("mergeable", detailed_merge_status="checking")[0]["mergeable"],
                         "UNKNOWN")

    def test_missing_draft_reads_as_draft(self):
        mr = {k: v for k, v in self.MR.items() if k != "draft"}
        forge, _ = make({f"GET {PROJ}/merge_requests/7": (200, mr)})
        self.assertTrue(forge.pr_view(7, "isDraft")["isDraft"])
        self.assertTrue(self.view("isDraft", draft=True)[0]["isDraft"])

    def test_cross_repository(self):
        self.assertTrue(self.view("isCrossRepository", source_project_id=2)[0]["isCrossRepository"])
        mr = {k: v for k, v in self.MR.items() if k != "source_project_id"}
        forge, _ = make({f"GET {PROJ}/merge_requests/7": (200, mr)})
        self.assertTrue(forge.pr_view(7, "isCrossRepository")["isCrossRepository"])

    def test_changes_count_capped_is_none(self):
        self.assertIsNone(self.view("changedFiles", changes_count="1000+")[0]["changedFiles"])

    def test_list_text(self):
        forge, _ = make({f"GET {PROJ}/merge_requests?state=opened": (200, [self.MR])})
        out = json.loads(forge.pr_list_text("number,state", "open", 5))
        self.assertEqual(out, [{"number": 7, "state": "OPEN"}])

    def diff(self, entries):
        forge, _ = make({f"GET {PROJ}/merge_requests/7/diffs": (200, entries)})
        return forge.pr_diff(7)

    def test_diff_parses(self):
        from baton import forbid
        patch = self.diff([
            {"old_path": "n", "new_path": "n", "new_file": True, "diff": "@@ -0,0 +1 @@\n+new"},
            {"old_path": "c", "new_path": "c", "diff": "@@ -1 +1 @@\n-a\n+b\n"},
            {"old_path": "d", "new_path": "d", "deleted_file": True,
             "diff": "@@ -1 +0,0 @@\n-x\n"}])
        self.assertIn("--- /dev/null\n+++ b/n\n@@", patch)
        self.assertIn("--- a/d\n+++ /dev/null\n", patch)
        self.assertIn("diff --git a/c b/c\n--- a/c\n+++ b/c\n", patch)
        self.assertEqual(forbid.violations(patch, {"*": "ZZZ"}), [])
        self.assertEqual(len(forbid.violations(patch, {"n": "new"})), 1)

    def test_diff_refuses_unsafe_paths(self):
        for bad in ("a\nb", "a\rb", "a\tb", 'x"y', "x\\y"):
            for key in ("old_path", "new_path"):
                entry = {"old_path": "ok", "new_path": "ok", "diff": "@@ -1 +1 @@\n-a\n+b\n"}
                entry[key] = bad
                with self.assertRaises(GitLabError):
                    self.diff([entry])

    def test_diff_too_large_raises(self):
        for flag in ("too_large", "collapsed"):
            with self.assertRaises(GitLabError):
                self.diff([{"old_path": "a", "new_path": "a", "diff": "", flag: True}])


class CiTest(unittest.TestCase):
    def rollup(self, jobs, pipeline=None, sha="abc", bridges=()):
        mr = {**MergeRequestTest.MR, "sha": sha}
        if pipeline is not None:
            mr["head_pipeline"] = pipeline
        forge, t = make({
            f"GET {PROJ}/pipelines/9/jobs": (200, jobs),
            f"GET {PROJ}/pipelines/9/bridges": (200, list(bridges)),
            f"GET {PROJ}/merge_requests/7": (200, mr)})
        return forge.pr_view(7, "statusCheckRollup")["statusCheckRollup"], t

    def test_job_statuses(self):
        def job(status, **more):
            return {"name": "n", "stage": "test", "status": status, "web_url": "u",
                    "started_at": "t", **more}
        cases = [(job("success"), "COMPLETED", "SUCCESS"),
                 (job("failed"), "COMPLETED", "FAILURE"),
                 (job("failed", allow_failure=True), "COMPLETED", "NEUTRAL"),
                 (job("canceled"), "COMPLETED", "CANCELLED"),
                 (job("skipped"), "COMPLETED", "SKIPPED"),
                 (job("manual"), "COMPLETED", "ACTION_REQUIRED"),
                 (job("running"), "IN_PROGRESS", ""),
                 (job("pending"), "IN_PROGRESS", "")]
        rollup, _ = self.rollup([c[0] for c in cases], {"id": 9, "sha": "abc"})
        for entry, (_, status, conclusion) in zip(rollup[:len(cases)], cases):
            self.assertEqual((entry["status"], entry["conclusion"]), (status, conclusion))
        self.assertEqual(rollup[0], {
            "__typename": "CheckRun", "name": "n", "workflowName": "test",
            "status": "COMPLETED", "conclusion": "SUCCESS", "detailsUrl": "u",
            "startedAt": "t"})

    def test_failed_downstream_pipeline_is_failure(self):
        bridge = {"name": "deploy", "stage": "test", "status": "failed",
                  "downstream_pipeline": {"status": "failed", "web_url": "d"}}
        rollup, _ = self.rollup([], {"id": 9, "sha": "abc", "status": "success"}, bridges=[bridge])
        self.assertEqual(rollup[0]["name"], "deploy")
        self.assertEqual((rollup[0]["status"], rollup[0]["conclusion"]), ("COMPLETED", "FAILURE"))
        self.assertEqual(rollup[0]["detailsUrl"], "d")

    def test_bridge_without_downstream_is_in_progress(self):
        bridge = {"name": "deploy", "stage": "test", "status": "success"}
        rollup, _ = self.rollup([], {"id": 9, "sha": "abc", "status": "success"}, bridges=[bridge])
        self.assertEqual((rollup[0]["status"], rollup[0]["conclusion"]), ("IN_PROGRESS", ""))

    def test_pipeline_status_is_an_entry(self):
        jobs = [{"name": "n", "stage": "test", "status": "success"}]
        for state, status, conclusion in [
                ("running", "IN_PROGRESS", ""), ("pending", "IN_PROGRESS", ""),
                ("created", "IN_PROGRESS", ""), ("weird", "IN_PROGRESS", ""),
                ("success", "COMPLETED", "SUCCESS"), ("failed", "COMPLETED", "FAILURE"),
                ("canceled", "COMPLETED", "CANCELLED"), ("skipped", "COMPLETED", "SKIPPED")]:
            rollup, _ = self.rollup(jobs, {"id": 9, "sha": "abc", "status": state})
            self.assertEqual(len(rollup), 2)
            self.assertEqual((rollup[-1]["status"], rollup[-1]["conclusion"]), (status, conclusion), state)

    def test_other_sha_or_no_pipeline_is_empty(self):
        rollup, t = self.rollup([{"status": "success"}], {"id": 9, "sha": "old"})
        self.assertEqual(rollup, [])
        self.assertFalse([line for line in t.lines() if "/jobs" in line])
        self.assertEqual(self.rollup([{"status": "success"}])[0], [])

    def test_run_shape_and_list(self):
        pipe = {"id": 9, "status": "success", "sha": "abc", "ref": "feat",
                "source": "push", "web_url": "u"}
        forge, t = make({f"GET {PROJ}/pipelines/9": (200, pipe),
                         f"GET {PROJ}/pipelines?": (200, [pipe, {**pipe, "id": 10,
                                                                  "status": "running"}])})
        fields = ",".join(GitLab._RUN_FIELDS)
        self.assertEqual(forge.run_view(9, fields), {
            "databaseId": 9, "status": "completed", "conclusion": "success",
            "headSha": "abc", "headBranch": "feat", "workflowName": "pipeline",
            "event": "push", "url": "u"})
        listed = json.loads(forge.run_list_text("feat", "abc", "push", 5, fields))
        self.assertEqual([r["status"] for r in listed], ["completed", "in_progress"])
        self.assertEqual(listed[1]["conclusion"], "")
        line = t.lines()[-1]
        self.assertIn("ref=feat", line)
        self.assertIn("sha=abc", line)
        self.assertIn("per_page=5", line)
        self.assertNotIn("event", line)

    def test_failed_pipeline_reads_red(self):
        from baton import sprint
        pipes = [{"id": i, "status": st, "sha": "abc", "ref": "main", "source": src}
                 for i, (st, src) in enumerate([("failed", "push"), ("canceled", "push"),
                                                ("manual", "push"), ("success", "schedule")])]
        forge, _ = make({f"GET {PROJ}/pipelines?": (200, pipes)})
        fields = "conclusion,status,headSha,event"
        runs = json.loads(forge.run_list_text("main", "abc", "", 5, fields))
        self.assertEqual([r["conclusion"] for r in runs],
                         ["failure", "cancelled", "action_required", "success"])
        self.assertEqual(sprint.branch_state(runs[:1]), "red")
        self.assertEqual(sprint.branch_state(runs[2:3]), "red")
        only = json.loads(forge.run_list_text("main", "abc", "push", 5, fields))
        self.assertEqual([r["event"] for r in only], ["push"] * 3)

    def test_branch_head_with_slash(self):
        forge, t = make({f"GET {PROJ}/repository/branches/issue-1%2Ffoo": (200, {
            "commit": {"id": "c1", "committed_date": "d"}})})
        self.assertEqual(forge.branch_head("o", "r", "issue-1/foo"),
                         {"sha": "c1", "commit": {"committer": {"date": "d"}}})

    def test_last_push_picks_matching_branch(self):
        forge, t = make({f"GET {PROJ}/events": (200, [
            {"created_at": "2026-01-03", "push_data": {"ref": "other"}},
            {"created_at": "2026-01-02", "push_data": {"ref": "feat"}},
            {"created_at": "2026-01-01", "push_data": {"ref": "feat"}},
            {"created_at": "2026-01-04"}])})
        self.assertEqual(forge.last_push("o", "r", "feat"), "2026-01-02")
        self.assertIn("action=pushed", t.lines()[0])
        self.assertIsNone(forge.last_push("o", "r", "none"))


class MergeRequestWriteTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.body = os.path.join(self.dir.name, "body.md")
        pathlib.Path(self.body).write_text("the body")

    def sent(self, t, index):
        return json.loads(t.calls[index][3])

    def test_pr_create(self):
        forge, t = make({f"GET {PROJ}": (200, {"default_branch": "main"}),
                         f"POST {PROJ}/merge_requests": (201, {"web_url": "https://g/mr/1"})})
        self.assertEqual(forge.pr_create("T", self.body, head="feat"), "https://g/mr/1\n")
        self.assertEqual(self.sent(t, 1), {"source_branch": "feat", "target_branch": "main",
                                           "title": "T", "description": "the body"})

    def test_pr_create_head_from_runner(self):
        forge, t = make({f"GET {PROJ}": (200, {"default_branch": "main"}),
                         f"POST {PROJ}/merge_requests": (201, {"web_url": "u"})})
        forge.runner = lambda argv: "topic\n"
        forge.pr_create("T", self.body)
        self.assertEqual(self.sent(t, 1)["source_branch"], "topic")

    def test_draft_raises_without_request(self):
        forge, t = make()
        with self.assertRaises(Unsupported):
            forge.pr_create("T", self.body, draft=True)
        self.assertEqual(t.calls, [])

    def test_pr_merge_sends_sha(self):
        forge, t = make({f"PUT {PROJ}/merge_requests/4/merge": (200, {})})
        forge.pr_merge(4, "abc")
        self.assertEqual(t.lines(), [f"PUT {PROJ}/merge_requests/4/merge"])
        self.assertEqual(self.sent(t, 0), {"sha": "abc"})

    def test_approve_posts_note_then_approves_head_sha(self):
        forge, t = make({f"POST {PROJ}/merge_requests/4/notes": (201, {"id": 1}),
                         f"GET {PROJ}/merge_requests/4": (200, {"sha": "head1"}),
                         f"POST {PROJ}/merge_requests/4/approve": (201, {})})
        forge.pr_review(4, "approve", self.body)
        self.assertEqual(t.lines(), [f"POST {PROJ}/merge_requests/4/notes",
                                     f"GET {PROJ}/merge_requests/4",
                                     f"POST {PROJ}/merge_requests/4/approve"])
        self.assertEqual(self.sent(t, 0), {"body": "the body"})
        self.assertEqual(self.sent(t, 2), {"sha": "head1"})

    def test_comment_only_posts_note_and_unknown_event_raises(self):
        forge, t = make({f"POST {PROJ}/merge_requests/4/notes": (201, {"id": 1})})
        forge.pr_review(4, "request-changes", self.body)
        self.assertEqual(t.lines(), [f"POST {PROJ}/merge_requests/4/notes"])
        with self.assertRaises(Unsupported):
            forge.pr_review(4, "dismiss", self.body)

    def test_approve_conflict_raises(self):
        forge, t = make({f"POST {PROJ}/merge_requests/4/notes": (201, {"id": 1}),
                         f"GET {PROJ}/merge_requests/4": (200, {"sha": "h"}),
                         f"POST {PROJ}/merge_requests/4/approve": (409, {"message": "sha"})})
        with self.assertRaises(GitLabError):
            forge.pr_review(4, "approve", self.body)

    def test_review_reads_are_empty(self):
        forge, t = make()
        self.assertEqual(forge.review_urls(4, "x"), [])
        self.assertEqual(forge.pr_review_items(4), [])
        self.assertEqual(t.calls, [])

    def test_review_request_remove_keeps_others(self):
        forge, t = make({f"GET {PROJ}/merge_requests/4": (200, {"reviewers": [
            {"id": 1, "username": "a"}, {"id": 2, "username": "b"}]}),
            f"PUT {PROJ}/merge_requests/4": (200, {})})
        forge.review_request_remove(4, "a")
        self.assertEqual(self.sent(t, 1), {"reviewer_ids": [2]})

    def test_review_request_remove_not_a_reviewer(self):
        forge, t = make({f"GET {PROJ}/merge_requests/4": (200, {"reviewers": [
            {"id": 1, "username": "a"}]})})
        forge.review_request_remove(4, "z")
        self.assertEqual(t.lines(), [f"GET {PROJ}/merge_requests/4"])


if __name__ == "__main__":
    unittest.main()
