import contextlib
import io
import os
import unittest
from unittest import mock

from baton import cli, identity


def run_exec(argv, returncode=0):
    with mock.patch.object(cli.subprocess, "run", return_value=mock.Mock(returncode=returncode)) as m, \
            contextlib.redirect_stderr(io.StringIO()):
        code = cli.COMMANDS["exec"](argv)
    return code, m


class ExecTest(unittest.TestCase):
    def tearDown(self):
        identity.use(None)

    def tier1(self):
        ids = identity.Identities(identity.App(1, 10, "/k/b.pem", "baton[bot]"),
                                  identity.App(2, 20, "/k/r.pem", "baton-reviewer[bot]"))
        identity.use(lambda: ids)
        p = mock.patch.object(identity, "token", lambda app, *a: (
            "tok-reviewer" if app.app_id == 2 else "tok-baton"))
        p.start()
        self.addCleanup(p.stop)

    def test_tier0_runs_gh_unchanged(self):
        identity.use(lambda: None)
        code, m = run_exec(["gh", "issue", "comment", "5", "--body", "x"])
        self.assertEqual(code, 0)
        m.assert_called_once_with(["gh", "issue", "comment", "5", "--body", "x"], env=None)

    def test_tier1_tokens_by_role(self):
        self.tier1()
        for argv, want in ((["gh", "issue", "comment", "5"], "tok-baton"),
                           (["--role", "reviewer", "gh", "pr", "review", "3"], "tok-reviewer")):
            code, m = run_exec(argv)
            args, kw = m.call_args
            self.assertEqual(kw["env"]["GH_TOKEN"], want)
            self.assertNotIn(want, " ".join(args[0]))
        self.assertNotIn("GH_TOKEN", os.environ)

    def test_exit_code_returned(self):
        identity.use(lambda: None)
        self.assertEqual(run_exec(["gh", "issue", "close", "5"], 3)[0], 3)

    def test_usage_errors(self):
        identity.use(lambda: None)
        for argv in ([], ["glab", "mr", "list"], ["--role", "nobody", "gh", "issue", "list"], ["gh"]):
            code, m = run_exec(argv)
            self.assertEqual(code, 64, argv)
            m.assert_not_called()

    def test_dangerous_gh_refused(self):
        for tier in (lambda: None, None):
            if tier is None:
                self.tier1()
            else:
                identity.use(tier)
            for argv in (["gh", "auth", "token"], ["gh", "auth", "status"],
                         ["gh", "extension", "install", "x"], ["gh", "alias", "set", "x", "y"],
                         ["gh", "issue", "comment", "5", "--hostname", "h"],
                         ["gh", "issue", "comment", "5", "--hostname=h"],
                         ["gh", "-R", "x", "auth", "token"]):
                code, m = run_exec(argv)
                self.assertEqual(code, 64, argv)
                m.assert_not_called()

    def test_unrecognised_gh_refused(self):
        identity.use(lambda: None)
        for argv in (["gh", "issue", "list"], ["gh", "api", "repos/o/r/issues"], ["gh", "pr", "view", "3"]):
            code, m = run_exec(argv)
            self.assertEqual(code, 64, argv)
            m.assert_not_called()

    def test_recognised_api_write_runs(self):
        identity.use(lambda: None)
        code, m = run_exec(["gh", "api", "-X", "POST", "repos/o/r/issues"])
        self.assertEqual(code, 0)
        m.assert_called_once()

    def test_recognised_api_write_runs_unchanged_at_tier0(self):
        identity.use(lambda: None)
        code, m = run_exec(["gh", "api", "-X", "POST", "repos/o/r/issues"])
        self.assertEqual(code, 0)
        m.assert_called_once_with(["gh", "api", "-X", "POST", "repos/o/r/issues"], env=None)

    def test_recognised_writes_run_with_token_at_tier1(self):
        self.tier1()
        for argv in (["gh", "issue", "comment", "5", "--body", "x"],
                     ["gh", "api", "-X", "POST", "repos/o/r/issues"]):
            code, m = run_exec(argv)
            self.assertEqual(code, 0, argv)
            args, kw = m.call_args
            self.assertEqual(args[0], argv)
            self.assertEqual(kw["env"]["GH_TOKEN"], "tok-baton")

    def test_refused_at_tier1_runs_nothing(self):
        self.tier1()
        for argv in (["gh", "issue", "list"], ["gh", "api", "repos/o/r/issues"], ["gh"], [],
                     ["--role", "nobody", "gh", "issue", "comment", "5"]):
            code, m = run_exec(argv)
            self.assertEqual(code, 64, argv)
            m.assert_not_called()
