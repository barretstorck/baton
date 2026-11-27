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


class PrReadCommandsTest(unittest.TestCase):
    def test_list_github_argv(self):
        fake = FakeRunner({"gh pr list": '[{"number": 7}]'})
        code, out, _, fake = run_cmd(["pr", "list", "--head", "issue-7-x"], fake)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [{"number": 7}])
        self.assertEqual(fake.lines(), [
            "gh pr list --state open --limit 30 --head issue-7-x "
            "--json number,title,headRefName,isDraft"])

    def test_list_options(self):
        _, _, _, fake = run_cmd(["pr", "list", "--state", "all", "--limit", "5",
                                 "--json", "number"], FakeRunner({"gh pr list": "[]"}))
        self.assertEqual(fake.lines(), ["gh pr list --state all --limit 5 --json number"])

    def test_view_github_argv(self):
        fake = FakeRunner({"gh pr view 7": '{"body": "b"}'})
        code, out, _, fake = run_cmd(["pr", "view", "7", "--json", "body"], fake)
        self.assertEqual((code, json.loads(out)), (0, {"body": "b"}))
        self.assertEqual(fake.lines(), ["gh pr view 7 --json body"])
        _, _, _, fake = run_cmd(["pr", "view", "7"], FakeRunner({"gh pr view": "{}"}))
        self.assertEqual(fake.lines(), [
            "gh pr view 7 --json number,title,state,isDraft,headRefName,headRefOid,url"])

    def test_checks_rows_from_rollup(self):
        rollup = {"statusCheckRollup": [
            {"__typename": "CheckRun", "name": "test", "workflowName": "CI",
             "status": "COMPLETED", "conclusion": "SUCCESS", "detailsUrl": "http://x/1"},
            {"__typename": "StatusContext", "context": "ext", "state": "PENDING",
             "targetUrl": None}]}
        fake = FakeRunner({"gh pr view 7": json.dumps(rollup)})
        code, out, _, fake = run_cmd(["pr", "checks", "7"], fake)
        self.assertEqual(code, 0)
        self.assertEqual(fake.lines(), ["gh pr view 7 --json statusCheckRollup"])
        self.assertEqual(json.loads(out), [
            {"name": "CI / test", "state": "SUCCESS", "url": "http://x/1"},
            {"name": "ext", "state": "PENDING", "url": None}])

    def test_checks_none(self):
        code, out, _, _ = run_cmd(["pr", "checks", "7"],
                                  FakeRunner({"gh pr view": '{"statusCheckRollup": []}'}))
        self.assertEqual((code, json.loads(out)), (0, []))

    def test_forgejo_list_filters_by_head(self):
        def pr(n, ref):
            return {"number": n, "title": "t", "state": "open", "draft": False,
                    "head": {"ref": ref, "sha": "a"}, "base": {"ref": "main"}}
        backend, _ = forgejo({"GET /api/v1/repos/o/r/pulls": (200, [pr(1, "a"), pr(2, "b")])})
        code, out, _, _ = run_cmd(["pr", "list", "--head", "b"], backend=backend)
        self.assertEqual(code, 0)
        self.assertEqual([row["number"] for row in json.loads(out)], [2])

    def test_forgejo_checks_reads_commit_status(self):
        item = {"number": 3, "head": {"ref": "h", "sha": "abc"}, "base": {"ref": "main"}}
        status = {"state": "success", "total_count": 1, "statuses": [
            {"context": "ci", "status": "success", "target_url": "http://u"}]}
        backend, transport = forgejo({
            "GET /api/v1/repos/o/r/pulls/3": (200, item),
            "GET /api/v1/repos/o/r/commits/abc/status": (200, status),
            "GET /api/v1/repos/o/r/branches/main": (200, {"enable_status_check": False})})
        code, out, _, _ = run_cmd(["pr", "checks", "3"], backend=backend)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [{"name": "ci", "state": "SUCCESS", "url": "http://u"}])

    def test_usage_errors_exit_64(self):
        for args in (["view"], ["view", "x"], ["checks"], ["list", "--state", "bad"],
                     ["list", "--limit", "0"], ["list", "--json", ""], ["view", "7", "--json", ""]):
            code, _, err, fake = run_cmd(["pr"] + args)
            self.assertEqual(code, 64, args)
            self.assertIn("usage: baton pr", err)
            self.assertEqual(fake.calls, [], args)


class RepoViewCommandTest(unittest.TestCase):
    def test_github_argv(self):
        fake = FakeRunner({"gh repo view": '{"visibility": "PUBLIC"}'})
        code, out, _, fake = run_cmd(
            ["repo", "view", "--json", "nameWithOwner,visibility,defaultBranchRef"], fake)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"visibility": "PUBLIC"})
        self.assertEqual(fake.lines(),
                         ["gh repo view --json nameWithOwner,visibility,defaultBranchRef"])

    def test_forgejo_visibility(self):
        backend, _ = forgejo({"GET /api/v1/repos/o/r": (200, {
            "full_name": "o/r", "name": "r", "private": True, "default_branch": "main",
            "owner": {"login": "o"}})})
        code, out, _, _ = run_cmd(["repo", "view", "--json", "visibility,defaultBranchRef"],
                                  backend=backend)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out),
                         {"visibility": "PRIVATE", "defaultBranchRef": {"name": "main"}})

    def test_empty_fields_exit_64(self):
        code, _, err, fake = run_cmd(["repo", "view", "--json", ""])
        self.assertEqual((code, fake.calls), (64, []))
        self.assertIn("usage: baton repo view", err)


class RunLogCommandTest(unittest.TestCase):
    def test_github_argv(self):
        code, out, _, fake = run_cmd(["run", "log", "42"], FakeRunner({"gh run view": "boom\n"}))
        self.assertEqual((code, out), (0, "boom\n"))
        self.assertEqual(fake.lines(), ["gh run view 42 --log-failed"])

    def test_forgejo_is_not_supported(self):
        backend, _ = forgejo({})
        code, out, err, _ = run_cmd(["run", "log", "42"], backend=backend)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("baton: not supported on Forgejo: run logs", err)

    def test_usage_errors_exit_64(self):
        for args in (["log"], ["log", "x"]):
            code, _, err, _ = run_cmd(["run"] + args)
            self.assertEqual(code, 64, args)
            self.assertIn("usage: baton run log", err)


if __name__ == "__main__":
    unittest.main()
