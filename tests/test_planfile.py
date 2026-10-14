import json
import unittest
from baton import checklist, planfile
from baton.run import RunError
from tests.fakes import FakeRunner

SHA = "b" * 40
CFG = {"docs": {"dir": "docs/baton"}}
PATH = "docs/baton/plans/5-x.md"


class Missing:
    def __call__(self, argv, cwd=None):
        if argv[1] == "show":
            raise RunError("git show failed: missing")
        return ""


class PlanfileTest(unittest.TestCase):
    def test_path_and_line(self):
        self.assertEqual(planfile.plan_path(CFG, 5, "x"), PATH)
        line = planfile.link_line("o/r", "issue-5-x", PATH)
        self.assertEqual(line, f"Plan: [{PATH}](https://github.com/o/r/blob/issue-5-x/{PATH})")

    def test_parse_branch_and_pinned(self):
        body = planfile.link_line("o/r", "issue-5-x", PATH) + "\n\n## Context\n"
        self.assertEqual(planfile.parse_link(body),
                         {"ref": "issue-5-x", "path": PATH, "pinned": False})
        body = planfile.link_line("o/r", SHA, PATH)
        self.assertEqual(planfile.parse_link(body), {"ref": SHA, "path": PATH, "pinned": True})

    def test_parse_none(self):
        self.assertIsNone(planfile.parse_link("## Context\nPlan: later\n"))
        self.assertIsNone(planfile.parse_link(""))
        self.assertIsNone(planfile.parse_link(None))

    def test_set_link_inserts_and_replaces(self):
        line = planfile.link_line("o/r", "b", PATH)
        self.assertEqual(planfile.set_link("## Context\nx\n", line), f"{line}\n\n## Context\nx\n")
        new = planfile.link_line("o/r", SHA, PATH)
        out = planfile.set_link(f"{line}\n\n## Context\nx\n", new)
        self.assertEqual(out, f"{new}\n\n## Context\nx\n")

    def test_read_branch_fetches_then_shows(self):
        runner = FakeRunner({"git show": "text"})
        self.assertEqual(planfile.read(runner, "issue-5-x", PATH), "text")
        self.assertEqual(runner.lines(), ["git fetch --quiet origin issue-5-x",
                                          f"git show origin/issue-5-x:{PATH}"])

    def test_read_pinned_shows_commit(self):
        runner = FakeRunner({"git show": "text"})
        planfile.read(runner, SHA, PATH)
        self.assertEqual(runner.lines(), [f"git show {SHA}:{PATH}"])

    def test_read_missing(self):
        with self.assertRaises(checklist.TaskError):
            planfile.read(Missing(), "issue-5-x", PATH)


class Capture(FakeRunner):
    """Records the body written by `gh issue edit`, which is deleted after the call."""

    def __init__(self, body):
        super().__init__({"gh issue view": json.dumps({"body": body})})
        self.written = []

    def __call__(self, argv, cwd=None):
        if argv[:3] == ["gh", "issue", "edit"]:
            with open(argv[argv.index("--body-file") + 1]) as handle:
                self.written.append(handle.read())
        return super().__call__(argv, cwd)


class LinkIssueTest(unittest.TestCase):
    def test_inserts_link_when_missing(self):
        runner = Capture("## Context\nx\n")
        line = planfile.link_issue(runner, "o/r", 5, branch="issue-5-x", path=PATH)
        self.assertEqual(line, planfile.link_line("o/r", "issue-5-x", PATH))
        self.assertEqual(runner.written, [f"{line}\n\n## Context\nx\n"])

    def test_at_pins_and_keeps_path_and_rest(self):
        old = planfile.link_line("o/r", "issue-5-x", PATH)
        runner = Capture(f"{old}\n\n## Context\nx\n")
        planfile.link_issue(runner, "o/r", 5, at=SHA)
        new = planfile.link_line("o/r", SHA, PATH)
        self.assertEqual(runner.written, [f"{new}\n\n## Context\nx\n"])

    def test_at_without_link_raises(self):
        with self.assertRaises(checklist.TaskError):
            planfile.link_issue(Capture("## Context\n"), "o/r", 5, at=SHA)

    def test_at_needs_full_sha(self):
        body = planfile.link_line("o/r", "b", PATH)
        with self.assertRaises(checklist.TaskError):
            planfile.link_issue(Capture(body), "o/r", 5, at="abc")

    def test_unchanged_writes_nothing(self):
        line = planfile.link_line("o/r", "issue-5-x", PATH)
        runner = Capture(f"{line}\n\n## Context\n")
        planfile.link_issue(runner, "o/r", 5, branch="issue-5-x", path=PATH)
        self.assertEqual(runner.written, [])

    def test_missing_args_raise(self):
        with self.assertRaises(checklist.TaskError):
            planfile.link_issue(Capture(""), "o/r", 5)


if __name__ == "__main__":
    unittest.main()
