import contextlib
import io
import unittest
import unittest.mock

from baton import cliargs


def make(positional_only=False, lenient=False):
    p = cliargs.BatonParser(prog="x", usage_text="usage: x USAGE\nline2",
                            lenient=lenient)
    if positional_only:
        p.add_argument("ref")
    else:
        p.add_argument("--title")
        p.add_argument("--flag", action="store_true")
    return p


def run(parser, argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            opts, code = cliargs.parse_with(parser, argv), None
        except cliargs.Usage as exc:
            opts, code = None, exc.code
    return opts, code, out.getvalue(), err.getvalue()


class FrameworkTest(unittest.TestCase):
    def test_error_prints_usage_text(self):
        _, code, out, err = run(make(), ["--bogus"])
        self.assertEqual((code, out, err), (64, "", "usage: x USAGE\nline2\n"))

    def test_help_is_stdout_exit_zero(self):
        _, code, out, _ = run(make(), ["--help"])
        self.assertEqual(code, 0)
        self.assertIn("usage:", out)

    def test_value_option_takes_dashed_word(self):
        opts, code, _, _ = run(make(), ["--title", "-x"])
        self.assertIsNone(code)
        self.assertEqual(opts.title, "-x")

    def test_positional_only_takes_dashed_value(self):
        opts, _, _, _ = run(make(positional_only=True), ["--force"])
        self.assertEqual(opts.ref, "--force")

    def test_positional_only_help(self):
        _, code, _, _ = run(make(positional_only=True), ["-h"])
        self.assertEqual(code, 0)

    def test_lenient_ignores_unknown(self):
        opts, code, _, _ = run(make(lenient=True), ["junk", "--flag", "--zz"])
        self.assertIsNone(code)
        self.assertTrue(opts.flag)

    def test_no_abbreviation(self):
        _, code, _, _ = run(make(), ["--tit", "a"])
        self.assertEqual(code, 64)

    def test_digits(self):
        self.assertEqual(cliargs.digits("12"), 12)
        for bad in ("1x", "-1"):
            with self.assertRaises(Exception):
                cliargs.digits(bad)

    def test_command_decorator(self):
        parser = make()
        cliargs.build()
        cliargs._commands()  # framework only: no commands yet
        with unittest.mock.patch.object(
                cliargs, "parse", lambda n, a: cliargs.parse_with(parser, a)):
            @cliargs.command("t")
            def handler(opts):
                return 7
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(handler(["--bogus"]), 64)
            self.assertEqual(handler(["--flag"]), 7)
        self.assertTrue(hasattr(handler, "__wrapped__"))


BAD = {
    "set-status": ["1"], "set-priority": ["x", "1"], "set-severity": ["1"],
    "merge-pr": ["x"], "comment": ["issue", "1"], "answers": [],
    "open-questions": ["x"], "ready-check": ["x"], "sprint-order": ["x"],
    "pr-state": [], "verify": ["x", "dir"], "identity": ["nope"],
    "push": [], "doctor": ["x"],
}
LENIENT = ["config", "budget", "gate", "install-hooks", "labels",
           "sprint-status", "clean"]


class CommandsTest(unittest.TestCase):
    def call(self, name, argv):
        from baton import cli
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.COMMANDS[name](argv)
        return code, out.getvalue(), err.getvalue()

    def test_help_returns_zero(self):
        for name in [*BAD, *LENIENT]:
            with self.subTest(name):
                code, out, _ = self.call(name, ["--help"])
                self.assertEqual((code, out.startswith("usage:")), (0, True))

    def test_malformed_prints_old_usage(self):
        for name, argv in BAD.items():
            with self.subTest(name):
                code, _, err = self.call(name, argv)
                self.assertEqual(code, 64)
                self.assertEqual(err, cliargs.USAGE[name] + "\n")

    def test_sprint_order_usage_text(self):
        self.assertEqual(cliargs.USAGE["sprint-order"],
                         "usage: baton sprint-order <issue>...")

    def test_lenient_commands_ignore_extra_words(self):
        for name in LENIENT:
            with self.subTest(name):
                opts = cliargs.parse(name, ["extra", "--zzz"])
                self.assertIsNotNone(opts)

    def test_push_takes_dash_word_as_value(self):
        self.assertEqual(cliargs.parse("push", ["--force"]).refspec, "--force")

class PublishCommandsTest(unittest.TestCase):
    def call(self, name, argv):
        from baton import cli
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.COMMANDS[name](argv)
        return code, out.getvalue(), err.getvalue()

    def test_help_returns_zero(self):
        for name, argv in (("issue", ["--help"]), ("issue", ["create", "--help"]),
                           ("pr", ["ready", "--help"]),
                           ("milestone", ["edit", "--help"])):
            with self.subTest(argv):
                code, out, _ = self.call(name, argv)
                self.assertEqual((code, out.startswith("usage:")), (0, True))

    def test_malformed_prints_the_action_usage(self):
        for name, argv, key in (
                ("issue", ["create"], "issue create"),
                ("issue", ["read", "x"], "issue read"),
                ("issue", ["missed", "7"], "issue missed"),
                ("pr", ["ready", "x"], "pr ready"),
                ("pr", ["edit", "5", "--milestone", "M", "--remove-milestone"], "pr edit"),
                ("milestone", ["edit", "3", "--state", "bogus"], "milestone edit"),
                ("milestone", [], "milestone"),
                ("issue", ["bogus"], "issue")):
            with self.subTest(argv):
                code, _, err = self.call(name, argv)
                self.assertEqual((code, err), (64, cliargs.USAGE[key] + "\n"))

    def test_option_value_may_look_like_an_option(self):
        o = cliargs.parse("issue", ["create", "--title", "-x", "-"])
        self.assertEqual((o.title, o.body_source), ("-x", "-"))


class StateCommandsTest(PublishCommandsTest):
    COMMANDS = (
        ("close", ["--help"]), ("reopen", ["--help"]), ("review", ["--help"]),
        ("review", ["post", "--help"]), ("tasks", ["--help"]),
        ("task", ["--help"]), ("task", ["done", "--help"]),
        ("plan", ["--help"]), ("plan", ["replace", "--help"]),
        ("notify", ["--help"]), ("triage", ["--help"]),
        ("triage", ["apply", "--help"]), ("git-hook", ["--help"]),
        ("git-hook", ["commit-msg", "--help"]))

    def test_help_returns_zero(self):
        for name, argv in self.COMMANDS:
            with self.subTest(name, argv=argv):
                code, out, _ = self.call(name, argv)
                self.assertEqual((code, out.startswith("usage:")), (0, True))

    def test_malformed_prints_the_command_usage(self):
        for name, argv in (
                ("close", ["issue", "5", "dev"]),
                ("close", ["issue", "5", "dev", "-", "--duplicate-of", "x"]),
                ("close", ["issue", "5", "dev", "-", "--bogus"]),
                ("reopen", ["issue", "5", "dev", "-", "--lock"]),
                ("review", ["state"]), ("review", ["post", "5"]),
                ("tasks", ["issue"]), ("tasks", ["x", "5"]),
                ("task", ["done", "pr", "5", "1", "--as-built", "x"]),
                ("task", ["done", "issue", "5"]),
                ("plan", ["replace", "5"]), ("plan", ["bogus"]),
                ("notify", []), ("notify", ["--rules", "5"]),
                ("notify", ["5", "bogus", "-"]),
                ("triage", ["apply", "x", "f"]), ("triage", []),
                ("git-hook", ["commit-msg"]), ("git-hook", ["bogus"])):
            with self.subTest(name, argv=argv):
                with unittest.mock.patch("baton.cli._require_config"):
                    code, _, err = self.call(name, argv)
                self.assertEqual(code, 64)
                self.assertTrue(err.startswith("usage: baton " + name), err)

    def test_close_flags(self):
        o = cliargs.parse("close", ["issue", "5", "dev", "-", "--reason", "duplicate",
                                    "--duplicate-of", "3", "--lock"])
        self.assertEqual((o.close_reason, o.duplicate_of, o.lock, o.number),
                         ("duplicate", 3, True, 5))

    def test_plan_deprecated_actions_take_more_words(self):
        o = cliargs.parse("plan", ["commit", "5", "path", "-"])
        self.assertEqual((o.action, o.issue), ("commit", 5))

    def test_cli_functions_are_short(self):
        import ast, inspect
        from baton import cli
        tree = ast.parse(inspect.getsource(cli))
        long = [f.name for f in tree.body if isinstance(f, ast.FunctionDef)
                and f.name in ("cmd_close", "cmd_reopen", "cmd_review", "cmd_tasks",
                               "cmd_task", "cmd_plan", "cmd_notify", "cmd_triage",
                               "cmd_git_hook", "_plan_replace", "_notify_rules")
                and f.end_lineno - f.lineno + 1 > 30]
        self.assertEqual(long, [])


class LastCommandsTest(PublishCommandsTest):
    def test_help_returns_zero(self):
        for name, argv in (("usage", ["--help"]), ("sprint-capacity", ["--help"]),
                           ("sprint-metrics", ["--help"]), ("sandbox", ["--help"]),
                           ("sandbox", ["run", "--help"]), ("lock", ["--help"]),
                           ("lock", ["acquire", "--help"])):
            with self.subTest(name, argv=argv):
                code, out, _ = self.call(name, argv)
                self.assertEqual((code, out.startswith("usage:")), (0, True))

    def test_malformed_prints_the_command_usage(self):
        for name, argv in (
                ("usage", ["--bogus"]), ("usage", ["--since"]), ("usage", ["x"]),
                ("sprint-capacity", []), ("sprint-capacity", ["0"]),
                ("sprint-capacity", ["x"]), ("sprint-capacity", ["inf"]),
                ("sprint-capacity", ["5", "--retros", "0"]),
                ("sprint-capacity", ["5", "--retros", "x"]),
                ("sprint-capacity", ["5", "6"]),
                ("sprint-metrics", []), ("sprint-metrics", ["m", "--bogus"]),
                ("sandbox", []), ("sandbox", ["bogus"]), ("sandbox", ["check", "x"]),
                ("sandbox", ["run"]),
                ("lock", []), ("lock", ["bogus"]), ("lock", ["acquire", "--ttl-hours"])):
            with self.subTest(name, argv=argv):
                with unittest.mock.patch("baton.cli._require_config"):
                    code, _, err = self.call(name, argv)
                self.assertEqual(code, 64)
                self.assertTrue(err.startswith("usage: baton " + name), err)

    def test_sandbox_run_keeps_dashed_words(self):
        o = cliargs.parse("sandbox", ["run", "echo", "-x"])
        self.assertEqual(o.words, ["echo", "-x"])

    def test_capacity_days_stay_integral(self):
        self.assertEqual(cliargs.parse("sprint-capacity", ["7"]).days, 7)
        self.assertEqual(cliargs.parse("sprint-capacity", ["1.5", "--retros", "2"]).days, 1.5)

    def test_usage_defaults(self):
        o = cliargs.parse("usage", ["--json"])
        self.assertEqual((o.dir, o.json, o.since, o.repo), ("~/.claude/projects", True, None, None))


if __name__ == "__main__":
    unittest.main()
