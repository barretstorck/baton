import base64
import calendar
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from baton import identity
from baton.run import run

VALID = """
[baton]
app_id = 1
installation_id = 10
key_path = "/k/baton.pem"

[reviewer]
app_id = 2
installation_id = 20
key_path = "/k/rev.pem"
login = "custom[bot]"
"""


AT = "@"
SCP = "g" + "it" + AT + "github.com:"


class LoadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name) / "o-r"
        self.dir.mkdir()

    def load(self, text=None):
        if text is not None:
            (self.dir / "identity.toml").write_text(text)
        return identity.load("o", "r", home=self.tmp.name)

    def test_absent(self):
        self.assertIsNone(self.load())

    def test_valid(self):
        ids = self.load(VALID)
        self.assertEqual(ids.baton, identity.App(1, 10, "/k/baton.pem", "baton[bot]"))
        self.assertEqual(ids.reviewer.login, "custom[bot]")
        self.assertEqual(ids.logins(), ["baton[bot]", "custom[bot]"])

    def test_only_one_entry(self):
        ids = self.load('[reviewer]\napp_id = 2\ninstallation_id = 3\nkey_path = "k"\n')
        self.assertIsNone(ids.baton)
        self.assertEqual(ids.logins(), ["baton-reviewer[bot]"])

    def test_missing_key(self):
        with self.assertRaisesRegex(identity.IdentityError, "missing key_path"):
            self.load("[baton]\napp_id = 1\ninstallation_id = 2\n")

    def test_bad_toml(self):
        with self.assertRaisesRegex(identity.IdentityError, "not valid TOML"):
            self.load("[baton\n")

    def test_wrong_types(self):
        for text in ('[baton]\napp_id = "1"\ninstallation_id = 2\nkey_path = "k"\n',
                     '[baton]\napp_id = 1\ninstallation_id = 2\nkey_path = 5\n',
                     '[baton]\napp_id = 1\ninstallation_id = 2\nkey_path = "k"\nlogin = 3\n',
                     'baton = 3\n', '[other]\nx = 1\n'):
            with self.assertRaises(identity.IdentityError, msg=text):
                self.load(text)


if __name__ == "__main__":
    unittest.main()


class TokenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.key = pathlib.Path(cls.tmp.name) / "k.pem"
        cls.pub = pathlib.Path(cls.tmp.name) / "p.pem"
        subprocess.run(["openssl", "genrsa", "-out", str(cls.key), "2048"], check=True,
                       capture_output=True)
        subprocess.run(["openssl", "rsa", "-in", str(cls.key), "-pubout", "-out", str(cls.pub)],
                       check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        identity._cache.clear()
        self.app = identity.App(7, 70, str(self.key), "x[bot]")

    def test_jwt_verifies(self):
        now = 1_000_000
        signed = identity.jwt(self.app, run, now)
        head, claims, sig = signed.split(".")
        pad = lambda s: s + "=" * (-len(s) % 4)
        self.assertEqual(json.loads(base64.urlsafe_b64decode(pad(head))),
                         {"alg": "RS256", "typ": "JWT"})
        self.assertEqual(json.loads(base64.urlsafe_b64decode(pad(claims))),
                         {"iat": now - 60, "exp": now + 540, "iss": 7})
        sigfile = pathlib.Path(self.tmp.name) / "sig"
        sigfile.write_bytes(base64.urlsafe_b64decode(pad(sig)))
        proc = subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(self.pub),
                               "-signature", str(sigfile)], input=f"{head}.{claims}".encode(),
                              capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_missing_key_is_identity_error(self):
        app = identity.App(7, 70, "/nonexistent/key.pem", "x")
        with self.assertRaises(identity.IdentityError):
            identity.jwt(app, run, 1000)

    def test_token_is_memoised(self):
        calls = []

        def post(url, headers):
            calls.append((url, headers))
            return {"token": "ghs_" + "a" * 30, "expires_at": "2026-01-01T01:00:00Z"}
        now = calendar.timegm((2026, 1, 1, 0, 0, 0))
        first = identity.token(self.app, run, post, now)
        self.assertEqual(identity.token(self.app, run, post, now + 3000), first)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][0].endswith("/app/installations/70/access_tokens"))
        self.assertTrue(calls[0][1]["Authorization"].startswith("Bearer "))
        identity.token(self.app, run, post, now + 3560)
        self.assertEqual(len(calls), 2)

    def test_failing_post_leaks_nothing(self):
        def post(url, headers):
            raise RuntimeError("boom " + headers["Authorization"])
        with self.assertRaises(identity.IdentityError) as cm:
            identity.token(self.app, run, post, 1000)
        self.assertNotIn("eyJ", str(cm.exception))
        self.assertNotIn("boom", str(cm.exception))


class WriteTest(unittest.TestCase):
    def setUp(self):
        from tests.fakes import FakeRunner
        self.runner = FakeRunner()
        self.minted = []
        self._token = identity.token
        identity.token = lambda app, runner, post, now: (self.minted.append(app.app_id) or f"tok{app.app_id}")
        self.ids = identity.Identities(identity.App(1, 11, "k", "baton[bot]"),
                                       identity.App(2, 22, "k", "baton-reviewer[bot]"))

    def tearDown(self):
        identity.token = self._token
        identity.use(None)

    def test_route(self):
        self.assertEqual(identity.route("reviewer"), "reviewer")
        for role in ("developer", "orchestrator", "refine", "triager", "researcher"):
            self.assertEqual(identity.route(role), "baton")

    def test_tier0_has_no_env(self):
        identity.use(lambda: None)
        identity.write(self.runner, ["gh", "x"], "developer", cwd="d")
        self.assertEqual(self.runner.envs, [None])
        self.assertEqual(self.minted, [])

    def test_tier1_routes_token(self):
        identity.use(lambda: self.ids)
        identity.write(self.runner, ["gh", "x"], "developer")
        identity.write(self.runner, ["gh", "y"], "reviewer")
        self.assertEqual([e["GH_TOKEN"] for e in self.runner.envs], ["tok1", "tok2"])
        self.assertEqual(self.runner.envs[0]["GH_PROMPT_DISABLED"], "1")

    def test_loader_called_once(self):
        calls = []
        identity.use(lambda: calls.append(1) or self.ids)
        identity.write(self.runner, ["gh"], "developer")
        identity.write(self.runner, ["gh"], "developer")
        self.assertEqual(len(calls), 1)

    def test_missing_entry_raises(self):
        identity.use(lambda: identity.Identities(baton=self.ids.baton))
        with self.assertRaises(identity.IdentityError):
            identity.write(self.runner, ["gh"], "reviewer")
        self.assertEqual(self.runner.calls, [])

    def test_failed_mint_never_runs(self):
        def boom(*a):
            raise identity.IdentityError("nope")
        identity.token = boom
        identity.use(lambda: self.ids)
        with self.assertRaises(identity.IdentityError):
            identity.write(self.runner, ["gh"], "developer")
        self.assertEqual(self.runner.calls, [])


class DescribeTest(unittest.TestCase):
    def test_tier_zero(self):
        out = identity.describe(None)
        self.assertEqual(out["tier"], 0)
        self.assertEqual(out["roles"]["reviewer"], {"login": None, "route": "maintainer"})
        self.assertIn("orchestrator", out["roles"])

    def test_tier_one_has_nothing_secret(self):
        ids = identity.Identities(identity.App(11, 22, "/secret/key.pem", "baton[bot]"),
                                  identity.App(33, 44, "/secret/r.pem", "baton-reviewer[bot]"))
        out = identity.describe(ids)
        self.assertEqual(out["tier"], 1)
        self.assertEqual(out["roles"]["reviewer"], {"login": "baton-reviewer[bot]", "route": "reviewer"})
        self.assertEqual(out["roles"]["developer"], {"login": "baton[bot]", "route": "baton"})
        text = json.dumps(out)
        for bad in ("secret", "11", "22", "33", "44", ".pem"):
            self.assertNotIn(bad, text)


class GitIdentityTest(unittest.TestCase):
    def setUp(self):
        from unittest import mock
        from tests.fakes import FakeRunner
        self.runner = FakeRunner()
        self.ids = identity.Identities(identity.App(1, 11, "k", "baton[bot]"))
        p = mock.patch.object(identity, "token", lambda *a: "tok-baton")
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(identity.use, None)

    def test_refspec_problem(self):
        ok = lambda ref: True
        self.assertIsNone(identity.refspec_problem("HEAD:refs/heads/b", "main", ok))
        self.assertIsNone(identity.refspec_problem("HEAD:refs/heads/issue-258-x", "main", ok))
        for bad in ["--delete", "-o", "--receive-pack=x", "--mirror", "+HEAD:refs/heads/b",
                    ":refs/heads/b", "HEAD", "a:b:c", "HEAD:main", "HEAD:refs/tags/v1",
                    "HEAD:refs/heads/", "HEAD:refs/heads/main",
                    "refs/heads/*:refs/heads/*", "HEAD:refs/heads/*", "HEAD*:refs/heads/b",
                    "HEAD:refs/heads/a^b", "HEAD^:refs/heads/b", "HEAD:refs/heads/a?b",
                    "HEAD:refs/heads/a[b", "HEAD:refs/heads/a\\b", "HEAD:refs/heads/a b",
                    "HEAD\t:refs/heads/b"]:
            with self.subTest(refspec=bad):
                self.assertIsInstance(identity.refspec_problem(bad, "main", ok), str)
        self.assertIsNone(identity.refspec_problem("HEAD:refs/heads/main", "trunk", ok))

    def test_refspec_problem_ref_ok(self):
        seen = []

        def no(ref):
            seen.append(ref)
            return False
        msg = identity.refspec_problem("HEAD:refs/heads/main", "main", no)
        self.assertIn("check-ref-format", msg)
        self.assertEqual(seen, ["refs/heads/main"])

    def test_ref_format_ok(self):
        from tests.fakes import FakeRunner
        runner = FakeRunner()
        with mock.patch.object(identity._run, "run", runner):
            self.assertTrue(identity.ref_format_ok("refs/heads/b"))
        self.assertEqual(runner.calls, [["git", "check-ref-format", "refs/heads/b"]])

        def boom(*a, **k):
            raise identity._run.RunError("bad")
        with mock.patch.object(identity._run, "run", boom):
            self.assertFalse(identity.ref_format_ok("refs/heads/b"))

    def test_github_repo(self):
        for url, want in [("https://github.com/o/r", "o/r"), ("https://github.com/o/r.git", "o/r"),
                          ("https://github.com/o/r.git/", "o/r"), ("https://u:pw" + AT + "github.com/o/r.git", "o/r"),
                          ("ssh://git" + AT + "github.com/o/r.git", "o/r"), ("ssh://git" + AT + "github.com:22/o/r", "o/r"),
                          (SCP + "o/r.git", "o/r"), ("../r.git", None),
                          ("https://gitlab.com/o/r.git", None), ("https://evilgithub.com/o/r", None),
                          ("", None)]:
            with self.subTest(url=url):
                self.assertEqual(identity.github_repo(url), want)

    def test_origin_problem(self):
        self.assertIsNone(identity.origin_problem(SCP + "O/R.git", "o/r"))
        self.assertIn("fork/r", identity.origin_problem(SCP + "fork/r.git", "o/r"))
        self.assertIn("not a GitHub", identity.origin_problem("https://gitlab.com/o/r.git", "o/r"))
        msg = identity.origin_problem("https://user:secret" + AT + "github.com/fork/r.git", "o/r")
        self.assertNotIn("secret", msg)
        self.assertNotIn("user", msg)

    def test_tier0(self):
        identity.use(lambda: None)
        self.assertEqual(identity.push_command("HEAD:refs/heads/b", "o/r"),
                         (["git", "push", "--", "origin", "HEAD:refs/heads/b"], None))
        self.assertIsNone(identity.commit_identity(self.runner))
        self.assertEqual(self.runner.calls, [])

    def test_tier1_push_keeps_token_out_of_argv(self):
        identity.use(lambda: self.ids)
        argv, env = identity.push_command("HEAD:refs/heads/b", "o/r")
        self.assertIn("https://github.com/o/r.git", argv)
        self.assertEqual(argv[-3:], ["--", "https://github.com/o/r.git", "HEAD:refs/heads/b"])
        self.assertEqual(argv[1:4], ["-c", "credential.helper=", "-c"])
        self.assertFalse(any("tok-baton" in a for a in argv))
        self.assertEqual(env, {"BATON_GIT_TOKEN": "tok-baton", "GIT_TERMINAL_PROMPT": "0"})

    def test_commit_identity(self):
        from tests.fakes import FakeRunner
        identity.use(lambda: self.ids)
        runner = FakeRunner({"gh api users/baton[bot]": '{"id": 42}'})
        self.assertEqual(identity.commit_identity(runner),
                         ("baton[bot]", "42+baton[bot]@users.noreply.github.com"))

    def test_missing_id_raises(self):
        from tests.fakes import FakeRunner
        identity.use(lambda: self.ids)
        for reply in ('{"login": "x"}', '{"id": "7"}', 'nope', ''):
            with self.assertRaises(identity.IdentityError) as cm:
                identity.commit_identity(FakeRunner({"gh api": reply}))
            self.assertIn("baton[bot]", str(cm.exception))


class ActualLoginTest(unittest.TestCase):
    app = identity.App(7, 70, "/k/x.pem", "x[bot]")

    def test_app_login(self):
        sent = []

        def get(url, headers):
            sent.append((url, headers))
            return {"slug": "baton"}
        with mock.patch.object(identity, "jwt", return_value="jwt-x"):
            self.assertEqual(identity.app_login(self.app, None, get, 0), "baton[bot]")
        self.assertEqual(sent[0][0], "https://api.github.com/app")
        self.assertEqual(sent[0][1]["Authorization"], "Bearer jwt-x")

    def test_no_slug(self):
        with mock.patch.object(identity, "jwt", return_value="j"):
            with self.assertRaisesRegex(identity.IdentityError, "no slug"):
                identity.app_login(self.app, None, lambda u, h: {}, 0)

    def test_installation_repos_pages(self):
        pages = {1: {"total_count": 101, "repositories": [{"full_name": f"o/r{i}"} for i in range(100)]},
                 2: {"total_count": 101, "repositories": [{"full_name": "o/last"}]}}
        urls = []

        def get(url, headers):
            urls.append(url)
            self.assertEqual(headers["Authorization"], "Bearer tok")
            return pages[int(url.rsplit("=", 1)[1])]
        names = identity.installation_repos("tok", get)
        self.assertEqual(len(names), 101)
        self.assertIn("o/last", names)
        self.assertEqual(len(urls), 2)
