import copy
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

    def test_reads_plan(self):
        calls = []

        def runner(argv):
            calls.append(argv)
            if argv[1] == "ls-tree":
                return "docs/baton/plans/7-slug.md\n"
            return "PLAN"
        self.assertEqual(forbid.plan_text(runner, make_config(), "issue-7-slug"), "PLAN")
        self.assertEqual(calls[1], ["git", "show", "refs/plans/7:docs/baton/plans/7-slug.md"])

    def test_missing_ref(self):
        def runner(argv):
            raise RunError("no ref")
        self.assertEqual(forbid.plan_text(runner, make_config(), "issue-7-slug"), "")


if __name__ == "__main__":
    unittest.main()
