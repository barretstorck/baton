import contextlib
import datetime
import io
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from baton import doctor, identity

TOML = """
[baton]
app_id = 1
installation_id = 10
key_path = "{key}"

[reviewer]
app_id = 2
installation_id = 20
key_path = "{key}"
"""
TOML_MACHINE = TOML + '\n[machine]\nlogin = "baton-user"\n'
REPO = "o/r"


class DoctorTest(unittest.TestCase):
    def setUp(self):
        identity._cache.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name) / "o-r"
        self.dir.mkdir()
        self.key = pathlib.Path(self.tmp.name) / "secret-key.pem"
        self.key.write_text("KEY-CONTENT")
        for target, value in (("jwt", "jwt-secret"), ("token", "tok-secret")):
            p = mock.patch.object(identity, target, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        self.slugs = ["baton", "baton-reviewer"]
        self.repos = [{"full_name": REPO}]
        self.status_urls = []

    def write(self, text=None):
        (self.dir / "identity.toml").write_text((text or TOML).format(key=self.key))

    def status(self, url, headers):
        self.status_urls.append(url)
        return 204

    def get(self, url, headers):
        if url.endswith("/app"):
            return {"slug": self.slugs.pop(0)}
        return {"total_count": len(self.repos), "repositories": self.repos}

    def run_main(self, get=None, status=None, spike=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = doctor.main(REPO, runner=None, get=get or self.get, post=None, now=1000,
                               home=self.tmp.name, spike=spike, status=status or self.status)
        text = out.getvalue()
        for secret in ("jwt-secret", "tok-secret", "KEY-CONTENT", str(self.key)):
            self.assertNotIn(secret, text)
        return code, text

    def test_spike_warning_tier_zero(self):
        code, text = self.run_main(spike={"tokens": {}, "minutes": {}, "max_per_sprint": 2})
        self.assertEqual(code, 0)
        self.assertEqual(text, "tier: 0\n" + doctor.SPIKE_WARNING + "\n")
        self.assertIn("spike: WARN: spike.tokens and spike.minutes have no range", text)

    def test_spike_ranges_no_warning(self):
        for sp in ({"tokens": {"max": 500000}, "minutes": {}, "max_per_sprint": 2},
                   {"tokens": {}, "minutes": {"min": 30, "max": 120}, "max_per_sprint": 2}):
            self.assertEqual(self.run_main(spike=sp), (0, "tier: 0\n"))

    def test_spike_warning_with_bad_identity(self):
        self.write("[baton\n")
        code, text = self.run_main(spike={"tokens": {}, "minutes": {}})
        self.assertEqual(code, 1)
        self.assertIn("identity.toml:", text)
        self.assertIn(doctor.SPIKE_WARNING, text)

    def test_tier_zero(self):
        self.assertEqual(self.run_main(), (0, "tier: 0\n"))

    def test_both_ok(self):
        self.write()
        code, text = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("tier: 1", text)
        self.assertIn("baton: ok (baton[bot], can read o/r)", text)
        self.assertIn("reviewer: ok (baton-reviewer[bot], can read o/r)", text)
        self.assertNotIn("machine:", text)
        self.assertEqual(self.status_urls, [])

    def test_tier_two_ok(self):
        self.write(TOML_MACHINE)
        code, text = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("tier: 2", text)
        self.assertIn("machine: ok (baton-user can be assigned on o/r)", text)
        self.assertTrue(self.status_urls[0].endswith("/repos/o/r/assignees/baton-user"))

    def test_machine_not_assignable(self):
        self.write(TOML_MACHINE)
        code, text = self.run_main(status=lambda url, headers: 404)
        self.assertEqual(code, 1)
        self.assertIn("machine: FAIL: baton-user cannot be assigned on o/r (HTTP 404); "
                      "invite it as a collaborator and accept the invitation", text)

    def test_machine_request_error(self):
        self.write(TOML_MACHINE)

        def bad(url, headers):
            raise identity.IdentityError("request failed")
        code, text = self.run_main(status=bad)
        self.assertEqual(code, 1)
        self.assertIn("machine: FAIL: request failed", text)

    def test_login_mismatch(self):
        self.write()
        self.slugs[0] = "other"
        code, text = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("baton: FAIL: login is other[bot] on GitHub, identity.toml says baton[bot]", text)

    def test_get_error(self):
        self.write()

        def bad(url, headers):
            raise identity.IdentityError("request failed: HTTP 401")
        code, text = self.run_main(bad)
        self.assertEqual(code, 1)
        self.assertIn("baton: FAIL: request failed: HTTP 401", text)

    def test_repo_missing(self):
        self.write()
        self.repos = [{"full_name": "o/other"}]
        code, text = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("baton: FAIL: the installation does not include o/r", text)

    def test_malformed(self):
        self.write("not = [valid")
        code, text = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("tier: unknown", text)
        self.assertIn("identity.toml:", text)


if __name__ == "__main__":
    unittest.main()


class ForgejoProbeTest(unittest.TestCase):
    def test_ok(self):
        seen = []

        def get(url, headers):
            seen.append(url)
            return {"version": "13.0.5+gitea-1.22.0"}
        lines, ok = doctor.forgejo_lines("https://f.example/", get)
        self.assertTrue(ok)
        self.assertEqual(lines, ["forge: ok (Forgejo 13.0.5+gitea-1.22.0)"])
        self.assertEqual(seen, ["https://f.example/api/forgejo/v1/version"])

    def test_http_404(self):
        def get(url, headers):
            raise identity.IdentityError("request failed: HTTP 404")
        lines, ok = doctor.forgejo_lines("https://f.example", get)
        self.assertFalse(ok)
        self.assertIn("forge: FAIL: forge.url does not answer /api/forgejo/v1/version", lines[0])
        self.assertIn("HTTP 404", lines[0])

    def test_missing_version(self):
        lines, ok = doctor.forgejo_lines("https://f.example", lambda u, h: {"x": 1})
        self.assertFalse(ok)
        self.assertTrue(lines[0].startswith("forge: FAIL: "))


class HttpGetRedirectTest(unittest.TestCase):
    def test_redirect_not_followed_and_token_not_resent(self):
        import http.server
        import threading
        seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.server.server_port, self.path, self.headers.get("PRIVATE-TOKEN")))
                if self.path == "/start":
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{other.server_port}/second")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        other = http.server.HTTPServer(("127.0.0.1", 0), H)
        for s in (srv, other):
            threading.Thread(target=s.serve_forever, daemon=True).start()
            self.addCleanup(s.server_close)
            self.addCleanup(s.shutdown)
        status, headers, _ = doctor.http_get(
            f"http://127.0.0.1:{srv.server_port}/start", {"PRIVATE-TOKEN": "sekret"})
        self.assertEqual(status, 302)
        self.assertIn("location", headers)
        self.assertEqual(seen, [(srv.server_port, "/start", "sekret")])


class GitLabProbeTest(unittest.TestCase):
    def fake(self, status=200, headers=None, body=b""):
        self.seen = []

        def get(url, h):
            self.seen.append((url, dict(h)))
            return status, {"x-gitlab-meta": "{}"} if headers is None else headers, body
        return get

    def test_no_header_fails(self):
        lines, ok = doctor.gitlab_lines("https://g.example", "tok", self.fake(headers={}))
        self.assertFalse(ok)
        self.assertIn("X-Gitlab-Meta", lines[0])
        self.assertNotIn("tok", lines[0])

    def test_no_reply_fails(self):
        lines, ok = doctor.gitlab_lines("https://g.example", None, lambda u, h: (None, {}, b""))
        self.assertFalse(ok)

    def test_no_token_warns(self):
        lines, ok = doctor.gitlab_lines("https://g.example/", None, self.fake(status=401))
        self.assertTrue(ok)
        self.assertEqual(lines, ["forge: WARN: GitLab g.example; version not checked: no token configured"])
        self.assertEqual(self.seen[0][0], "https://g.example/api/v4/version")
        self.assertNotIn("PRIVATE-TOKEN", self.seen[0][1])

    def test_ok_with_plan(self):
        lines, ok = doctor.gitlab_lines("https://g.example", "sekret",
                                        self.fake(body=b'{"version": "19.6.2", "plan": "free"}'))
        self.assertTrue(ok)
        self.assertEqual(lines, ["forge: ok (GitLab 19.6.2, plan free)"])
        self.assertEqual(self.seen[0][1]["PRIVATE-TOKEN"], "sekret")
        self.assertEqual(self.seen[0][1]["Accept"], "application/json")

    def test_floor_is_ok(self):
        lines, ok = doctor.gitlab_lines("https://g.example", "t", self.fake(body=b'{"version": "17.0.0"}'))
        self.assertTrue(ok)
        self.assertIn("plan unknown", lines[0])

    def test_below_minimum(self):
        lines, ok = doctor.gitlab_lines("https://g.example", "t", self.fake(body=b'{"version": "16.11.3"}'))
        self.assertFalse(ok)
        self.assertIn("below the minimum 17.0", lines[0])

    def test_unparseable_version(self):
        for body in (b"nope", b'{"x": 1}', b'{"version": "abc"}'):
            lines, ok = doctor.gitlab_lines("https://g.example", "t", self.fake(body=body))
            self.assertFalse(ok)
            self.assertTrue(lines[0].startswith("forge: FAIL: "))

    def test_401_with_token_fails_without_leaking(self):
        lines, ok = doctor.gitlab_lines("https://g.example", "sekret", self.fake(status=401))
        self.assertFalse(ok)
        self.assertIn("HTTP 401", lines[0])
        self.assertNotIn("sekret", "".join(lines))


class GitlabExpiryLinesTest(unittest.TestCase):
    TODAY = datetime.date(2026, 11, 27)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = pathlib.Path(tmp.name)

    def setup_tables(self, names=("baton", "reviewer")):
        folder = self.home / "o-r"
        folder.mkdir(parents=True, exist_ok=True)
        text = ""
        for name in names:
            tok = self.home / f"{name}.tok"
            tok.write_text(f"SECRET-{name}\n")
            text += f'[gitlab.{name}]\ntoken_path = "{tok}"\n'
        (folder / "identity.toml").write_text(text)

    def fake(self, status=200, expires="2026-12-27"):
        self.seen = []

        def get(url, h):
            self.seen.append((url, dict(h)))
            return status, {}, json.dumps({"expires_at": expires}).encode()
        return get

    def lines(self, get):
        return doctor.gitlab_expiry_lines("https://g.example/", "o", "r", self.TODAY, get=get, home=self.home)

    def test_thirty_days_warns(self):
        self.setup_tables(("baton",))
        out = self.lines(self.fake(expires="2026-12-27"))
        self.assertEqual(out, ["token: WARN: [gitlab.baton] expires 2026-12-27 (in 30 days); "
                               "run baton identity rotate"])
        self.assertEqual(self.seen[0][0], "https://g.example/api/v4/personal_access_tokens/self")
        self.assertEqual(self.seen[0][1]["PRIVATE-TOKEN"], "SECRET-baton")

    def test_twenty_nine_days_warns(self):
        self.setup_tables(("baton",))
        self.assertIn("in 29 days", self.lines(self.fake(expires="2026-12-26"))[0])

    def test_thirty_one_days_and_null_print_nothing(self):
        self.setup_tables(("baton",))
        self.assertEqual(self.lines(self.fake(expires="2026-12-28")), [])
        self.assertEqual(self.lines(self.fake(expires=None)), [])

    def test_401(self):
        self.setup_tables(("baton",))
        self.assertEqual(self.lines(self.fake(status=401)),
                         ["token: WARN: [gitlab.baton] was refused (HTTP 401); "
                          "it may have expired: renew it by hand"])

    def test_500(self):
        self.setup_tables(("baton",))
        self.assertEqual(self.lines(self.fake(status=500)),
                         ["token: WARN: [gitlab.baton] expiry not checked (HTTP 500)"])

    def test_unreadable_reply(self):
        self.setup_tables(("baton",))
        out = self.lines(lambda u, h: (200, {}, b"nope"))
        self.assertEqual(out, ["token: WARN: [gitlab.baton] expiry not checked (HTTP 200)"])

    def test_no_tables(self):
        self.assertEqual(self.lines(self.fake()), [])

    def test_missing_token_file(self):
        self.setup_tables(("baton",))
        (self.home / "baton.tok").unlink()
        out = self.lines(self.fake())
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith("token: WARN: cannot read"))

    def test_invalid_identity_toml(self):
        (self.home / "o-r").mkdir()
        (self.home / "o-r" / "identity.toml").write_text("[[[")
        out = self.lines(self.fake())
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith("token: WARN: "))

    def test_token_in_no_line(self):
        self.setup_tables()
        for get in (self.fake(), self.fake(status=401), self.fake(status=500)):
            self.assertNotIn("SECRET", "\n".join(self.lines(get)))


class CmdDoctorForgeTest(unittest.TestCase):
    def run_doctor(self, origin, local=None, gl_ok=True, token="tok", token_error=False):
        from baton import cli
        from baton import identity
        token_kw = ({"side_effect": identity.IdentityError("none")} if token_error
                    else {"return_value": token})
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        brief = pathlib.Path(tmp.name) / "brief.local.md"
        if local:
            brief.write_text(local)
        out = io.StringIO()
        with mock.patch.object(cli.run, "run", return_value=origin + "\n") as run_, \
                mock.patch.object(cli.config, "load", return_value=None), \
                mock.patch.object(cli.sandbox, "brief_path", return_value=brief), \
                mock.patch.object(cli.doctor, "forgejo_lines",
                                  return_value=(["forge: ok (Forgejo 1)"], True)) as fl, \
                mock.patch.object(cli.doctor, "gitlab_lines",
                                  return_value=(["forge: ok (GitLab 17.0)"], gl_ok)) as self.gl, \
                mock.patch.object(cli.doctor, "gitlab_expiry_lines",
                                  return_value=["token: WARN: [gitlab.baton] expires soon"]) as self.ex, \
                mock.patch.object(cli.identity, "forge_token", **token_kw) as self.ft, \
                mock.patch.object(cli.doctor, "main", return_value=0) as main, \
                mock.patch.object(cli, "_repo_name", return_value="o/r"), \
                contextlib.redirect_stdout(out):
            code = cli.cmd_doctor([])
        return code, out.getvalue(), run_, fl, main

    def test_forgejo_origin(self):
        code, out, run_, fl, main = self.run_doctor(
            "https://f.example/o/r.git", "## Hosts\n\nforge: forgejo https://f.example\n")
        self.assertEqual(code, 0)
        self.assertIn("forge: ok (Forgejo 1)", out)
        fl.assert_called_once_with("https://f.example")
        main.assert_not_called()
        for call in run_.call_args_list:
            self.assertNotEqual(call.args[0][0], "gh")

    def test_forgejo_prints_github_only_skips(self):
        code, out, _, _, _ = self.run_doctor(
            "https://f.example/o/r.git", "## Hosts\n\nforge: forgejo https://f.example\n")
        self.assertEqual(code, 0)
        for line in doctor.GITHUB_ONLY_SKIPS:
            self.assertIn(line, out)

    def test_gitlab_origin(self):
        code, out, run_, fl, main = self.run_doctor(
            "https://gl.example/a/b/proj.git", "## Hosts\n\nforge: gitlab https://gl.example\n")
        self.assertEqual(code, 0)
        self.assertIn("forge: ok (GitLab 17.0)", out)
        self.gl.assert_called_once_with("https://gl.example", "tok")
        self.ft.assert_called_once_with("gitlab", "a/b", "proj")
        for line in doctor.GITHUB_ONLY_SKIPS:
            self.assertIn(line, out)
        main.assert_not_called()
        fl.assert_not_called()
        for call in run_.call_args_list:
            self.assertNotEqual(call.args[0][0], "gh")

    def test_gitlab_expiry_line_printed_and_exit_stays_zero(self):
        code, out, _, _, _ = self.run_doctor(
            "https://gl.example/a/b/proj.git", "## Hosts\n\nforge: gitlab https://gl.example\n")
        self.assertEqual(code, 0)
        self.assertIn("token: WARN: [gitlab.baton] expires soon", out)
        args = self.ex.call_args.args
        self.assertEqual(args[:3], ("https://gl.example", "a/b", "proj"))

    def test_gitlab_com(self):
        code, _, _, _, _ = self.run_doctor("https://gitlab.com/g/p.git")
        self.assertEqual(code, 0)
        self.gl.assert_called_once_with("https://gitlab.com", "tok")

    def test_gitlab_no_token(self):
        code, _, _, _, _ = self.run_doctor("https://gitlab.com/g/p.git", token_error=True)
        self.assertEqual(code, 0)
        self.gl.assert_called_once_with("https://gitlab.com", None)

    def test_gitlab_failing_probe(self):
        code, _, _, _, main = self.run_doctor("https://gitlab.com/g/p.git", gl_ok=False)
        self.assertEqual(code, 1)
        main.assert_not_called()

    def test_gitlab_url_with_other_port_or_scheme_fails(self):
        for url in ("http://gl.example", "https://gl.example:8443"):
            code, out, _, _, main = self.run_doctor(
                "https://gl.example/a/p.git", f"## Hosts\n\nforge: gitlab {url}\n")
            self.assertEqual(code, 1)
            self.assertTrue(out.startswith("forge: FAIL: "), out)
            self.gl.assert_not_called()
            self.ft.assert_not_called()
            main.assert_not_called()

    def test_github_prints_no_skip_lines(self):
        code, out, _, _, _ = self.run_doctor("git@github.com:o/r.git")
        self.assertEqual(code, 0)
        for line in doctor.GITHUB_ONLY_SKIPS:
            self.assertNotIn(line, out)

    def test_unknown_host(self):
        code, out, _, fl, main = self.run_doctor("https://other.example/o/r")
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("forge: FAIL: "))
        main.assert_not_called()

    def test_github(self):
        code, _, _, fl, main = self.run_doctor("git@github.com:o/r.git")
        self.assertEqual(code, 0)
        main.assert_called_once()
        fl.assert_not_called()
