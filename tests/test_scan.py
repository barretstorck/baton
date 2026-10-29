import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock
from baton import scan


def kinds(text, terms=(), allow=()):
    return [f.kind for f in scan.scan_text(text, list(terms), list(allow))]


class ScanTest(unittest.TestCase):
    def test_clean_text_passes(self):
        self.assertEqual(kinds("Refactor the resample loop.\nCloses #12"), [])

    def test_private_ip(self):
        self.assertEqual(kinds("server at 192.168.7.7 today"), ["private-ip"])
        self.assertEqual(kinds("10.0.0.5"), ["private-ip"])
        self.assertEqual(kinds("172.20.1.1"), ["private-ip"])

    def test_public_ip_and_versions_pass(self):
        self.assertEqual(kinds("8.8.8.8 and version 10.2.1"), [])

    def test_skip_leaves_out_a_kind(self):
        version = ".".join(["10", "0", "0", "5"])
        text = f"nvidia-cublas {version}\nmail alice" + "@corp-mail.test"
        found = [f.kind for f in scan.scan_text(text, [], [], skip={"private-ip"})]
        self.assertEqual(found, ["email"])
        self.assertEqual(kinds(version), ["private-ip"])

    def test_skips_private_ip_by_basename(self):
        for path in ("uv.lock", "sub/dir/uv.lock", "requirements.txt",
                     "requirements-dev.txt", "requirements.in", "constraints-3.txt",
                     "poetry.lock", "pdm.lock", "Pipfile.lock", "pylock.toml",
                     "pylock.cuda.toml"):
            self.assertTrue(scan.skips_private_ip(path), path)
        for path in ("README.md", "uv.lock.bak", "notes/requirements.md", "Cargo.lock",
                     "pyproject.toml"):
            self.assertFalse(scan.skips_private_ip(path), path)

    def test_email(self):
        self.assertEqual(kinds("mail alice@corp-mail.test"), ["email"])

    def test_noreply_and_example_emails_pass(self):
        text = ("Co-Authored-By: Claude <noreply@anthropic.com>\n"
                "1+someone@users.noreply.github.com\nuser@example.com")
        self.assertEqual(kinds(text), [])

    def test_package_versions_and_git_remotes_are_not_emails(self):
        text = "uses: actions/checkout@v4.1.0\npnpm@9.1.0\ngit@github.com:owner/repo.git"
        self.assertEqual(kinds(text), [])

    def test_attribute_access_is_not_a_domain(self):
        self.assertEqual(kinds("self.local = x; this.internal"), [])

    def test_host_path(self):
        self.assertEqual(kinds("stored in /mnt/tank/photos"), ["host-path"])
        self.assertEqual(kinds("see /home/someone/.config"), ["host-path"])

    def test_internal_domain(self):
        self.assertEqual(kinds("http://nas.local:8080"), ["internal-domain"])
        self.assertEqual(kinds("box.home.arpa"), ["internal-domain"])

    def test_code_that_looks_like_a_domain_passes(self):
        self.assertEqual(kinds("edit settings.local.json; x = threading.local()"), [])

    def test_private_term_case_insensitive(self):
        self.assertEqual(kinds("the zebrowski album", terms=["Zebrowski"]), ["private-term"])

    def test_allow_list_exempts_literal(self):
        self.assertEqual(kinds("WORKDIR /home/app", allow=["/home/app"]), [])

    def test_line_numbers_and_no_match_text(self):
        findings = scan.scan_text("ok\nok\nZebrowski", ["zebrowski"], [])
        self.assertEqual(findings[0].line, 3)
        self.assertNotIn("ebrowski", " ".join(scan.describe(findings)))

    def test_load_terms_skips_comments_and_blanks(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write("# names\nZebrowski\n\n  tank  \n")
        self.assertEqual(scan.load_terms(fh.name), ["Zebrowski", "tank"])
        os.unlink(fh.name)

    def _load(self, env, **kw):
        err = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False), \
                contextlib.redirect_stderr(err):
            result = scan.load_terms(**kw)
        return result, err.getvalue()

    def test_load_terms_missing_file(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(scan.load_terms("/nonexistent/terms.txt"), [])
        self.assertIn("only the built-in patterns", err.getvalue())

    def test_missing_default_warns(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"HOME": home}):
                os.environ.pop("BATON_PRIVATE_TERMS", None)
                result, out = self._load({})
        self.assertEqual(result, [])
        self.assertIn("~/.baton/private-terms.txt", out)
        self.assertIn("/baton:init step 5", out)
        self.assertNotIn(home, out)

    def test_missing_env_path_warns_without_value(self):
        result, out = self._load({"BATON_PRIVATE_TERMS": "/nonexistent/x/t.txt"})
        self.assertEqual(result, [])
        self.assertIn("$BATON_PRIVATE_TERMS", out)
        self.assertNotIn("/nonexistent", out)

    def test_existing_comment_only_file_is_silent(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write("# nothing\n")
        self.addCleanup(os.unlink, fh.name)
        result, out = self._load({"BATON_PRIVATE_TERMS": fh.name})
        self.assertEqual((result, out), ([], ""))

    def test_warn_false_is_silent(self):
        result, out = self._load({"BATON_PRIVATE_TERMS": "/nonexistent/t.txt"},
                                 warn=False)
        self.assertEqual((result, out), ([], ""))
