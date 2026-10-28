import copy
import json
import pathlib
import unittest
from baton import config, forbid
from baton.run import RunError

RULES = {"pyproject.toml": r'^\s*"src/[^"]*"\s*=\s*\['}


def patch(path, *lines, removed=()):
    body = "".join(f"-{r}\n" for r in removed) + "".join(f"+{x}\n" for x in lines)
    return (f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1,1 +1,2 @@\n" + body)


def make_config():
    return config.Config(root=pathlib.Path("."), data=copy.deepcopy(config.DEFAULTS))


class ViolationsTest(unittest.TestCase):
    def test_matching_line(self):
        out = forbid.violations(patch("pyproject.toml", '"src/x.py" = ["E501"]'), RULES)
        self.assertEqual(out, ['forbidden added line in pyproject.toml: "src/x.py" = ["E501"]'])

    def test_non_matching_line(self):
        self.assertEqual(forbid.violations(patch("pyproject.toml", 'name = "x"'), RULES), [])

    def test_non_matching_glob(self):
        self.assertEqual(forbid.violations(patch("other.toml", '"src/x.py" = ["E501"]'), RULES), [])

    def test_deleted_line_ignored(self):
        p = patch("pyproject.toml", "x = 1", removed=['"src/x.py" = ["E501"]'])
        self.assertEqual(forbid.violations(p, RULES), [])

    def test_unicode_line_separators_do_not_hide_a_line(self):
        for sep in ("\x0c", "\x0b", "\x1c", "\x85", "\u2028"):
            with self.subTest(sep=repr(sep)):
                p = patch("pyproject.toml", sep + '"src/x.py" = ["E501"]')
                self.assertEqual(len(forbid.violations(p, RULES)), 1)

    def test_quoted_header_is_unquoted_before_matching(self):
        p = ('diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"\n--- "a/caf\\303\\251.py"\n'
             '+++ "b/caf\\303\\251.py"\n@@ -0,0 +1 @@\n+bad\n')
        out = forbid.violations(p, {"*.py": "^bad$"})
        self.assertEqual(out, ["forbidden added line in caf\u00e9.py: bad"])

    def test_unparsable_header_is_reported(self):
        for name in ('"b/bad\\xff.py"', '"b/\\377.py"', '"b/bad\\9.py"', '"b/open.py'):
            with self.subTest(name=name):
                p = f"diff --git a/x b/x\n--- a/x\n+++ {name}\n@@ -0,0 +1 @@\n+bad\n"
                out = forbid.violations(p, {"*.py": "^bad$"})
                self.assertTrue(any(name in line for line in out), out)

    def test_allowed_glob(self):
        p = patch("pyproject.toml", '"src/x.py" = ["E501"]')
        self.assertEqual(forbid.violations(p, RULES, allowed={"pyproject.toml"}), [])


class AllowedTest(unittest.TestCase):
    def test_reads_allows_lines(self):
        text = '- [ ] **2. t**\n  - Allows: forbid_added "pyproject.toml"\n  Allows: forbid_added "a/*.py"\n'
        self.assertEqual(forbid.allowed_globs(text), {"pyproject.toml", "a/*.py"})

    def test_none(self):
        self.assertEqual(forbid.allowed_globs("nothing"), set())


class PlanTextTest(unittest.TestCase):
    def test_branch_without_issue(self):
        def runner(argv):
            raise AssertionError("no call expected")
        self.assertEqual(forbid.plan_text(runner, make_config(), "main"), "")
        self.assertEqual(forbid.plan_text(runner, make_config(), ""), "")

    def test_reads_plan_section_of_the_body(self):
        calls = []
        body = "## Context\nc\n\n## Developer Plan\n\nAllows: forbid_added \"a\"\n\n## Other\nx\n"

        def runner(argv):
            calls.append(argv)
            return json.dumps({"body": body})
        text = forbid.plan_text(runner, make_config(), "issue-7-slug")
        self.assertIn('Allows: forbid_added "a"', text)
        self.assertNotIn("Context", text)
        self.assertNotIn("Other", text)
        self.assertEqual(calls, [["gh", "issue", "view", "7", "--json", "body"]])

    def test_explicit_number_wins_over_branch(self):
        calls = []

        def runner(argv):
            calls.append(argv)
            return json.dumps({"body": "## Developer Plan\nPLAN\n"})
        self.assertIn("PLAN", forbid.plan_text(runner, make_config(), "issue-7-slug", 5))
        self.assertEqual(calls[0][3], "5")

    def test_body_without_plan_section(self):
        def runner(argv):
            return json.dumps({"body": "## Context\nc\n"})
        self.assertEqual(forbid.plan_text(runner, make_config(), "issue-7-slug"), "")

    def test_errors_give_nothing(self):
        for reply in (RunError("no network"), "not json", json.dumps({})):
            def runner(argv, reply=reply):
                if isinstance(reply, Exception):
                    raise reply
                return reply
            self.assertEqual(forbid.plan_text(runner, make_config(), "issue-7-slug"), "")


if __name__ == "__main__":
    unittest.main()
