import copy
import json
import pathlib
import contextlib
import io
import tempfile
import unittest
from unittest import mock
from baton import checklist, cli, config, planfile
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


class TickCommitsTest(unittest.TestCase):
    PLAN = "docs/baton/plans/5-x.md"
    REF = "refs/plans/5"

    def plan_text(self, ticks):
        rows = "".join(f"- [{'x' if n in ticks else ' '}] **{n}. T{n}**\n" for n in "123")
        return f"## Plan\n\nWritten against: {SHA}\n\n{rows}"

    def test_first_commit_that_ticks_each_task(self):
        c1, c2, c3 = "1" * 40, "2" * 40, "3" * 40
        log = (f"aaa1111\t{c1}\tPlan\nbbb2222\t{c2}\tTask 1: one\n"
               f"ccc3333\t{c3}\tTask 2: two\n")
        runner = FakeRunner({
            "git log": log,
            f"git show --end-of-options {c1}:": self.plan_text(""),
            f"git show --end-of-options {c2}:": self.plan_text("1"),
            f"git show --end-of-options {c3}:": self.plan_text("12"),
        })
        self.assertEqual(planfile.tick_commits(runner, self.REF, self.PLAN),
                         {"1": "bbb2222 Task 1: one", "2": "ccc3333 Task 2: two"})
        self.assertTrue(any(l.startswith("git fetch") for l in runner.lines()))

    def test_not_a_plan_ref_is_empty(self):
        runner = FakeRunner()
        self.assertEqual(planfile.tick_commits(runner, "issue-5-x", self.PLAN), {})
        self.assertEqual(runner.calls, [])


class Capture(FakeRunner):
    """Records the body written by `gh issue edit`, which is deleted after the call."""

    def __init__(self, body):
        super().__init__({"gh issue view": json.dumps({"body": body})})
        self.written = []

    def __call__(self, argv, cwd=None, input=None):
        if argv[:3] == ["gh", "issue", "edit"]:
            with open(argv[argv.index("--body-file") + 1]) as handle:
                self.written.append(handle.read())
        return super().__call__(argv, cwd, input)


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
    def __init__(self, body, labels=(), plan_text=None):
        super().__init__(body)
        self.replies["gh issue view"] = json.dumps(
            {"title": "T", "body": body, "labels": [{"name": name} for name in labels]})
        self.replies["git commit-tree"] = SHA
        self.replies["git show"] = plan_text or ""


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

    def _migrated(self, runner):
        summary = planfile.migrate(self.cfg, runner, 5)
        self.assertEqual(len(runner.written), 1)
        written = runner.written[0]
        self.assertFalse(any(c.startswith(("git push", "git commit", "git worktree", "git rm"))
                             for c in runner.lines()))
        self.assertRegex(summary, r"\d+ task")
        return written

    def test_legacy_body_plan_goes_into_the_body(self):
        written = self._migrated(Migrating(LEGACY, ["status:ready"]))
        self.assertIn("## Developer Plan\n\nWritten against: " + SHA, written)
        self.assertIn("### Verification\n\n`make test`", written)
        self.assertIn("### Pattern", written)
        self.assertIn("- [ ] Task 1: First", written)
        self.assertNotIn("\n## Plan", written)
        self.assertNotIn("\n## Verification", written)
        self.assertTrue(written.index("## Developer Plan") < written.index("## Decisions"))
        self.assertEqual(checklist.layout_problems(written), [])

    def test_ref_plan_goes_into_the_body(self):
        body = planfile.link_line("o/r", "refs/plans/5", PATH) + "\n\n## Context\nx\n"
        runner = Migrating(body, ["status:ready"], plan_text=PLAN_TEXT)
        written = self._migrated(runner)
        self.assertNotIn("Plan:", written.split("## Developer Plan")[0])
        self.assertTrue(written.startswith("## Context"))
        self.assertIn("- [ ] Task 1:", written)

    def test_branch_plan_goes_into_the_body_and_the_branch_is_left(self):
        body = planfile.link_line("o/r", "issue-5-x", PATH) + "\n\n## Context\nx\n"
        runner = Migrating(body, ["status:ready"], plan_text=PLAN_TEXT)
        written = self._migrated(runner)
        self.assertIn(f"git show --end-of-options origin/issue-5-x:{PATH}", runner.lines())
        self.assertIn("## Developer Plan", written)
        self.assertNotIn("Plan: [", written)

    def test_refuses_started_issue(self):
        for label in ("status:in-dev", "status:blocked", "status:in-review", "status:complete"):
            runner = Migrating(LEGACY, [label])
            with self.assertRaises(checklist.TaskError):
                planfile.migrate(self.cfg, runner, 5)
            self.assertEqual(runner.written, [])

    def test_refuses_an_issue_without_a_plan(self):
        runner = Migrating("## Context\nx\n")
        with self.assertRaises(checklist.TaskError):
            planfile.migrate(self.cfg, runner, 5)
        self.assertEqual(runner.written, [])

    def test_refuses_an_already_converted_issue(self):
        done = self._migrated(Migrating(LEGACY))
        runner = Migrating(done)
        with self.assertRaises(checklist.TaskError):
            planfile.migrate(self.cfg, runner, 5)
        self.assertEqual(runner.written, [])

    def test_refuses_an_oversized_result(self):
        big = LEGACY.replace("Nothing else.", "x" * (checklist.MAX_BODY + 10))
        runner = Migrating(big)
        with self.assertRaises(checklist.TaskError):
            planfile.migrate(self.cfg, runner, 5)
        self.assertEqual(runner.written, [])

    def test_templates(self):
        root = pathlib.Path(__file__).resolve().parent.parent / "templates"
        plan = (root / "plan.md").read_text()
        for heading in ("### Pattern", "### Verification", "### Design notes", "## Developer Plan"):
            self.assertIn(heading, plan)
        issue_text = (root / "issue.md").read_text()
        self.assertFalse(issue_text.startswith("Plan:"))
        self.assertNotIn("## Developer Plan", issue_text)
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


REF = "refs/plans/5"
LOCAL = "refs/baton/plans/5"
FETCH = f"git fetch --quiet origin +{REF}:{LOCAL}"


class MissingUntilFetched(FakeRunner):
    """`git show` fails until the plan ref has been fetched."""

    def __call__(self, argv, cwd=None, input=None):
        out = super().__call__(argv, cwd, input)
        if argv[1] == "show" and not any(call[1] == "fetch" for call in self.calls):
            raise RunError("git show failed: missing")
        return out


class PlanRefTest(unittest.TestCase):
    def test_read_ref_fetches_into_local_ref_then_shows(self):
        runner = FakeRunner({"git show": "text"})
        self.assertEqual(planfile.read(runner, REF, PATH), "text")
        self.assertEqual(runner.lines(),
                         [FETCH, f"git show --end-of-options {LOCAL}:{PATH}"])

    def test_read_pinned_missing_locally_fetches_the_ref_and_retries(self):
        runner = MissingUntilFetched({"git show": "text"})
        self.assertEqual(planfile.read(runner, SHA, PATH), "text")
        self.assertEqual(runner.lines(), [f"git show --end-of-options {SHA}:{PATH}", FETCH,
                                          f"git show --end-of-options {SHA}:{PATH}"])

    def test_tip(self):
        runner = FakeRunner({"git ls-remote": f"{SHA}\t{REF}\n"})
        self.assertEqual(planfile.tip(runner, 5), SHA)
        self.assertEqual(runner.lines(), [f"git ls-remote origin {REF}"])
        self.assertIsNone(planfile.tip(FakeRunner(), 5))

    def test_write_first_commit_has_no_parent(self):
        runner = FakeRunner({"git hash-object": "b1\n", "git mktree": "t\n",
                             "git commit-tree": f"{SHA}\n"})
        self.assertEqual(planfile.write(runner, 5, PATH, "text", "Plan for #5: x"), SHA)
        lines = runner.lines()
        self.assertEqual(lines[0], "git hash-object -w --stdin")
        self.assertEqual(lines.count("git mktree"), 4)
        self.assertEqual(lines[-2], f"git ls-remote origin {REF}")
        self.assertEqual(lines[-1], "git commit-tree t -m Plan for #5: x")
        self.assertFalse(any(call[1] == "fetch" for call in runner.calls))
        self.assertEqual([i for i in runner.inputs if i][0], "text")
        self.assertEqual(runner.inputs[1], "100644 blob b1\t5-x.md\n")
        self.assertEqual(runner.inputs[2], "040000 tree t\tplans\n")
        self.assertEqual(runner.inputs[3], "040000 tree t\tbaton\n")
        self.assertEqual(runner.inputs[4], "040000 tree t\tdocs\n")

    def test_write_on_an_existing_ref_has_the_fetched_parent(self):
        runner = FakeRunner({"git hash-object": "b1\n", "git mktree": "t\n",
                             "git ls-remote": f"{SHA}\t{REF}\n", "git commit-tree": f"{SHA}\n"})
        planfile.write(runner, 5, PATH, "text", "Task 2")
        self.assertEqual(runner.lines()[-2:], [FETCH, f"git commit-tree t -p {LOCAL} -m Task 2"])

    def test_write_refuses_a_path_for_another_issue(self):
        with self.assertRaises(checklist.TaskError):
            planfile.write(FakeRunner(), 6, PATH, "text", "m")

    def test_push_is_a_plain_fast_forward(self):
        runner = FakeRunner()
        planfile.push(runner, 5, SHA)
        self.assertEqual(runner.lines(), [f"git push origin {SHA}:{REF}"])
        with self.assertRaises(checklist.TaskError):
            planfile.push(runner, 5, "abc")

    def test_link_issue_writes_the_history_page_link(self):
        runner = Capture("## Context\nx\n")
        line = planfile.link_issue(runner, "o/r", 5, branch=REF, path=PATH)
        self.assertEqual(line, f"Plan: [{PATH}](https://github.com/o/r/commits/{REF})")
        self.assertEqual(runner.written, [f"{line}\n\n## Context\nx\n"])

    def test_pin_writes_the_permalink(self):
        old = planfile.link_line("o/r", REF, PATH)
        runner = Capture(f"{old}\n\n## Context\nx\n")
        runner.replies["git ls-remote"] = f"{SHA}\t{REF}\n"
        self.assertEqual(planfile.pin(runner, "o/r", 5), SHA)
        self.assertEqual(runner.written,
                         [f"{planfile.link_line('o/r', SHA, PATH)}\n\n## Context\nx\n"])

    def test_pin_is_a_no_op_for_pinned_and_branch_links(self):
        for ref in (SHA, "issue-5-x"):
            runner = Capture(planfile.link_line("o/r", ref, PATH))
            self.assertIsNone(planfile.pin(runner, "o/r", 5))
            self.assertEqual(runner.written, [])
            self.assertFalse(any("ls-remote" in line for line in runner.lines()))

    def test_pin_without_the_ref_raises(self):
        runner = Capture(planfile.link_line("o/r", REF, PATH))
        with self.assertRaises(checklist.TaskError):
            planfile.pin(runner, "o/r", 5)


PLAN_TEXT = """## Plan
Written against: abc

- [ ] **1. First**
  - Provides: `f()`
  - Check: none
- [ ] **2. Second**
  - Check: none
"""


FILE_PLAN = """## Pattern

Follow `a.py`.

## Verification

`make test`

## Design notes

(none)

## Plan

Written against: %s

- [x] **1. First**
  - Files: `a.py`
  - Provides: `f()`
  - Provides (as built): `f(x)`
  - Check: none
- [ ] **2. Second**
  - Check: none
- [ ] Task 3: Third
  <details>
  <summary>Click to see plan details</summary>

  - Check: none

  </details>
""" % SHA


class DeveloperPlanFromFileTest(unittest.TestCase):
    def setUp(self):
        self.out = planfile.developer_plan_from_file(FILE_PLAN)

    def test_layout_and_order(self):
        self.assertTrue(self.out.startswith("## Developer Plan\n\nWritten against: " + SHA))
        self.assertLess(self.out.index("### Verification"), self.out.index("### Pattern"))
        self.assertNotIn("Design notes", self.out)
        self.assertEqual(checklist.layout_problems(self.out), [])

    def test_ticks_and_as_built_convert(self):
        self.assertIn("- [x] Task 1: First\n  <details>", self.out)
        self.assertIn("  - Provides (as built): `f(x)`", self.out)

    def test_new_layout_task_copied_unchanged(self):
        self.assertTrue(self.out.endswith(
            "- [ ] Task 3: Third\n  <details>\n  <summary>Click to see plan details</summary>\n\n"
            "  - Check: none\n\n  </details>\n"))

    def test_same_tasks_and_flags(self):
        old = checklist.plan(FILE_PLAN)["tasks"]
        self.assertEqual(checklist.plan(self.out)["tasks"], old)

    def test_design_notes_kept_when_real(self):
        out = planfile.developer_plan_from_file(
            FILE_PLAN.replace("(none)", "Use X."))
        self.assertIn("### Design notes\n\nUse X.", out)


class CliRunner(FakeRunner):
    def __init__(self, replies=None, body=""):
        base = {"hash-object": "b1\n", "git mktree": "t\n", "git commit-tree": f"{SHA}\n",
                "gh issue view 5 --json title": json.dumps({"title": "The title"}),
                "gh issue view 5 --json body": json.dumps({"body": body}),
                "gh repo view": json.dumps({"nameWithOwner": "o/r"}),
                "git show": PLAN_TEXT}
        base.update(replies or {})
        super().__init__(base)


class CliTest(unittest.TestCase):
    def call(self, runner, argv, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        cfg = mock.Mock(data={"docs": {"dir": "docs/baton"}, "privacy_allow": []})
        with mock.patch.object(cli.run, "run", runner), \
                mock.patch.object(cli, "_require_config", return_value=cfg), \
                mock.patch("sys.stdin", io.StringIO(stdin)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_plan_set_from_a_file_and_from_stdin(self):
        seen = []

        def fake(cfg, runner, number, text, terms):
            seen.append((number, text))
            return [1, 2]
        with mock.patch.object(cli.checklist, "set_issue_plan", fake), \
                mock.patch.object(cli.scan, "load_terms", return_value=[]):
            code, out, _ = self.call(CliRunner(), ["plan", "set", "5", "-"], stdin="STDIN")
            self.assertEqual((code, out), (0, "set Developer Plan of #5: 2 task(s)\n"))
            with tempfile.NamedTemporaryFile("w", suffix=".md") as handle:
                handle.write("FILE")
                handle.flush()
                code, _, _ = self.call(CliRunner(), ["plan", "set", "5", handle.name])
        self.assertEqual(code, 0)
        self.assertEqual(seen, [(5, "STDIN"), (5, "FILE")])

    def test_plan_set_wrong_count_exits_64_and_usage_lists_it(self):
        code, _, err = self.call(CliRunner(), ["plan", "set", "5"])
        self.assertEqual(code, 64)
        self.assertIn("baton plan set <issue> <file | ->", err)

    def test_plan_set_task_error_is_not_a_crash(self):
        def boom(*a):
            raise cli.checklist.TaskError("bad layout")
        with mock.patch.object(cli.checklist, "set_issue_plan", boom), \
                mock.patch.object(cli.scan, "load_terms", return_value=[]):
            code, _, err = self.call(CliRunner(), ["plan", "set", "5", "-"], stdin="x")
        self.assertNotEqual(code, 0)
        self.assertIn("bad layout", err)

    def test_plan_commit_link_pin_are_deprecated_no_ops(self):
        for argv in (["plan", "commit", "5", PATH, "-"], ["plan", "pin", "5"],
                     ["plan", "link", "5", REF, PATH], ["plan", "link", "5", "--at", SHA]):
            runner = CliRunner()
            code, out, err = self.call(runner, argv, stdin=PLAN_TEXT)
            self.assertEqual((code, out), (0, ""), argv)
            self.assertIn("deprecated: plans live in the issue body", err)
            self.assertIn("baton plan set <N> <file>", err)
            self.assertEqual(runner.lines(), [], argv)

    def test_task_done_refuses_a_ref_plan(self):
        body = ("Plan: [docs/baton/plans/5-x.md]"
                "(https://github.com/o/r/commits/refs/plans/5)\n")
        for extra in ([], ["--as-built", "g()"]):
            runner = CliRunner(body=body)
            code, _, err = self.call(runner, ["task", "done", "issue", "5", "1"] + extra)
            self.assertNotEqual(code, 0)
            self.assertIn("issue #5 still links its plan on refs/plans/5; "
                          "run 'baton plan migrate 5' first", err)
            self.assertFalse(any(l.startswith(("git fetch", "git push", "git commit-tree",
                                               "git ls-remote")) for l in runner.lines()))

    def test_bad_argument_counts_exit_64(self):
        for argv in (["plan", "commit", "x", PATH, "-"],
                     ["plan", "pin"],
                     ["task", "done", "issue", "5", "1", "--as-built"],
                     ["task", "done", "pr", "5", "1.1", "--as-built", "x"],
                     ["task", "done", "issue", "5"]):
            code, _, err = self.call(CliRunner(), argv)
            self.assertEqual(code, 64, argv)
            self.assertIn("usage:", err)

    def test_usage_lists_the_new_forms(self):
        _, _, err = self.call(CliRunner(), ["plan"])
        for form in ("plan commit", "plan pin", "plan link <issue> ...", "deprecated"):
            self.assertIn(form, err)
