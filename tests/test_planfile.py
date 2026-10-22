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
        cfg = mock.Mock(data={"docs": {"dir": "docs/baton"}})
        with mock.patch.object(cli.run, "run", runner), \
                mock.patch.object(cli, "_require_config", return_value=cfg), \
                mock.patch("sys.stdin", io.StringIO(stdin)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_plan_commit_writes_pushes_and_prints_the_sha(self):
        runner = CliRunner()
        code, out, _ = self.call(runner, ["plan", "commit", "5", PATH, "-"], stdin=PLAN_TEXT)
        self.assertEqual((code, out), (0, SHA + "\n"))
        self.assertEqual([i for i in runner.inputs if i][0], PLAN_TEXT)
        lines = runner.lines()
        self.assertIn("git commit-tree t -m Plan for #5: The title", lines)
        self.assertEqual(lines[-1], f"git push origin {SHA}:{REF}")

    def test_plan_commit_reads_a_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md") as handle:
            handle.write(PLAN_TEXT)
            handle.flush()
            runner = CliRunner()
            code, _, _ = self.call(runner, ["plan", "commit", "5", PATH, handle.name])
        self.assertEqual(code, 0)
        self.assertEqual([i for i in runner.inputs if i][0], PLAN_TEXT)

    def test_plan_link_writes_the_history_page_link(self):
        runner = CliRunner(body="## Context\nx\n")
        with mock.patch.object(checklist, "_save_issue") as save:
            code, out, _ = self.call(runner, ["plan", "link", "5", REF, PATH])
        line = f"Plan: [{PATH}](https://github.com/o/r/commits/{REF})"
        self.assertEqual((code, out), (0, line + "\n"))
        save.assert_called_once_with(runner, 5, f"{line}\n\n## Context\nx\n")

    def test_plan_pin_pins_to_the_tip(self):
        body = planfile.link_line("o/r", REF, PATH) + "\n\n## Context\n"
        runner = CliRunner(replies={"git ls-remote": f"{SHA}\t{REF}\n"}, body=body)
        with mock.patch.object(checklist, "_save_issue") as save:
            code, out, _ = self.call(runner, ["plan", "pin", "5"])
        self.assertEqual((code, out), (0, SHA + "\n"))
        self.assertIn(planfile.link_line("o/r", SHA, PATH), save.call_args[0][2])

    def test_plan_pin_with_nothing_to_pin(self):
        runner = CliRunner(body=planfile.link_line("o/r", "issue-5-x", PATH))
        code, out, _ = self.call(runner, ["plan", "pin", "5"])
        self.assertEqual((code, out), (0, "nothing to pin\n"))

    def test_task_done_ticks_on_the_ref(self):
        body = planfile.link_line("o/r", REF, PATH) + "\n"
        runner = CliRunner(replies={"git ls-remote": f"{SHA}\t{REF}\n"}, body=body)
        code, out, _ = self.call(runner, ["task", "done", "issue", "5", "1"])
        self.assertEqual(code, 0)
        self.assertIn(f"plan commit {SHA}", out)
        self.assertIn("git commit-tree t -p refs/baton/plans/5 -m Task 1", runner.lines())
        self.assertIn("[x] **1. First**", [i for i in runner.inputs if i][0])

    def test_task_done_as_built_adds_the_line(self):
        body = planfile.link_line("o/r", REF, PATH) + "\n"
        runner = CliRunner(body=body)
        code, _, _ = self.call(runner, ["task", "done", "issue", "5", "1", "--as-built", "g()"])
        self.assertEqual(code, 0)
        self.assertIn("Provides (as built): g()", [i for i in runner.inputs if i][0])

    def test_task_done_as_built_flag_before_the_task(self):
        body = planfile.link_line("o/r", REF, PATH) + "\n"
        runner = CliRunner(body=body)
        code, _, _ = self.call(runner, ["task", "done", "issue", "5", "--as-built", "g()", "1"])
        self.assertEqual(code, 0)
        self.assertIn("Provides (as built): g()", [i for i in runner.inputs if i][0])

    def test_bad_argument_counts_exit_64(self):
        for argv in (["plan", "commit", "5", PATH], ["plan", "commit", "x", PATH, "-"],
                     ["plan", "pin"], ["plan", "pin", "5", "6"],
                     ["task", "done", "issue", "5", "1", "--as-built"],
                     ["task", "done", "pr", "5", "1.1", "--as-built", "x"],
                     ["task", "done", "issue", "5"]):
            code, _, err = self.call(CliRunner(), argv)
            self.assertEqual(code, 64, argv)
            self.assertIn("usage:", err)

    def test_usage_lists_the_new_forms(self):
        _, _, err = self.call(CliRunner(), ["plan"])
        for form in ("plan commit", "plan pin", "plan link <issue> refs/plans/<issue>"):
            self.assertIn(form, err)
