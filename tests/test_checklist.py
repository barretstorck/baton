import copy
import json
import os
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


class TaskLineTest(unittest.TestCase):
    def test_line_of_each_task(self):
        lines = BODY.splitlines()
        for task_id in ("1", "2", "3"):
            number = checklist.task_line(BODY, task_id)
            self.assertTrue(lines[number - 1].startswith(f"- [") and f"**{task_id}." in lines[number - 1])

    def test_unknown_task_or_no_plan(self):
        self.assertIsNone(checklist.task_line(BODY, "9"))
        self.assertIsNone(checklist.task_line("## Context\nx\n", "1"))
        self.assertIsNone(checklist.task_line("", "1"))

    def test_crlf(self):
        crlf = BODY.replace("\n", "\r\n")
        self.assertEqual(checklist.task_line(crlf, "2"), checklist.task_line(BODY, "2"))


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


class InsertTest(unittest.TestCase):
    PLAN = f"""## Plan
Written against: {SHA}

- [x] **1. One**
  - Files: `a.py`
- [ ] **2. Two**
  - Files: `b.py`
- [ ] **3. Three**
  - Uses: task 1 and task 2
- [ ] **4. Four**
  - Uses: task 4 and Task 3, see task 1.2

## Decisions
- kept
"""
    NEW = "- [ ] **3. Inserted**\n  - Uses: task 3\n"

    def test_renumbers_and_shifts_references(self):
        body = checklist.replace_plan(self.PLAN, "3", self.NEW, insert=True)
        found = checklist.plan(body)
        self.assertEqual([(t["id"], t["title"]) for t in found["tasks"]],
                         [("1", "One"), ("2", "Two"), ("3", "Inserted"),
                          ("4", "Three"), ("5", "Four")])
        self.assertIn("  - Uses: task 3\n- [ ] **4. Three**", body)
        self.assertIn("  - Uses: task 1 and task 2\n", body)
        self.assertIn("  - Uses: task 5 and Task 4, see task 1.2\n", body)
        self.assertTrue(body.endswith("## Decisions\n- kept\n"))

    def test_refuses_when_a_later_task_is_done(self):
        with self.assertRaises(checklist.TaskError):
            checklist.replace_plan(self.PLAN, "1", self.NEW, insert=True)

    def test_shifts_every_number_in_a_task_list(self):
        plan = self.PLAN.replace("task 4 and Task 3, see task 1.2",
                                 "tasks 2 and 3, tasks 1, 2 and 3, see task 1.2")
        body = checklist.replace_plan(plan, "2", "- [ ] **2. Inserted**\n", insert=True)
        self.assertIn("  - Uses: tasks 3 and 4, tasks 1, 3 and 4, see task 1.2\n", body)

    def test_inserting_after_the_last_task_appends(self):
        body = checklist.replace_plan(self.PLAN, "5", "- [ ] **5. Last**\n", insert=True)
        self.assertEqual([t["title"] for t in checklist.plan(body)["tasks"]],
                         ["One", "Two", "Three", "Four", "Last"])
        with self.assertRaises(checklist.TaskError):
            checklist.replace_plan(self.PLAN, "6", "- [ ] **6. Gap**\n", insert=True)

    def test_plan_changes_lists_the_shift(self):
        replaced, written = checklist.plan_changes(self.PLAN, "3", self.NEW, insert=True)
        self.assertEqual(replaced, [("3", "Three"), ("4", "Four")])
        self.assertEqual([i for i, _ in written], ["3", "4", "5"])


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


PHOTO = """## Plan
Written against: %s

- [x] **4. Four**
- [ ] **5. Five**
  - Files: `e.py`
- [ ] **6. Six**
  - Files: `f.py`

## Decisions
- kept
""" % SHA
MERGED = "- [ ] **5. Five and six**\n  - Files: `e.py`, `f.py`\n"


class DropTest(unittest.TestCase):
    def test_fewer_tasks_refused_naming_the_dropped(self):
        with self.assertRaises(checklist.TaskError) as caught:
            checklist.replace_plan(PHOTO, "5", MERGED)
        self.assertIn("6 (Six)", str(caught.exception))
        self.assertIn("--drop", str(caught.exception))

    def test_drop_accepts(self):
        body = checklist.replace_plan(PHOTO, "5", MERGED, drop=True)
        self.assertEqual([t["id"] for t in checklist.plan(body)["tasks"]], ["4", "5"])

    def test_equal_or_more_accepted(self):
        two = "- [ ] **5. A**\n- [ ] **6. B**\n- [ ] **7. C**\n"
        checklist.replace_plan(PHOTO, "5", "- [ ] **5. A**\n- [ ] **6. B**\n")
        checklist.replace_plan(PHOTO, "5", two)

    def test_plan_changes(self):
        self.assertEqual(checklist.plan_changes(PHOTO, "5", MERGED),
                         ([("5", "Five"), ("6", "Six")], [("5", "Five and six")]))

    def test_body_plan_issue_level(self):
        runner = issue_runner(PHOTO)
        with self.assertRaises(checklist.TaskError):
            checklist.replace_issue_plan(cfg(), runner, 7, "5", MERGED, [])
        self.assertFalse(any("issue edit" in line for line in runner.lines()))
        found = checklist.replace_issue_plan(cfg(), runner, 7, "5", MERGED, [], drop=True)
        self.assertEqual(found["dropped"], [("6", "Six")])
        self.assertEqual(found["replaced"], [("5", "Five"), ("6", "Six")])
        self.assertTrue(any("issue edit" in line for line in runner.lines()))


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


REF_LINK = planfile.link_line("o/r", "refs/plans/7", "docs/baton/plans/7-x.md")


class TickedByTest(unittest.TestCase):
    def plan(self, link, ticked):
        from unittest import mock
        runner = issue_runner(link + "\n")
        with mock.patch.object(planfile, "read", return_value=FILE), \
                mock.patch.object(planfile, "tick_commits", return_value=ticked):
            return checklist.issue_plan(runner, 7)

    def test_ref_plan_marks_done_tasks_only(self):
        found = self.plan(REF_LINK, {"1": "abc1234 Task 1: A"})
        self.assertEqual(found["tasks"][0]["ticked_by"], "abc1234 Task 1: A")
        self.assertNotIn("ticked_by", found["tasks"][1])

    def test_branch_plan_has_no_ticked_by(self):
        found = self.plan(planfile.link_line("o/r", "issue-7-x", "docs/baton/plans/7-x.md"),
                          {"1": "abc1234 Task 1: A"})
        self.assertTrue(all("ticked_by" not in t for t in found["tasks"]))

    def test_body_plan_has_no_ticked_by(self):
        found = checklist.issue_plan(issue_runner(), 7)
        self.assertTrue(all("ticked_by" not in t for t in found["tasks"]))


LINK = planfile.link_line("o/r", "issue-7-x", "docs/baton/plans/7-x.md")
FILE = "## Pattern\nx\n\n## Plan\n\nWritten against: " + SHA + "\n\n- [x] **1. A**\n- [ ] **2. B**\n"


class BadLinkTest(unittest.TestCase):
    BAD = [("--upload-pack=x", "docs/baton/plans/7-x.md"), ("issue-7-x", "../../x.md"),
           ("issue-7-x", "/etc/x.md"), ("issue-7-x", "docs/baton/other/x.md")]

    def runner(self, ref, path):
        link = f"Plan: [{path}](https://github.com/o/r/blob/{ref}/{path})"
        return FakeRunner({
            "gh issue view 7 --json body": json.dumps({"body": link + "\n\n## Context\nx\n"})})

    def test_tick_issue_refuses_without_running_or_writing(self):
        import tempfile
        for ref, path in self.BAD:
            runner = self.runner(ref, path)
            with self.subTest(ref=ref, path=path), tempfile.TemporaryDirectory() as tree:
                with self.assertRaises(checklist.TaskError):
                    checklist.tick_issue(runner, 7, "1", cwd=tree)
                self.assertEqual(runner.lines(), ["gh issue view 7 --json body"])
                self.assertEqual(os.listdir(tree), [])

    def test_replace_issue_plan_refuses_without_git_or_edit(self):
        for ref, path in self.BAD:
            runner = self.runner(ref, path)
            with self.subTest(ref=ref, path=path), self.assertRaises(checklist.TaskError):
                checklist.replace_issue_plan(cfg(), runner, 7, "1", "- [ ] **1. X**\n", [])
            self.assertEqual(runner.lines(), ["gh issue view 7 --json body"])

    def test_issue_plan_refuses_without_git(self):
        runner = self.runner(*self.BAD[0])
        with self.assertRaises(checklist.TaskError):
            checklist.issue_plan(runner, 7)
        self.assertEqual(runner.lines(), ["gh issue view 7 --json body"])


class LinkedPlanTest(unittest.TestCase):
    def runner(self, link=LINK):
        return FakeRunner({
            "gh issue view 7 --json body": json.dumps({"body": link + "\n\n## Context\nx\n"}),
            "git show --end-of-options origin/issue-7-x:docs/baton/plans/7-x.md": FILE})

    def test_plan_source_reads_the_file(self):
        runner = self.runner()
        text, link = checklist.plan_source(runner, LINK + "\n")
        self.assertEqual(text, FILE)
        self.assertEqual(link["ref"], "issue-7-x")
        self.assertIn("git fetch --quiet --end-of-options origin issue-7-x", runner.lines())

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

    def test_replace_refuses_a_branch_that_is_not_the_issues_own(self):
        for branch in ("main", "issue-8-x", "issue-7-"):
            link = planfile.link_line("o/r", branch, "docs/baton/plans/7-x.md")
            base = FakeRunner({"gh issue view 7": self.issue(link)})
            with self.subTest(branch=branch), self.assertRaises(checklist.TaskError):
                checklist.replace_issue_plan(cfg(), self.fake_worktree(base), 7, "2",
                                             "- [ ] **2. X**\n", [])
            self.assertEqual(base.lines(), ["gh issue view 7 --json body"])

    def test_tick_refuses_a_symlinked_plan_path(self):
        import tempfile
        with tempfile.TemporaryDirectory() as cwd, tempfile.TemporaryDirectory() as other:
            victim = pathlib.Path(other, "victim.md")
            victim.write_text(FILE)
            target = pathlib.Path(cwd, self.PATH)
            target.parent.mkdir(parents=True)
            target.symlink_to(victim)
            runner = FakeRunner({"gh issue view 7": self.issue()})
            with self.assertRaises(checklist.TaskError):
                checklist.tick_issue(runner, 7, "2", cwd=cwd)
            self.assertEqual(victim.read_text(), FILE)

    def test_tick_refuses_a_symlinked_parent_folder(self):
        import tempfile
        with tempfile.TemporaryDirectory() as cwd, tempfile.TemporaryDirectory() as other:
            pathlib.Path(other, "plans").mkdir()
            victim = pathlib.Path(other, "plans", "7-x.md")
            victim.write_text(FILE)
            pathlib.Path(cwd, "docs").mkdir()
            pathlib.Path(cwd, "docs", "baton").symlink_to(other)
            runner = FakeRunner({"gh issue view 7": self.issue()})
            with self.assertRaises(checklist.TaskError):
                checklist.tick_issue(runner, 7, "2", cwd=cwd)
            self.assertEqual(victim.read_text(), FILE)

    def test_replace_refuses_a_symlinked_plan_in_the_branch(self):
        base = FakeRunner({"gh issue view 7": self.issue()})

        def runner(argv, cwd=None):
            out = base(argv, cwd)
            if argv[:4] == ["git", "worktree", "add", "--detach"]:
                target = pathlib.Path(argv[4], self.PATH)
                target.parent.mkdir(parents=True)
                target.symlink_to("/etc/hostname")
            return out

        with self.assertRaises(checklist.TaskError):
            checklist.replace_issue_plan(cfg(), runner, 7, "2", "- [ ] **2. X**\n", [])
        self.assertFalse(any(l.startswith(("git commit", "git push")) for l in base.lines()))


class LinkedDropTest(unittest.TestCase):
    PATH = LinkedTickReplaceTest.PATH
    issue = LinkedTickReplaceTest.issue
    fake_worktree = LinkedTickReplaceTest.fake_worktree
    FILE = FILE.replace("- [ ] **2. B**\n", "- [ ] **2. B**\n- [ ] **3. C**\n")

    def runner(self):
        base = FakeRunner({"gh issue view 7": self.issue()})
        original = self.fake_worktree(base)

        def run(argv, cwd=None):
            result = original(argv, cwd)
            if argv[:4] == ["git", "worktree", "add", "--detach"]:
                pathlib.Path(argv[4], self.PATH).write_text(self.FILE)
            return result
        return base, run

    def test_refused_without_drop_before_commit(self):
        base, run = self.runner()
        with self.assertRaises(checklist.TaskError):
            checklist.replace_issue_plan(cfg(), run, 7, "2", "- [ ] **2. BC**\n", [])
        self.assertFalse(any(l.startswith("git commit") or l.startswith("git push")
                             for l in base.lines()))

    def test_drop_names_the_tasks_in_the_commit(self):
        base, run = self.runner()
        found = checklist.replace_issue_plan(cfg(), run, 7, "2", "- [ ] **2. BC**\n", [],
                                             drop=True)
        self.assertEqual(found["dropped"], [("3", "C")])
        commit = [l for l in base.lines() if l.startswith("git commit")][0]
        self.assertIn("Replan from task 2", commit)
        self.assertIn("Dropped: 3 (C)", commit)
        self.assertFalse(any("issue edit" in l for l in base.lines()))

    def test_no_dropped_line_when_nothing_dropped(self):
        base, run = self.runner()
        checklist.replace_issue_plan(cfg(), run, 7, "2", "- [ ] **2. B**\n- [ ] **3. C**\n", [])
        self.assertIn("git commit -q -m Replan from task 2", base.lines())


class RefPlanTest(unittest.TestCase):
    """A plan kept at refs/plans/<N>: ticks and replans are commits on that ref."""
    PATH = "docs/baton/plans/7-x.md"
    REF = "refs/plans/7"
    NEW = "c" * 40
    TEXT = ("## Plan\n\nWritten against: " + SHA + "\n\n- [x] **1. A**\n  - Provides: `f()`\n"
            "- [ ] **2. B**\n  - Provides: `g()`\n  - Check: none\n- [ ] **3. C**\n")

    def runner(self):
        link = planfile.link_line("o/r", self.REF, self.PATH)
        return FakeRunner({
            "gh issue view 7": json.dumps({"body": link + "\n\n## Context\nx\n"}),
            "git show": self.TEXT, "git hash-object": "b1\n", "git mktree": "t\n",
            "git commit-tree": self.NEW + "\n", "git ls-remote": f"{SHA}\t{self.REF}\n"})

    @staticmethod
    def written(runner):
        return next(i for c, i in zip(runner.calls, runner.inputs)
                    if c[:2] == ["git", "hash-object"])

    def tick(self, runner, task, **kwargs):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = checklist.tick_issue(runner, 7, task, **kwargs)
        return result, out.getvalue()

    def test_tick_commits_to_the_ref_and_leaves_cwd_alone(self):
        import tempfile
        runner = self.runner()
        with tempfile.TemporaryDirectory() as cwd:
            changed, out = self.tick(runner, "2", cwd=cwd)
            self.assertEqual(os.listdir(cwd), [])
        self.assertTrue(changed)
        lines = runner.lines()
        self.assertIn("git fetch --quiet origin +refs/plans/7:refs/baton/plans/7", lines)
        self.assertIn(f"git show --end-of-options refs/baton/plans/7:{self.PATH}", lines)
        self.assertTrue(any(l.startswith("git commit-tree t -p refs/baton/plans/7 -m Task 2")
                            for l in lines))
        self.assertIn("- [x] **2. B**", self.written(runner))
        self.assertIn("- [ ] **3. C**", self.written(runner))
        self.assertFalse(any(l.startswith("git push") or "issue edit" in l for l in lines))
        self.assertIn("plan commit " + self.NEW, out)
        self.assertIn("push with: git push --atomic origin HEAD:refs/heads/<branch> "
                      + self.NEW + ":refs/plans/7", out)

    def test_second_tick_is_a_no_op(self):
        runner = self.runner()
        changed, out = self.tick(runner, "1")
        self.assertFalse(changed)
        self.assertEqual(out, "")
        self.assertFalse(any("commit-tree" in l or "hash-object" in l for l in runner.lines()))

    def test_as_built_line_goes_under_provides(self):
        runner = self.runner()
        self.tick(runner, "2", as_built="`g(x)`")
        self.assertIn("  - Provides: `g()`\n  - Provides (as built): `g(x)`\n  - Check: none\n",
                      self.written(runner))
        self.assertEqual(self.written(runner).count("(as built)"), 1)

    def test_as_built_needs_a_provides_line(self):
        with self.assertRaises(checklist.TaskError):
            checklist.add_as_built(self.TEXT, "plan", "3", "x")
        with self.assertRaises(checklist.TaskError):
            checklist.add_as_built(self.TEXT, "plan", "9", "x")

    def test_as_built_is_refused_for_other_plans(self):
        with self.assertRaises(checklist.TaskError):
            checklist.tick_issue(issue_runner(), 7, "2", as_built="x")

    def test_replace_commits_and_pushes_to_the_ref(self):
        runner = self.runner()
        found = checklist.replace_issue_plan(cfg(), runner, 7, "2",
                                             "- [ ] **2. New B**\n- [ ] **3. New C**\n", [])
        self.assertEqual([t["title"] for t in found["tasks"]], ["A", "New B", "New C"])
        lines = runner.lines()
        self.assertTrue(any(l.startswith("git commit-tree t -p refs/baton/plans/7 "
                                         "-m Replan from task 2") for l in lines))
        self.assertEqual(lines[-1], f"git push origin {self.NEW}:refs/plans/7")
        self.assertFalse(any("issue edit" in l or "worktree" in l for l in lines))

    def test_insert_commits_the_renumbered_plan_to_the_ref(self):
        runner = self.runner()
        found = checklist.replace_issue_plan(cfg(), runner, 7, "3",
                                             "- [ ] **3. New**\n", [], insert=True)
        self.assertEqual([t["title"] for t in found["tasks"]], ["A", "B", "New", "C"])
        lines = runner.lines()
        self.assertTrue(any(l.startswith("git commit-tree t -p refs/baton/plans/7 "
                                         "-m Insert 1 task before task 3") for l in lines))
        self.assertIn("- [ ] **4. C**", self.written(runner))
        self.assertEqual(lines[-1], f"git push origin {self.NEW}:refs/plans/7")

    def test_replace_done_task_is_refused_before_commit(self):
        runner = self.runner()
        with self.assertRaises(checklist.TaskError):
            checklist.replace_issue_plan(cfg(), runner, 7, "1", "- [ ] **1. X**\n", [])
        self.assertFalse(any("commit-tree" in l or "push" in l for l in runner.lines()))


class WrittenAgainstTest(unittest.TestCase):
    NEW = "d" * 40

    def test_changes_only_the_line_and_keeps_boxes(self):
        text = RefPlanTest.TEXT + "\n## Other\nWritten against: " + SHA + "\n"
        new = checklist.set_written_against(text, self.NEW)
        self.assertEqual(new, text.replace(f"Written against: {SHA}\n\n- [x]",
                                           f"Written against: {self.NEW}\n\n- [x]", 1))
        self.assertEqual(new.count(SHA), 1)
        self.assertEqual(checklist.plan(new)["tasks"], checklist.plan(text)["tasks"])

    def test_refusals(self):
        for sha in ("abc", "g" * 40, SHA.upper()):
            with self.subTest(sha=sha), self.assertRaises(checklist.TaskError):
                checklist.set_written_against(RefPlanTest.TEXT, sha)
        with self.assertRaises(checklist.TaskError):
            checklist.set_written_against("- [ ] **1. A**\n", self.NEW)
        with self.assertRaises(checklist.TaskError):
            checklist.set_written_against("## Plan\n\n- [ ] **1. A**\n", self.NEW)

    def test_ref_plan_commits_once_and_pushes(self):
        runner = RefPlanTest().runner()
        found = checklist.against_issue_plan(cfg(), runner, 7, self.NEW)
        self.assertEqual(len(found["tasks"]), 3)
        lines = runner.lines()
        self.assertEqual(sum(l.startswith("git commit-tree") for l in lines), 1)
        self.assertTrue(any("-m Re-refine: written against ddddddd" in l for l in lines))
        self.assertIn(f"Written against: {self.NEW}", RefPlanTest.written(runner))
        self.assertEqual(lines[-1], f"git push origin {RefPlanTest.NEW}:refs/plans/7")
        self.assertFalse(any("issue edit" in l for l in lines))

    def test_branch_or_body_plan_is_refused(self):
        with self.assertRaises(checklist.TaskError):
            checklist.against_issue_plan(cfg(), issue_runner(), 7, self.NEW)
        link = planfile.link_line("o/r", "issue-7-x", "docs/baton/plans/7-x.md")
        runner = FakeRunner({"gh issue view 7": json.dumps({"body": link + "\n"})})
        with self.assertRaises(checklist.TaskError):
            checklist.against_issue_plan(cfg(), runner, 7, self.NEW)
        self.assertFalse(any(l.startswith("git push") for l in runner.lines()))


class NamedPathsTest(unittest.TestCase):
    def test_open_tasks_only_and_filtering(self):
        text = f"""## Plan
Written against: {SHA}

- [x] **1. Done**
  - Files: `done.py`
- [ ] **2. Open**
  - Files: `lib/a.py`, `tests/new_test.py` (new), `README.md`
  - Uses: `f(x) -> int` (`lib/b.py:12-30`), `span(text, heading)`, `<placeholder>.py`
  - Port from: `ref/c.rs:5`
  - Check: `tests/test_zzz.py`
- [ ] **3. Other**
  - Files: `lib/a.py`, `Makefile`

## Decisions
- `outside.py`
"""
        self.assertEqual(checklist.named_paths(text),
                         ["README.md", "lib/a.py", "lib/b.py", "ref/c.rs"])

    def test_no_plan(self):
        self.assertEqual(checklist.named_paths("## Context\nx"), [])
