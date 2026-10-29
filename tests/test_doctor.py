import contextlib
import io
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

    def write(self, text=None):
        (self.dir / "identity.toml").write_text(text or TOML.format(key=self.key))

    def get(self, url, headers):
        if url.endswith("/app"):
            return {"slug": self.slugs.pop(0)}
        return {"total_count": len(self.repos), "repositories": self.repos}

    def run_main(self, get=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = doctor.main(REPO, runner=None, get=get or self.get, post=None, now=1000,
                               home=self.tmp.name)
        text = out.getvalue()
        for secret in ("jwt-secret", "tok-secret", "KEY-CONTENT", str(self.key)):
            self.assertNotIn(secret, text)
        return code, text

    def test_tier_zero(self):
        self.assertEqual(self.run_main(), (0, "tier: 0\n"))

    def test_both_ok(self):
        self.write()
        code, text = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("tier: 1", text)
        self.assertIn("baton: ok (baton[bot], can read o/r)", text)
        self.assertIn("reviewer: ok (baton-reviewer[bot], can read o/r)", text)

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
