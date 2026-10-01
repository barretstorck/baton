import os
import tempfile
import unittest
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

    def test_email(self):
        self.assertEqual(kinds("mail alice@corp-mail.test"), ["email"])

    def test_noreply_and_example_emails_pass(self):
        text = ("Co-Authored-By: Claude <noreply@anthropic.com>\n"
                "1+someone@users.noreply.github.com\nuser@example.com")
        self.assertEqual(kinds(text), [])

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

    def test_load_terms_missing_file(self):
        self.assertEqual(scan.load_terms("/nonexistent/terms.txt"), [])
