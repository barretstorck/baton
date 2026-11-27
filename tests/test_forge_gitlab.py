import os
import pathlib
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


if __name__ == "__main__":
    unittest.main()
