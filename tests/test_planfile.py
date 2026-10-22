import copy
import json
import pathlib
import unittest
from baton import checklist, config, planfile
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
                         {"ref": "issue-5-x", "path": PATH, "pinned": False,
                          "kind": "branch"})
        body = planfile.link_line("o/r", SHA, PATH)
        self.assertEqual(planfile.parse_link(body), {"ref": SHA, "path": PATH, "pinned": True,
                                              "kind": "pinned"})

    def test_history_page_link_round_trips(self):
        self.assertEqual(planfile.ref_name(5), "refs/plans/5")
        line = planfile.link_line("o/r", "refs/plans/5", PATH)
        self.assertEqual(line, f"Plan: [{PATH}](https://github.com/o/r/commits/refs/plans/5)")
        self.assertEqual(planfile.parse_link(line + "\n\n## Context\n"),
                         {"ref": "refs/plans/5", "path": PATH, "pinned": False, "kind": "ref"})

    def test_plan_ref_must_match_the_file_number(self):
        planfile.check_link("refs/plans/5", PATH)
        for ref in ("refs/plans/7", "refs/plans/x", "refs/plans/", "refs/plans/5/x"):
            with self.assertRaises(checklist.TaskError, msg=ref):
                planfile.check_link(ref, PATH)
        with self.assertRaises(checklist.TaskError):
            planfile.parse_link(f"Plan: [{PATH}](https://github.com/o/r/commits/refs/plans/7)")

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
        self.assertEqual(runner.lines(), ["git fetch --quiet --end-of-options origin issue-5-x",
                                          f"git show --end-of-options origin/issue-5-x:{PATH}"])

    def test_read_pinned_shows_commit(self):
        runner = FakeRunner({"git show": "text"})
        planfile.read(runner, SHA, PATH)
        self.assertEqual(runner.lines(), [f"git show --end-of-options {SHA}:{PATH}"])

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


LEGACY = """## Context

Why.

## Acceptance criteria

- [ ] works

## Out of scope

Nothing else.

## Pointers

Expected files: `a.py`, `b.py`
Blocked by: #3

Follow the pattern in `a.py`.

## Verification

`make test`

## Plan

Written against: %s

- [ ] **1. First**
  - Check: none

## Decisions

- one
""" % SHA


class Migrating(Capture):
    def __init__(self, body, labels=()):
        super().__init__(body)
        self.replies["gh issue view"] = json.dumps(
            {"body": body, "labels": [{"name": name} for name in labels]})
        self.files = {}

    def __call__(self, argv, cwd=None):
        if argv[:2] == ["git", "add"]:
            with open(f"{cwd}/{argv[-1]}") as handle:
                self.files[argv[-1]] = handle.read()
        return super().__call__(argv, cwd)


class MigrateTest(unittest.TestCase):
    cfg = config.Config(root=pathlib.Path("."), data=copy.deepcopy(config.DEFAULTS))

    def test_split_legacy(self):
        file_text, body = planfile.split_legacy(LEGACY)
        self.assertIn("## Pattern\n\nFollow the pattern in `a.py`.", file_text)
        self.assertIn("## Verification\n\n`make test`", file_text)
        self.assertIn("## Plan\n\nWritten against: " + SHA, file_text)
        self.assertIn("- [ ] **1. First**", file_text)
        self.assertNotIn("## Verification", body)
        self.assertNotIn("## Plan", body)
        self.assertNotIn("Follow the pattern", body)
        self.assertIn("Expected files: `a.py`, `b.py`\nBlocked by: #3", body)
        self.assertTrue(body.index("## Pointers") < body.index("## Decisions"))
        self.assertIn("- one", body)

    def test_migrate_commits_pushes_links_and_trims(self):
        runner = Migrating(LEGACY, ["status:ready"])
        line = planfile.migrate(self.cfg, runner, "o/r", 5, "issue-5-x", "main")
        path = "docs/baton/plans/5-x.md"
        self.assertEqual(line, planfile.link_line("o/r", "issue-5-x", path))
        self.assertIn("make test", runner.files[path])
        calls = runner.lines()
        self.assertTrue(any(c.startswith("git worktree add --detach") and c.endswith("origin/main")
                            for c in calls))
        push = "git push origin HEAD:refs/heads/issue-5-x"
        self.assertIn(push, calls)
        self.assertLess(calls.index(push), next(i for i, c in enumerate(calls)
                                                if c.startswith("gh issue edit")))
        self.assertEqual(len(runner.written), 1)
        self.assertTrue(runner.written[0].startswith(line + "\n\n## Context"))
        self.assertNotIn("## Plan", runner.written[0])

    def test_refuses_started_issue(self):
        for label in ("status:in-dev", "status:blocked", "status:in-review", "status:complete"):
            runner = Migrating(LEGACY, [label])
            with self.assertRaises(checklist.TaskError):
                planfile.migrate(self.cfg, runner, "o/r", 5, "issue-5-x", "main")
            self.assertFalse(any(c.startswith("git push") for c in runner.lines()))
            self.assertEqual(runner.written, [])

    def test_refuses_linked_issue_and_bad_branch(self):
        linked = planfile.link_line("o/r", "issue-5-x", PATH) + "\n\n" + LEGACY
        with self.assertRaises(checklist.TaskError):
            planfile.migrate(self.cfg, Migrating(linked), "o/r", 5, "issue-5-x", "main")
        with self.assertRaises(checklist.TaskError):
            planfile.migrate(self.cfg, Migrating(LEGACY), "o/r", 5, "other", "main")

    def test_templates(self):
        root = pathlib.Path(__file__).resolve().parent.parent / "templates"
        plan = (root / "plan.md").read_text()
        for heading in ("## Pattern", "## Verification", "## Design notes", "## Plan"):
            self.assertIn(heading, plan)
        issue_text = (root / "issue.md").read_text()
        self.assertTrue(issue_text.startswith("Plan:"))
        self.assertNotIn("## Plan", issue_text)
        self.assertNotIn("## Verification", issue_text)



def bad_link(ref, path):
    return f"Plan: [{path}](https://github.com/o/r/blob/{ref}/{path})\n\n## Context\nx\n"


BAD_LINKS = [
    ("--upload-pack=x", PATH),
    ("issue-5-x", "../../x.md"),
    ("issue-5-x", "docs/baton/plans/../../x.md"),
    ("issue-5-x", "/etc/x.md"),
    ("issue-5-x", "docs/baton/other/x.md"),
    ("issue-5-x", "other/plans/x.md"),
    ("issue-5-x", "docs/baton/plans/x.txt"),
    ("issue-5-x", "docs/baton/plans/"),
    ("issue-5-x", "-docs/baton/plans/x.md"),
    ("a..b", PATH),
    ("a b", PATH),
]


class CheckLinkTest(unittest.TestCase):
    def test_good_links_pass(self):
        planfile.check_link("issue-5-x", PATH)
        planfile.check_link(SHA, PATH)
        planfile.check_link("feature/x.y_z", "docs/baton/plans/sub/5-x.md")
        planfile.check_link("issue-5-x", "docs/plans/5-x.md", "docs/")

    def test_bad_links_are_refused(self):
        for ref, path in BAD_LINKS:
            with self.subTest(ref=ref, path=path), self.assertRaises(checklist.TaskError) as raised:
                planfile.check_link(ref, path)
            self.assertIn("invalid Plan: link", str(raised.exception))

    def test_parse_link_refuses_bad_links(self):
        for ref, path in BAD_LINKS:
            if " " in ref:
                continue  # not a link at all: the Plan: line does not parse
            with self.subTest(ref=ref, path=path), self.assertRaises(checklist.TaskError):
                planfile.parse_link(bad_link(ref, path))

    def test_parse_link_uses_the_configured_docs_dir(self):
        body = bad_link("issue-5-x", "notes/plans/5-x.md")
        with self.assertRaises(checklist.TaskError):
            planfile.parse_link(body)
        self.assertEqual(planfile.parse_link(body, "notes")["path"], "notes/plans/5-x.md")

    def test_link_issue_refuses_a_bad_branch_or_path(self):
        for ref, path in BAD_LINKS:
            runner = Capture("## Context\nx\n")
            with self.subTest(ref=ref, path=path), self.assertRaises(checklist.TaskError):
                planfile.link_issue(runner, "o/r", 5, branch=ref, path=path)
            self.assertEqual(runner.written, [])

    def test_link_issue_at_refuses_a_bad_existing_link(self):
        runner = Capture(bad_link("issue-5-x", "../../x.md"))
        with self.assertRaises(checklist.TaskError):
            planfile.link_issue(runner, "o/r", 5, at=SHA)
        self.assertEqual(runner.written, [])

    def test_read_never_runs_for_a_bad_link_through_plan_source(self):
        runner = FakeRunner()
        with self.assertRaises(checklist.TaskError):
            checklist.plan_source(runner, bad_link("--upload-pack=x", PATH))
        self.assertEqual(runner.calls, [])

if __name__ == "__main__":
    unittest.main()
