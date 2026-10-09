import copy
import pathlib
import unittest
from baton import config, issue

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
