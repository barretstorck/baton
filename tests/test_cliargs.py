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


if __name__ == "__main__":
    unittest.main()
