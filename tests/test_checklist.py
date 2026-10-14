import copy
import json
import pathlib
import unittest
from baton import checklist, config, planfile
from tests.fakes import FakeRunner

SHA = "a" * 40
BODY = f"""## Context
x

## Plan
Written against: {SHA}

- [x] **1. First step**
  - Files: `a.py`
  - Check: none
- [ ] **2. Second step**
  - Files: `b.py`
- [ ] **3. Third step**
  - Check: `tests/test_c.py`

## Decisions
- kept
"""

REVIEW = """<!-- baton:reviewer:review verdict=changes commit=%s -->
**Baton reviewer: review (changes requested, commit aaaaaaa)**

## Tasks
- [ ] **1.1 Clamp the estimate**
  - Problem: `x.py:5`
  - Fix:
    ```sh
    # not a heading
    cargo test x
    ```
- [ ] **1.2 Rename**

## Follow-up issues
- none
""" % SHA


class ParseTest(unittest.TestCase):
    def test_plan(self):
        found = checklist.plan(BODY)
        self.assertEqual(found["written_against"], SHA)
        self.assertEqual([(t["id"], t["done"]) for t in found["tasks"]],
                         [("1", True), ("2", False), ("3", False)])
        self.assertEqual(found["tasks"][1]["title"], "Second step")
        self.assertEqual(found["next"], "2")

    def test_no_plan(self):
        self.assertIsNone(checklist.plan("## Context\nx\n"))

    def test_fenced_hash_lines_are_not_headings(self):
        tasks = checklist.tasks_under(REVIEW, "tasks")
        self.assertEqual([t["id"] for t in tasks], ["1.1", "1.2"])
        self.assertEqual(checklist.tasks_under(REVIEW, "follow-up issues"), [])

    def test_crlf(self):
        found = checklist.plan(BODY.replace("\n", "\r\n"))
        self.assertEqual(found["next"], "2")
        self.assertEqual(found["written_against"], SHA)

    def test_progress(self):
        self.assertEqual(checklist.progress(checklist.plan(BODY)["tasks"]),
                         {"done": 1, "total": 3, "next": "2"})


class TickTest(unittest.TestCase):
    def test_ticks_only_that_box(self):
        new, changed = checklist.tick(BODY, "plan", "2")
        self.assertTrue(changed)
        self.assertEqual(new, BODY.replace("- [ ] **2.", "- [x] **2."))

    def test_already_ticked_is_unchanged(self):
        self.assertEqual(checklist.tick(BODY, "plan", "1"), (BODY, False))

    def test_unknown_task_or_section(self):
        with self.assertRaises(checklist.TaskError):
            checklist.tick(BODY, "plan", "9")
        with self.assertRaises(checklist.TaskError):
            checklist.tick(BODY, "tasks", "1")

    def test_review_task_and_crlf(self):
        crlf = REVIEW.replace("\n", "\r\n")
        new, changed = checklist.tick(crlf, "tasks", "1.2")
        self.assertTrue(changed)
        self.assertEqual(new, crlf.replace("- [ ] **1.2", "- [x] **1.2"))


class ReplaceTest(unittest.TestCase):
    NEW = "- [ ] **2. New second**\n  - Files: `d.py`\n- [ ] **3. New third**\n"

    def test_replaces_open_tasks_from_n(self):
        body = checklist.replace_plan(BODY, "2", self.NEW)
        found = checklist.plan(body)
        self.assertEqual([t["title"] for t in found["tasks"]],
                         ["First step", "New second", "New third"])
        self.assertIn(f"Written against: {SHA}", body)
        self.assertTrue(body.endswith("## Decisions\n- kept\n"))
        self.assertIn("  - Files: `a.py`", body)
        self.assertNotIn("b.py", body)

    def test_refuses_to_replace_a_done_task(self):
        with self.assertRaises(checklist.TaskError):
            checklist.replace_plan(BODY, "1", "- [ ] **1. Again**\n")

    def test_refuses_bad_numbering_or_ticked_or_text_first(self):
        for text in ("- [ ] **3. Skips two**\n", "- [x] **2. Done already**\n",
                     "Intro\n- [ ] **2. X**\n", "no tasks at all\n"):
            with self.subTest(text=text), self.assertRaises(checklist.TaskError):
                checklist.replace_plan(BODY, "2", text)

    def test_needs_a_plan(self):
        with self.assertRaises(checklist.TaskError):
            checklist.replace_plan("## Context\nx\n", "1", "- [ ] **1. X**\n")


def cfg():
    return config.Config(root=pathlib.Path("."), data=copy.deepcopy(config.DEFAULTS))


def issue_runner(body=BODY):
    return FakeRunner({"gh issue view 7 --json body": json.dumps({"body": body})})


class IssueForgeTest(unittest.TestCase):
    def test_tick_issue_writes_the_whole_body(self):
        runner = issue_runner()
        written = {}
        original = runner.__call__

        def capture(argv, cwd=None):
            if argv[:3] == ["gh", "issue", "edit"]:
                written["body"] = pathlib.Path(argv[argv.index("--body-file") + 1]).read_text()
            return original(argv, cwd)

        self.assertTrue(checklist.tick_issue(capture, 7, "2"))
        self.assertEqual(written["body"], BODY.replace("- [ ] **2.", "- [x] **2."))

    def test_tick_issue_already_done_writes_nothing(self):
        runner = issue_runner()
        self.assertFalse(checklist.tick_issue(runner, 7, "1"))
        self.assertFalse(any("issue edit" in line for line in runner.lines()))

    def test_replace_scans_new_text(self):
        runner = issue_runner()
        with self.assertRaises(checklist.TaskError):
            checklist.replace_issue_plan(cfg(), runner, 7, "2",
                                         "- [ ] **2. Copy from 192.168.1.20**\n", [])
        self.assertFalse(any("issue edit" in line for line in runner.lines()))

    def test_replace_refused_keeps_body(self):
        runner = issue_runner()
        with self.assertRaises(checklist.TaskError):
            checklist.replace_issue_plan(cfg(), runner, 7, "1", "- [ ] **1. X**\n", [])
        self.assertFalse(any("issue edit" in line for line in runner.lines()))

    def test_issue_plan_without_plan(self):
        with self.assertRaises(checklist.TaskError):
            checklist.issue_plan(issue_runner("## Context\nx\n"), 7)


LINK = planfile.link_line("o/r", "issue-7-x", "docs/baton/plans/7-x.md")
FILE = "## Pattern\nx\n\n## Plan\n\nWritten against: " + SHA + "\n\n- [x] **1. A**\n- [ ] **2. B**\n"


class LinkedPlanTest(unittest.TestCase):
    def runner(self, link=LINK):
        return FakeRunner({
            "gh issue view 7 --json body": json.dumps({"body": link + "\n\n## Context\nx\n"}),
            "git show origin/issue-7-x:docs/baton/plans/7-x.md": FILE})

    def test_plan_source_reads_the_file(self):
        runner = self.runner()
        text, link = checklist.plan_source(runner, LINK + "\n")
        self.assertEqual(text, FILE)
        self.assertEqual(link["ref"], "issue-7-x")
        self.assertIn("git fetch --quiet origin issue-7-x", runner.lines())

    def test_plan_source_legacy_body(self):
        runner = FakeRunner()
        self.assertEqual(checklist.plan_source(runner, BODY), (BODY, None))
        self.assertEqual(runner.calls, [])

    def test_plan_source_cache_fetches_once(self):
        runner, cache = self.runner(), {}
        for _ in range(2):
            checklist.plan_source(runner, LINK + "\n", cache)
        self.assertEqual(sum(1 for c in runner.lines() if "fetch" in c), 1)

    def test_issue_plan_linked(self):
        found = checklist.issue_plan(self.runner(), 7)
        self.assertEqual(found["source"], "file")
        self.assertEqual(found["ref"], "issue-7-x")
        self.assertEqual(found["path"], "docs/baton/plans/7-x.md")
        self.assertEqual(found["written_against"], SHA)
        self.assertEqual(found["next"], "2")

    def test_issue_plan_legacy_has_source_body(self):
        self.assertEqual(checklist.issue_plan(issue_runner(), 7)["source"], "body")

    def test_missing_file_raises(self):
        from baton.run import RunError

        def runner(argv, cwd=None):
            if argv[1] == "show":
                raise RunError("missing")
            return json.dumps({"body": LINK + "\n"})
        with self.assertRaises(checklist.TaskError):
            checklist.issue_plan(runner, 7)


class LinkedTickReplaceTest(unittest.TestCase):
    PATH = "docs/baton/plans/7-x.md"

    def issue(self, link=LINK):
        return json.dumps({"body": link + "\n\n## Context\nx\n"})

    def fake_worktree(self, base):
        def runner(argv, cwd=None):
            base(argv, cwd)
            if argv[:4] == ["git", "worktree", "add", "--detach"]:
                target = pathlib.Path(argv[4], self.PATH)
                target.parent.mkdir(parents=True)
                target.write_text(FILE)
            return base.replies.get(" ".join(argv[:4]), "")
        return runner

    def test_tick_edits_the_file_not_the_issue(self):
        import tempfile
        with tempfile.TemporaryDirectory() as cwd:
            target = pathlib.Path(cwd, self.PATH)
            target.parent.mkdir(parents=True)
            target.write_text(FILE)
            runner = FakeRunner({"gh issue view 7": self.issue()})
            self.assertTrue(checklist.tick_issue(runner, 7, "2", cwd=cwd))
            self.assertEqual(target.read_text(), FILE.replace("- [ ] **2.", "- [x] **2."))
            self.assertFalse(checklist.tick_issue(runner, 7, "2", cwd=cwd))
        self.assertFalse(any("issue edit" in line for line in runner.lines()))

    def test_tick_missing_file_raises(self):
        import tempfile
        with tempfile.TemporaryDirectory() as cwd:
            with self.assertRaises(checklist.TaskError):
                checklist.tick_issue(FakeRunner({"gh issue view 7": self.issue()}), 7, "2", cwd=cwd)

    def test_pinned_link_is_refused(self):
        pinned = planfile.link_line("o/r", SHA, self.PATH)
        runner = FakeRunner({"gh issue view 7": self.issue(pinned)})
        with self.assertRaises(checklist.TaskError):
            checklist.tick_issue(runner, 7, "2")
        with self.assertRaises(checklist.TaskError):
            checklist.replace_issue_plan(cfg(), runner, 7, "2", "- [ ] **2. X**\n", [])

    def test_replace_commits_and_pushes_without_editing_the_issue(self):
        base = FakeRunner({"gh issue view 7": self.issue()})
        seen = {}
        inner = self.fake_worktree(base)

        def runner(argv, cwd=None):
            if argv[:2] == ["git", "push"]:
                seen["push"] = (argv, cwd)
                seen["text"] = pathlib.Path(cwd, self.PATH).read_text()
            return inner(argv, cwd)

        found = checklist.replace_issue_plan(cfg(), runner, 7, "2",
                                             "- [ ] **2. New B**\n- [ ] **3. New C**\n", [])
        self.assertEqual([t["title"] for t in found["tasks"]], ["A", "New B", "New C"])
        argv, tree = seen["push"]
        self.assertEqual(argv, ["git", "push", "origin", "HEAD:issue-7-x"])
        self.assertIn("- [ ] **3. New C**", seen["text"])
        self.assertIn("git commit -q -m Replan from task 2", base.lines())
        self.assertIn(f"git worktree remove --force {tree}", base.lines())
        self.assertFalse(pathlib.Path(tree).exists())
        self.assertFalse(any("issue edit" in line for line in base.lines()))

    def test_replace_done_task_refused_before_commit(self):
        base = FakeRunner({"gh issue view 7": self.issue()})
        with self.assertRaises(checklist.TaskError):
            checklist.replace_issue_plan(cfg(), self.fake_worktree(base), 7, "1",
                                         "- [ ] **1. X**\n", [])
        lines = base.lines()
        self.assertFalse(any(l.startswith("git commit") or l.startswith("git push") for l in lines))
        self.assertTrue(any(l.startswith("git worktree remove") for l in lines))
