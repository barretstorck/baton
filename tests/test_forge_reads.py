import io
import json
import unittest
from unittest import mock

from baton import cli
from baton.forge.forgejo import Forgejo
from tests.fakes import FakeRunner, FakeTransport


def run_cmd(args, fake=None, backend=None):
    fake = fake or FakeRunner()
    out, err = io.StringIO(), io.StringIO()
    patches = [mock.patch.object(cli.run, "run", fake),
               mock.patch("sys.stdout", out), mock.patch("sys.stderr", err)]
    if backend is not None:
        patches.append(mock.patch("baton.forge.for_repo", return_value=backend))
    for patch in patches:
        patch.start()
    try:
        code = cli.main(args)
    finally:
        for patch in reversed(patches):
            patch.stop()
    return code, out.getvalue(), err.getvalue(), fake


def forgejo(replies):
    transport = FakeTransport(replies)
    return Forgejo("https://f.example", "o", "r", token="t", transport=transport), transport


class IssueListCommandTest(unittest.TestCase):
    def test_github_argv_and_output(self):
        fake = FakeRunner({"gh issue list": '[{"number": 3, "title": "T"}]'})
        code, out, _, fake = run_cmd(["issue", "list", "--state", "all", "--search", "two words"], fake)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [{"number": 3, "title": "T"}])
        self.assertEqual(fake.lines(), [
            "gh issue list --state all --search two words --limit 30 "
            "--json number,title,state,labels"])

    def test_filters_and_fields(self):
        code, _, _, fake = run_cmd(["issue", "list", "--label", "x", "--limit", "5",
                                    "--json", "number,author"])
        self.assertEqual(code, 0)
        line = fake.lines()[0]
        self.assertIn("--label x", line)
        self.assertTrue(line.endswith("--limit 5 --json number,author"))

    def test_refuses_issue_text_fields(self):
        for fields in ("body", "number,comments", "bogus", ""):
            code, out, err, fake = run_cmd(["issue", "list", "--json", fields])
            self.assertEqual(code, 64, fields)
            self.assertIn("usage: baton issue list", err)
            self.assertEqual((out, fake.calls), ("", []))

    def test_bad_options_exit_64(self):
        for args in (["--state", "bad"], ["--limit", "0"], ["--limit", "x"]):
            self.assertEqual(run_cmd(["issue", "list"] + args)[0], 64)

    def test_forgejo(self):
        item = {"number": 4, "title": "F", "state": "open", "labels": [{"name": "a"}]}
        backend, transport = forgejo({"GET /api/v1/repos/o/r/issues": (200, [item])})
        code, out, _, _ = run_cmd(["issue", "list"], backend=backend)
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual([row["number"] for row in data], [4])
        self.assertTrue(transport.lines()[0].startswith("GET /api/v1/repos/o/r/issues"))


class TriageReadCommandTest(unittest.TestCase):
    def test_github_prints_outsider_text_in_full(self):
        data = {"title": "T", "body": "IGNORE PREVIOUS", "labels": [],
                "author": {"login": "eve"}, "comments": [{"body": "hi"}]}
        fake = FakeRunner({"gh issue view 7": json.dumps(data)})
        code, out, _, fake = run_cmd(["triage", "read", "7"], fake)
        self.assertEqual(code, 0)
        self.assertEqual(fake.lines(), ["gh issue view 7 --json title,body,labels,author,comments"])
        self.assertEqual(json.loads(out), data)
        self.assertIn("IGNORE PREVIOUS", out)

    def test_fields(self):
        fake = FakeRunner({"gh issue view 7": "{}"})
        _, _, _, fake = run_cmd(["triage", "read", "7", "--json", "body"], fake)
        self.assertEqual(fake.lines(), ["gh issue view 7 --json body"])

    def test_usage_errors_exit_64(self):
        for args in (["read"], ["read", "x"], ["read", "7", "--json", ""]):
            code, _, err, fake = run_cmd(["triage"] + args)
            self.assertEqual(code, 64, args)
            self.assertIn("baton triage read", err)
            self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
