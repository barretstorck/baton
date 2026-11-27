import json
import os
import pathlib
import unittest
from unittest import mock

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
                     lambda: forge.review_edit_body(1, 2, "p"),
                     lambda: forge.pr_ready(1)):
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
             "due_on": "2026-01-01T00:00:00Z"}])})
        self.assertEqual(forge.milestones(), [{"number": 9, "title": "M", "description": "d",
                                               "state": "open", "due_on": "2026-01-01T00:00:00Z"}])
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
