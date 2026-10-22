import copy
import pathlib
import unittest
from baton import config, issue
from baton.run import RunError
from tests.fakes import FakeRunner

OWNER = "octo"
BODY = """## Context
Users cannot pass a flag.

## Acceptance criteria
- [ ] `--dry-run` prints the plan
- [x] exits 0

## Out of scope
Config files.

## Pointers
Expected files: `src/cli.py`, tests/test_cli.py
Blocked by: #12, #14

## Verification
scripts/test.sh

## Plan
Written against: 0123456789abcdef0123456789abcdef01234567

- [ ] **1. Add the flag**
  - Files: `src/cli.py`
  - Check: `scripts/test.sh tests/test_cli.py`

## Decisions
- Keep the short flag. ([answer](https://example.com/c/1))
"""


def cfg():
    return config.Config(root=pathlib.Path("."), data=copy.deepcopy(config.DEFAULTS))


def make(body=BODY, author=OWNER, labels=("type:feature",)):
    return {"author": {"login": author}, "body": body,
            "labels": [{"name": name} for name in labels]}


class ParseTest(unittest.TestCase):
    def test_fields(self):
        self.assertEqual(issue.blocked_by(BODY), [12, 14])
        self.assertEqual(issue.expected_files(BODY), ["src/cli.py", "tests/test_cli.py"])
        self.assertIn("flag", issue.sections(BODY)["context"])

    def test_missing_fields_are_empty(self):
        self.assertEqual(issue.blocked_by("text"), [])
        self.assertEqual(issue.expected_files("text"), [])
        self.assertEqual(issue.blocked_by(None), [])

    def test_none_line_has_no_blockers(self):
        note = ("Blocked by: (none; issue #5 also edits `config.py`, `README.md` and the "
                "sprint skill, so expect a trivial merge conflict if both are in one sprint)")
        self.assertEqual(issue.blocked_by(note), [])
        for line in ("Blocked by: none, see #5", "  blocked by:   None #5", "Blocked by: (None) #5"):
            with self.subTest(line=line):
                self.assertEqual(issue.blocked_by(line), [])

    def test_none_line_does_not_hide_other_lines(self):
        body = "Blocked by: none (#5 conflicts)\nBlocked by: #12\nBlocked by: #14, #15"
        self.assertEqual(issue.blocked_by(body), [12, 14, 15])


class ReadyTest(unittest.TestCase):
    def test_complete_issue_is_ready(self):
        self.assertEqual(issue.ready_problems(make(), cfg(), OWNER), [])

    def test_each_missing_part_is_reported(self):
        cases = {
            "Context": BODY.replace("Users cannot pass a flag.", ""),
            "Acceptance criteria": BODY.replace("- [ ] `--dry-run` prints the plan\n- [x] exits 0", "It works."),
            "Out of scope": BODY.replace("## Out of scope\nConfig files.\n", ""),
            "Verification": BODY.replace("scripts/test.sh", ""),
            "Expected files": BODY.replace("Expected files: `src/cli.py`, tests/test_cli.py\n", ""),
        }
        for name, body in cases.items():
            with self.subTest(name=name):
                found = issue.ready_problems(make(body=body), cfg(), OWNER)
                self.assertTrue(any(name in p for p in found), found)

    def test_type_label_required(self):
        found = issue.ready_problems(make(labels=("deliverable",)), cfg(), OWNER)
        self.assertTrue(any("type" in p for p in found))

    def test_spike_does_not_need_expected_files(self):
        body = BODY.replace("Expected files: `src/cli.py`, tests/test_cli.py\n", "")
        self.assertEqual(issue.ready_problems(make(body=body, labels=("type:spike",)), cfg(), OWNER), [])

    def test_issue_by_someone_else_needs_the_maintainer(self):
        found = issue.ready_problems(make(author="mallory"), cfg(), OWNER)
        self.assertTrue(any("maintainer" in p for p in found))


class PlanReadyTest(unittest.TestCase):
    def problems(self, body, labels=("type:feature",)):
        return issue.ready_problems(make(body=body, labels=labels), cfg(), OWNER)

    def test_complete_plan_passes(self):
        self.assertFalse(any("lan" in p for p in self.problems(BODY)))

    def test_missing_plan(self):
        body = BODY[:BODY.index("## Plan")] + BODY[BODY.index("## Decisions"):]
        self.assertTrue(any("Plan section" in p for p in self.problems(body)))
        self.assertFalse(any("Plan" in p for p in self.problems(body, ("type:spike",))))

    def test_plan_needs_written_against_and_numbered_tasks(self):
        no_sha = BODY.replace("Written against: 0123456789abcdef0123456789abcdef01234567\n", "")
        self.assertTrue(any("Written against" in p for p in self.problems(no_sha)))
        renumbered = BODY.replace("**1. Add the flag**", "**2. Add the flag**")
        self.assertTrue(any("numbered 1, 2, 3" in p for p in self.problems(renumbered)))


LINKED_HEAD = ("Plan: [docs/baton/plans/5-x.md]"
               "(https://github.com/o/r/blob/issue-5-x/docs/baton/plans/5-x.md)\n\n")
SLIM = BODY[:BODY.index("## Verification")] + BODY[BODY.index("## Decisions"):]
FILE = BODY[BODY.index("## Verification"):BODY.index("## Decisions")]


class RaisingRunner:
    def __call__(self, argv, cwd=None):
        raise RunError("missing")


class BadLinkReadyTest(unittest.TestCase):
    def test_unsafe_link_is_a_problem_and_nothing_is_run(self):
        for ref, path in [("--upload-pack=x", "docs/baton/plans/5-x.md"),
                          ("issue-5-x", "../../x.md"), ("issue-5-x", "/etc/x.md")]:
            body = f"Plan: [{path}](https://github.com/o/r/blob/{ref}/{path})\n\n" + SLIM
            runner = FakeRunner()
            with self.subTest(ref=ref, path=path):
                found = issue.ready_problems(make(body=body), cfg(), OWNER, runner)
                self.assertTrue(any("invalid Plan: link" in p for p in found))
                self.assertEqual(runner.calls, [])


REF_HEAD = ("Plan: [docs/baton/plans/5-x.md]"
            "(https://github.com/o/r/commits/refs/plans/5)\n\n")


class RefPlanReadyTest(unittest.TestCase):
    def problems(self, runner):
        return issue.ready_problems(make(body=REF_HEAD + SLIM), cfg(), OWNER, runner)

    def test_ref_plan_passes_and_is_read_through_the_ref(self):
        runner = FakeRunner({"git show --end-of-options refs/baton/plans/5:docs/baton/plans/5-x.md":
                             FILE})
        self.assertEqual(self.problems(runner), [])
        self.assertEqual(runner.lines()[0],
                         "git fetch --quiet origin +refs/plans/5:refs/baton/plans/5")

    def test_missing_ref_fails(self):
        found = self.problems(RaisingRunner())
        self.assertTrue(any("the Plan: link cannot be read" in p for p in found))
        self.assertFalse(any("Verification" in p or "Plan section" in p for p in found))


class LinkedPlanReadyTest(unittest.TestCase):
    def problems(self, body, runner, labels=("type:feature",)):
        return issue.ready_problems(make(body=body, labels=labels), cfg(), OWNER, runner)

    def runner(self, text=FILE):
        return FakeRunner({"git show --end-of-options origin/issue-5-x:docs/baton/plans/5-x.md": text})

    def test_linked_plan_passes(self):
        self.assertEqual(self.problems(LINKED_HEAD + SLIM, self.runner()), [])

    def test_linked_plan_with_as_built_line_passes(self):
        text = FILE.replace("  - Check:", "  - Provides: a\n  - Provides (as built): b\n  - Check:")
        self.assertEqual(self.problems(LINKED_HEAD + SLIM, self.runner(text)), [])

    def test_missing_file(self):
        found = self.problems(LINKED_HEAD + SLIM, RaisingRunner())
        self.assertTrue(any("cannot be read" in p for p in found))
        self.assertFalse(any("Verification" in p or "Plan section" in p for p in found))

    def test_both_plan_line_and_section(self):
        found = self.problems(LINKED_HEAD + BODY, self.runner())
        self.assertTrue(any("both a Plan:" in p for p in found))

    def test_file_without_verification_or_tasks(self):
        found = self.problems(LINKED_HEAD + SLIM, self.runner("## Notes\nx\n"))
        self.assertTrue(any("Verification" in p and "5-x.md" in p for p in found))
        self.assertTrue(any("Plan section" in p for p in found))

    def test_missing_link_reported(self):
        found = self.problems(SLIM, self.runner())
        self.assertTrue(any("'Plan:' link" in p for p in found))

    def test_spike_ignores_plan_line(self):
        self.assertEqual(self.problems(LINKED_HEAD + BODY, RaisingRunner(), ("type:spike",)), [])

    def test_legacy_body_plan_still_passes(self):
        self.assertEqual(self.problems(BODY, RaisingRunner()), [])
