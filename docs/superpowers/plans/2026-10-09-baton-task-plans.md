# Task plans and quota use Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut a sprint's quota use by working each issue as a plan of small tasks, one fresh
developer per task, with review fixes worked the same way, and measure the result with
`baton usage`.

**Architecture:** A new pure-text module `checklist.py` parses and edits task checklists (an
issue's `## Plan`, a review's `## Tasks`) and does the issue-side forge I/O. `review.py` gains the
review-side task functions and refuses an approval while tasks are open. `sprint.py` reports task
progress, `merge.py` also removes the detached worktrees task agents leave, and a new `usage.py`
reads local transcripts. The skills change from "one developer per issue" to "one developer per
task".

**As built (2026-10-09):** Task 6 matches task worktrees by the pull request's own commit ids
rather than by ancestry, which also matched worktrees on older default-branch commits. `baton
usage` also recognises the pre-0.3 prompt `Implement issue #N` and agent types containing
`baton-developer`. The review body's other headings are `##`. Two more dispatch prompts exist:
`Fix CI on pull request <P> (issue <N>).` and `Update pull request <P> (issue <N>).`

**Tech Stack:** Python 3 standard library only, `unittest`, `gh` and `git` through the injected
`runner`; Markdown skills and agent files.

**Spec:** `docs/superpowers/specs/2026-10-01-baton-design.md`, revision 4 (sections 4 "The plan",
6, 7, 7a, 7b).

## Global Constraints

- Standard library only; no PyYAML.
- Every forge and git call goes through the `runner(argv, cwd=None)` callable so tests use
  `tests.fakes.FakeRunner`.
- Agents never edit checklists: only `baton task done` and `baton plan replace` change boxes or
  tasks.
- New text published to the forge is scanned with `scan.scan_text(text, terms, cfg.data["privacy_allow"])`.
- No private details (names, hosts, paths, IPs) in code, tests, docs or commits.
- Commits: author `Barret Storck <113543095+barretstorck@users.noreply.github.com>`, message ends
  with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. One commit per task, pushed to
  `main`.
- Run the suite with `cd /app/baton && python3 -m unittest discover -s tests -t .`.
- Dispatch prompts, exactly (the sprint skill writes them, `baton usage` reads them):
  `Do task <n> of issue <N>.`, `Do review task <r.n> of pull request <P> (issue <N>).`,
  `Do spike issue <N>.`, `Review pull request <P> (issue <N>).`, `Refine issues <N>, <M>.`

## Review Focus

- An issue body edited in GitHub's web editor has `\r\n` line endings: parsing, ticking and
  replacing must work on it and keep the other lines byte for byte (Task 1).
- A review body with a fenced code block containing `# comment` lines: those are not headings, so
  `## Tasks` must still end at the next real heading (Task 1).
- `baton task done` on a task that is already ticked: no forge write, exit 0, says so (Tasks 3, 4).
- `baton plan replace` starting at a task that is already done: refused, description untouched
  (Tasks 1, 3).
- `baton review post ... approve` while a task of the previous review is still open: refused
  before anything is posted (Task 4).

---

### Task 1: `checklist.py`, the text side

**Files:**
- Create: `lib/baton/checklist.py`
- Test: `tests/test_checklist.py`

**Interfaces:**
- Produces: `TaskError(Exception)`; `span(text, heading) -> (start, end) | None`;
  `parse(section) -> [{"id": str, "title": str, "done": bool}]`;
  `tasks_under(text, heading) -> list` (parse of that section, `[]` when absent);
  `plan(body) -> {"written_against": str|None, "tasks": list, "next": str|None} | None`;
  `tick(text, heading, task_id) -> (new_text, changed: bool)`;
  `replace_plan(body, from_id: str, new_text) -> new_body`;
  `progress(tasks) -> {"done": int, "total": int, "next": str|None}`.

- [ ] **Step 1: Write the failing tests**

```python
import unittest
from baton import checklist

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
```

- [ ] **Step 2: Run to see it fail**

Run: `python3 -m unittest tests.test_checklist -v`
Expected: ERROR, `cannot import name 'checklist'`.

- [ ] **Step 3: Implement**

```python
"""Task checklists: an issue's plan and a review's fix list.

Design spec, sections 4 ("The plan"), 6 and 7a. Only Baton's commands tick a box
or replace open tasks; agents never edit a checklist by hand.
"""
import re

_HEADING = re.compile(r"(#{1,6})\s+(.+?)\s*$")
_TASK = re.compile(r"^[-*] \[([ xX])\] \*\*(\d+(?:\.\d+)?)\.?\s+(.+?)\*\*\s*$")
_AGAINST = re.compile(r"^\s*Written against:\s*([0-9a-f]{40})\b", re.M)


class TaskError(Exception):
    pass


def _headings(text):
    """(level, lower-cased name, line start, line end) for each heading outside code fences."""
    found, fenced, offset = [], False, 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fenced = not fenced
        elif not fenced:
            match = _HEADING.match(line.rstrip("\r\n"))
            if match:
                found.append((len(match.group(1)), match.group(2).lower(),
                              offset, offset + len(line)))
        offset += len(line)
    return found


def span(text, heading):
    """(start, end) of the text under `heading`, up to the next heading of its level or higher."""
    text = text or ""
    found = _headings(text)
    for index, (level, name, _, line_end) in enumerate(found):
        if name == heading.lower():
            end = next((start for lvl, _, start, _ in found[index + 1:] if lvl <= level),
                       len(text))
            return line_end, end
    return None


def _match(line):
    return _TASK.match(line.rstrip("\r\n"))


def parse(section):
    tasks = []
    for line in (section or "").splitlines():
        match = _match(line)
        if match:
            tasks.append({"id": match.group(2), "title": match.group(3),
                          "done": match.group(1) != " "})
    return tasks


def tasks_under(text, heading):
    where = span(text, heading)
    return parse(text[where[0]:where[1]]) if where else []


def progress(tasks):
    return {"done": sum(1 for task in tasks if task["done"]), "total": len(tasks),
            "next": next((task["id"] for task in tasks if not task["done"]), None)}


def plan(body):
    where = span(body, "plan")
    if where is None:
        return None
    section = body[where[0]:where[1]]
    against = _AGAINST.search(section)
    tasks = parse(section)
    return {"written_against": against.group(1) if against else None, "tasks": tasks,
            "next": progress(tasks)["next"]}


def tick(text, heading, task_id):
    """`text` with task `task_id` ticked under `heading`, and whether anything changed."""
    where = span(text, heading)
    if where is None:
        raise TaskError(f"no '{heading}' section")
    start, end = where
    lines = text[start:end].splitlines(keepends=True)
    for index, line in enumerate(lines):
        match = _match(line)
        if match and match.group(2) == task_id:
            if match.group(1) != " ":
                return text, False
            lines[index] = line.replace("[ ]", "[x]", 1)
            return text[:start] + "".join(lines) + text[end:], True
    raise TaskError(f"no task {task_id} under '{heading}'")


def _blocks(section):
    head, blocks = [], []
    for line in section.splitlines(keepends=True):
        match = _match(line)
        if match:
            blocks.append({"id": match.group(2), "done": match.group(1) != " ", "lines": [line]})
        elif blocks:
            blocks[-1]["lines"].append(line)
        else:
            head.append(line)
    return head, blocks


def replace_plan(body, from_id, new_text):
    """`body` with the plan's tasks from `from_id` on replaced by `new_text`'s tasks."""
    where = span(body, "plan")
    if where is None:
        raise TaskError("the issue has no '## Plan' section")
    if not str(from_id).isdigit():
        raise TaskError("plan tasks are numbered 1, 2, 3 ...")
    first = int(from_id)
    start, end = where
    head, blocks = _blocks(body[start:end])
    if any(not block["id"].isdigit() for block in blocks):
        raise TaskError("the plan has a task that is not numbered 1, 2, 3 ...")
    done = [block["id"] for block in blocks if int(block["id"]) >= first and block["done"]]
    if done:
        raise TaskError(f"task {done[0]} is done; replace from a later task")
    newline = "\r\n" if "\r\n" in body else "\n"
    new_text = new_text.replace("\r\n", "\n").strip("\n").replace("\n", newline) + newline
    new_head, new_blocks = _blocks(new_text)
    if any(line.strip() for line in new_head) or not new_blocks:
        raise TaskError("the new text must start with a task line: - [ ] **<n>. <title>**")
    for offset, block in enumerate(new_blocks):
        if block["done"] or block["id"] != str(first + offset):
            raise TaskError(f"the new tasks must be open and numbered from {first}")
    kept = [line for block in blocks if int(block["id"]) < first for line in block["lines"]]
    if kept and not kept[-1].strip():
        kept = kept[:-1]
    section = "".join(head + kept + [line for block in new_blocks for line in block["lines"]])
    if end < len(body):
        section += newline
    return body[:start] + section + body[end:]
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest tests.test_checklist -v` — expected: all pass. Then the whole suite.

- [ ] **Step 5: Commit** — `git add lib/baton/checklist.py tests/test_checklist.py`, message
  "Add checklist: parse, tick and replace task lists".

### Task 2: the plan in the definition of ready

**Files:**
- Modify: `lib/baton/issue.py` (`ready_problems`)
- Modify: `templates/issue.md`
- Test: `tests/test_issue.py`

**Interfaces:**
- Consumes: `checklist.plan(body)`.
- Produces: two new problems from `ready_problems` for issues that are not spikes.

- [ ] **Step 1: Write the failing tests.** Add a `## Plan` section to the test file's `BODY`
  (between `## Verification` and `## Decisions`) so existing ready tests keep passing:

```python
## Plan
Written against: 0123456789abcdef0123456789abcdef01234567

- [ ] **1. Add the flag**
  - Files: `src/cli.py`
  - Check: `scripts/test.sh tests/test_cli.py`
```

and add:

```python
class PlanReadyTest(unittest.TestCase):
    def problems(self, body, labels=("type:feature",)):
        return issue.ready_problems(make(body=body, labels=labels), cfg(), OWNER)

    def test_missing_plan(self):
        body = BODY[:BODY.index("## Plan")] + BODY[BODY.index("## Decisions"):]
        self.assertTrue(any("Plan section" in p for p in self.problems(body)))
        self.assertFalse(any("Plan" in p for p in self.problems(body, ("type:spike",))))

    def test_plan_needs_written_against_and_numbered_tasks(self):
        no_sha = BODY.replace("Written against: 0123456789abcdef0123456789abcdef01234567\n", "")
        self.assertTrue(any("Written against" in p for p in self.problems(no_sha)))
        renumbered = BODY.replace("**1. Add the flag**", "**2. Add the flag**")
        self.assertTrue(any("numbered 1, 2, 3" in p for p in self.problems(renumbered)))
```

- [ ] **Step 2: Run** `python3 -m unittest tests.test_issue -v`; expected: the new tests FAIL.

- [ ] **Step 3: Implement.** In `issue.py` add `from . import checklist` and, after the
  `Expected files` check in `ready_problems`:

```python
    if not spike:
        found = checklist.plan(body)
        if found is None or not found["tasks"]:
            problems.append("Plan section with at least one task is missing "
                            "(- [ ] **1. <title>** with Files, Uses, Provides and Check lines)")
        else:
            if not found["written_against"]:
                problems.append("the plan needs a 'Written against: <full commit sha>' line")
            ids = [task["id"] for task in found["tasks"]]
            if ids != [str(n) for n in range(1, len(ids) + 1)]:
                problems.append("plan tasks must be numbered 1, 2, 3 ... in order")
```

  In `templates/issue.md`, add before `## Decisions`:

```markdown
## Plan

Written against: (full commit sha of the default branch the plan was written from)

- [ ] **1. One unit of work with its own check**
  - Files: `path/one` (new), `path/two`
  - Uses: the exact signatures this task relies on, with `file:line`
  - Provides: the exact signatures later tasks rely on
  - Port from: `path:first-last` (when porting; leave out otherwise)
  - Check: the test or subset that proves this task done, a build, or "none"

(Not for spikes. Tasks run in order, one developer each. No code here; the developer writes it.)
```

- [ ] **Step 4: Run** the issue tests and the whole suite; all pass.
- [ ] **Step 5: Commit** "Ready means a plan of tasks, except for spikes".

### Task 3: issue tasks on the forge, and the CLI

**Files:**
- Modify: `lib/baton/checklist.py` (forge functions)
- Modify: `lib/baton/cli.py` (`tasks`, `task`, `plan`)
- Test: `tests/test_checklist.py`

**Interfaces:**
- Consumes: Task 1 functions; `scan.scan_text`, `scan.describe`.
- Produces: `issue_body(runner, number) -> str`; `issue_plan(runner, number) -> dict`
  (raises `TaskError` without a plan); `tick_issue(runner, number, task_id) -> bool`;
  `replace_issue_plan(cfg, runner, number, from_id, new_text, terms) -> dict` (the new plan);
  CLI `baton tasks issue <N>`, `baton task done issue <N> <n>`,
  `baton plan replace <N> <from> <file|->`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_checklist.py`):

```python
import copy, json, pathlib
from baton import config
from tests.fakes import FakeRunner


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
```

  (`192.168.1.20` is a generic private-range address; `scan` reports it as `private-ip`.)

- [ ] **Step 2: Run** `python3 -m unittest tests.test_checklist -v`; new tests FAIL.

- [ ] **Step 3: Implement** in `checklist.py` (add `import json, os, tempfile` and
  `from . import scan`):

```python
def issue_body(runner, number):
    return json.loads(runner(["gh", "issue", "view", str(number), "--json", "body"]))["body"] or ""


def issue_plan(runner, number):
    found = plan(issue_body(runner, number))
    if found is None:
        raise TaskError(f"issue #{number} has no '## Plan' section")
    return found


def _save_issue(runner, number, body):
    if not body.strip():
        raise TaskError("refusing to write an empty description")
    handle, path = tempfile.mkstemp(suffix=".md")
    try:
        with os.fdopen(handle, "w", newline="") as out:
            out.write(body)
        runner(["gh", "issue", "edit", str(number), "--body-file", path])
    finally:
        os.unlink(path)


def tick_issue(runner, number, task_id):
    """Tick a plan task. False when it was already ticked (nothing is written)."""
    new, changed = tick(issue_body(runner, number), "plan", str(task_id))
    if changed:
        _save_issue(runner, number, new)
    return changed


def replace_issue_plan(cfg, runner, number, from_id, new_text, terms):
    findings = scan.scan_text(new_text, terms, cfg.data["privacy_allow"])
    if findings:
        raise TaskError("plan not changed; privacy scan found: "
                        + "; ".join(scan.describe(findings)))
    new = replace_plan(issue_body(runner, number), str(from_id), new_text)
    _save_issue(runner, number, new)
    return plan(new)
```

  In `cli.py` import `checklist` and add:

```python
def cmd_tasks(args):
    if len(args) == 2 and args[0] == "issue" and args[1].isdigit():
        _require_config()
        found = checklist.issue_plan(run.run, int(args[1]))
        print(json.dumps({**found, **checklist.progress(found["tasks"])}, indent=2))
        return 0
    if len(args) == 2 and args[0] == "pr" and args[1].isdigit():
        _require_config()
        pr = json.loads(run.run(["gh", "pr", "view", args[1], "--json", "comments"]))
        print(json.dumps(review.review_tasks(pr, _owner()), indent=2))
        return 0
    print("usage: baton tasks <issue|pr> <number>", file=sys.stderr)
    return 64


def cmd_task(args):
    if len(args) == 4 and args[0] == "done" and args[1] in ("issue", "pr") and args[2].isdigit():
        _require_config()
        if args[1] == "issue":
            changed = checklist.tick_issue(run.run, int(args[2]), args[3])
        else:
            changed = review.tick_review(run.run, int(args[2]), _owner(), args[3])
        print(f"ticked task {args[3]}" if changed else f"task {args[3]} was already ticked")
        return 0
    print("usage: baton task done <issue|pr> <number> <task>", file=sys.stderr)
    return 64


def cmd_plan(args):
    if len(args) == 4 and args[0] == "replace" and args[1].isdigit() and args[2].isdigit():
        text = comment.read_body(args[3], sys.stdin)
        found = checklist.replace_issue_plan(_require_config(), run.run, int(args[1]), args[2],
                                             text, scan.load_terms())
        print(json.dumps(found, indent=2))
        return 0
    print("usage: baton plan replace <issue> <from-task> <file | ->", file=sys.stderr)
    return 64
```

  Register `"plan": cmd_plan, "task": cmd_task, "tasks": cmd_tasks` in `COMMANDS`, and add
  `checklist.TaskError, comment.CommentError` to the exceptions `main` reports (print
  `baton: <error>`, return 1 for these two; keep 2 for the existing ones). `review.review_tasks`
  and `review.tick_review` arrive in Task 4; until then `cmd_tasks pr` / `cmd_task ... pr` are
  untested.

- [ ] **Step 4: Run** the checklist tests and the whole suite; all pass. Smoke:
  `bin/baton tasks` prints usage and exits 64.
- [ ] **Step 5: Commit** "Add baton tasks, task done and plan replace for issues".

### Task 4: review tasks, and no approval with open tasks

**Files:**
- Modify: `lib/baton/review.py`
- Modify: `lib/baton/cli.py` (`cmd_review` passes the owner to `post`)
- Test: `tests/test_review.py`

**Interfaces:**
- Consumes: `checklist.tasks_under`, `checklist.tick`, `checklist.progress`,
  `checklist.issue_body`, `checklist.plan`, `checklist.TaskError`.
- Produces: `review_tasks(pr, owner) -> {"round": int, "review": url|None, "tasks": list, "done", "total", "next"}`;
  `tick_review(runner, number, owner, task_id) -> bool`;
  `post(cfg, runner, number, verdict, commit, body, terms, owner)` (new `owner` argument).

- [ ] **Step 1: Write the failing tests.** Update the two existing `post` tests to the new
  signature and the new `gh pr view` fields, and add:

```python
TASKS = "## Tasks\n- [ ] **1.1 Fix it**\n  - Check: none\n"


def changes_with_tasks(commit=NEW, ticked=False):
    body = TASKS.replace("[ ]", "[x]") if ticked else TASKS
    return {"author": {"login": OWNER}, "url": "https://x/pull/9#issuecomment-55",
            "body": review.compose("changes", commit, body)}


def pr_runner(comments=(), closing=(), issue_body=""):
    return FakeRunner({
        "gh pr view 9 --json headRefOid,comments,closingIssuesReferences": json.dumps(
            {"headRefOid": NEW, "comments": list(comments),
             "closingIssuesReferences": [{"number": n} for n in closing]}),
        "gh pr view 9 --json comments": json.dumps({"comments": list(comments)}),
        "gh issue view 4 --json body": json.dumps({"body": issue_body}),
        "gh pr comment 9": "https://example.com/c/1\n",
    })


class ReviewTasksTest(unittest.TestCase):
    def test_latest_changes_review_tasks(self):
        found = review.review_tasks({"comments": [changes_with_tasks()]}, OWNER)
        self.assertEqual((found["round"], found["next"], found["total"]), (1, "1.1", 1))

    def test_after_approval_there_are_none(self):
        found = review.review_tasks({"comments": [changes_with_tasks(), rev("approve")]}, OWNER)
        self.assertEqual(found["tasks"], [])

    def test_tick_review_patches_that_comment(self):
        runner = pr_runner([changes_with_tasks()])
        self.assertTrue(review.tick_review(runner, 9, OWNER, "1.1"))
        patch = [c for c in runner.calls if c[:4] == ["gh", "api", "-X", "PATCH"]]
        self.assertEqual(patch[0][4], "repos/{owner}/{repo}/issues/comments/55")

    def test_tick_review_already_ticked(self):
        runner = pr_runner([changes_with_tasks(ticked=True)])
        self.assertFalse(review.tick_review(runner, 9, OWNER, "1.1"))
        self.assertFalse(any("PATCH" in line for line in runner.lines()))


class PostGateTest(unittest.TestCase):
    PLAN_OPEN = "## Plan\nWritten against: " + "c" * 40 + "\n\n- [ ] **1. A**\n"

    def test_approve_refused_with_open_review_task(self):
        runner = pr_runner([changes_with_tasks(), reply()])
        with self.assertRaises(review.ReviewError):
            review.post(cfg(), runner, 9, "approve", NEW, "Looks right.", [], OWNER)
        self.assertFalse(any("pr comment" in line for line in runner.lines()))

    def test_approve_refused_with_open_plan_task(self):
        runner = pr_runner(closing=[4], issue_body=self.PLAN_OPEN)
        with self.assertRaises(review.ReviewError):
            review.post(cfg(), runner, 9, "approve", NEW, "Looks right.", [], OWNER)

    def test_approve_when_all_ticked(self):
        runner = pr_runner([changes_with_tasks(ticked=True), reply()], closing=[4],
                           issue_body=self.PLAN_OPEN.replace("[ ]", "[x]"))
        self.assertEqual(review.post(cfg(), runner, 9, "approve", NEW, "Good.", [], OWNER),
                         "https://example.com/c/1")

    def test_changes_need_tasks_numbered_for_the_round(self):
        runner = pr_runner([changes_with_tasks(ticked=True), reply()])
        for body in ("No checklist.", TASKS):          # round 2 needs 2.n ids
            with self.subTest(body=body), self.assertRaises(review.ReviewError):
                review.post(cfg(), runner, 9, "changes", NEW, body, [], OWNER)
        self.assertEqual(review.post(cfg(), runner, 9, "changes", NEW,
                                     TASKS.replace("1.1", "2.1"), [], OWNER),
                         "https://example.com/c/1")
```

- [ ] **Step 2: Run** `python3 -m unittest tests.test_review -v`; new tests FAIL.

- [ ] **Step 3: Implement** in `review.py` (add `from . import checklist`, and `import os,
  tempfile`):

```python
_COMMENT_ID = re.compile(r"#issuecomment-(\d+)$")


def _latest_review(comments, owner):
    """The owner's latest review comment and its regex match, or (None, None)."""
    found = (None, None)
    for item in comments or []:
        if item.get("author", {}).get("login") != owner:
            continue
        match = _REVIEW.match((item.get("body") or "").lstrip())
        if match:
            found = (item, match)
    return found


def review_tasks(pr, owner):
    """The tasks of the latest review when it asks for changes; none after an approval."""
    item, match = _latest_review(pr.get("comments"), owner)
    if item is None or match.group(1) == "approve":
        return {"round": 0, "review": None, "tasks": [], **checklist.progress([])}
    tasks = checklist.tasks_under(item["body"], "tasks")
    rounds = state(pr.get("comments"), owner, 1 << 30)["rounds"]
    return {"round": rounds, "review": item.get("url"), "tasks": tasks,
            **checklist.progress(tasks)}


def tick_review(runner, number, owner, task_id):
    pr = json.loads(runner(["gh", "pr", "view", str(number), "--json", "comments"]))
    item, match = _latest_review(pr.get("comments"), owner)
    if item is None or match.group(1) == "approve":
        raise ReviewError(f"the latest review on #{number} asks for no changes")
    new, changed = checklist.tick(item["body"], "tasks", task_id)
    if not changed:
        return False
    found = _COMMENT_ID.search(item.get("url") or "")
    if not found:
        raise ReviewError("cannot tell the review comment's id from its URL")
    handle, path = tempfile.mkstemp(suffix=".md")
    try:
        with os.fdopen(handle, "w", newline="") as out:
            out.write(new)
        runner(["gh", "api", "-X", "PATCH",
                f"repos/{{owner}}/{{repo}}/issues/comments/{found.group(1)}",
                "-F", f"body=@{path}", "--silent"])
    finally:
        os.unlink(path)
    return True


def _open_work(runner, pr, owner):
    problems = [f"task {task['id']} of the last review is not done"
                for task in review_tasks(pr, owner)["tasks"] if not task["done"]]
    for ref in pr.get("closingIssuesReferences") or []:
        found = checklist.plan(checklist.issue_body(runner, ref["number"]))
        for task in (found or {}).get("tasks", []):
            if not task["done"]:
                problems.append(f"task {task['id']} of issue #{ref['number']}'s plan is not done")
    return problems
```

  Replace `post` with:

```python
def post(cfg, runner, number, verdict, commit, body, terms, owner):
    """Post a review of `commit`. Refuses if the branch moved, if an approval would leave
    tasks open, or if a request for changes has no task list for this round."""
    text = compose(verdict, commit, body)
    pr = json.loads(runner(["gh", "pr", "view", str(number), "--json",
                            "headRefOid,comments,closingIssuesReferences"]))
    head = pr["headRefOid"]
    if head != commit:
        raise ReviewError(f"the pull request's head is now {head[:7]}, not {commit[:7]}; "
                          "review the new commits and post again")
    if verdict == "approve":
        problems = _open_work(runner, pr, owner)
        if problems:
            raise ReviewError("cannot approve yet: " + "; ".join(problems))
    else:
        tasks = checklist.tasks_under(body, "tasks")
        round_ = state(pr.get("comments"), owner, 1 << 30)["rounds"] + 1
        if not tasks:
            raise ReviewError("a request for changes needs a '## Tasks' checklist")
        wrong = [task["id"] for task in tasks
                 if task["done"] or not task["id"].startswith(f"{round_}.")]
        if wrong:
            raise ReviewError(f"this is round {round_}: tasks must be open and numbered "
                              f"{round_}.1, {round_}.2 ... (not {', '.join(wrong)})")
    try:
        return comment.publish(cfg, runner, "pr", number, text, terms)
    except comment.CommentError as error:
        raise ReviewError(str(error)) from error
```

  Note: when the latest review was an approval that went stale, `state(...)["rounds"]` is 0, so
  the next request is round 1 again; that is intended (rounds restart after an approval).
  In `cli.py`'s `cmd_review` post branch pass `_owner()` as the last argument, and add
  `checklist.TaskError` to the errors it reports.

- [ ] **Step 4: Run** the review tests and the whole suite; all pass.
- [ ] **Step 5: Commit** "Reviews carry tasks; no approval while tasks are open".

### Task 5: task progress in `sprint-status`

**Files:**
- Modify: `lib/baton/sprint.py`
- Test: `tests/test_sprint.py`

**Interfaces:**
- Consumes: `checklist.plan`, `checklist.progress`, `review.review_tasks`.
- Produces: `result["progress"]`: `{"<N>": {"pr": P|None, "plan": {"done","total","next"}|None, "review": {"round","done","total","next"}|None}}`
  for every issue in `status:in-dev` or `status:in-review`.

- [ ] **Step 1: Write the failing test** in `tests/test_sprint.py`:

```python
    def test_progress_for_in_dev_and_in_review(self):
        plan = ("## Plan\nWritten against: " + "c" * 40 +
                "\n\n- [x] **1. A**\n- [ ] **2. B**\n")
        dev = issue(4, "in-dev")
        dev["body"] += plan
        pr = {"number": 40, "headRefName": "issue-4-a", "headRefOid": "d" * 40,
              "isDraft": True, "mergeable": "MERGEABLE", "statusCheckRollup": [],
              "closingIssuesReferences": [{"number": 4}],
              "comments": [{"author": {"login": OWNER}, "url": "u#issuecomment-1",
                            "body": review.compose("changes", "d" * 40,
                                                   "## Tasks\n- [ ] **1.1 Fix**\n")}]}
        snap = sprint.snapshot(cfg(), FakeRunner(replies([dev], prs=[pr])))
        self.assertEqual(snap["progress"]["4"],
                         {"pr": 40, "plan": {"done": 1, "total": 2, "next": "2"},
                          "review": {"round": 1, "done": 0, "total": 1, "next": "1.1"}})
```

  (Adapt the fixture call to the file's existing `replies(...)` helper and PR shape.)

- [ ] **Step 2: Run** `python3 -m unittest tests.test_sprint -v`; the new test FAILS.

- [ ] **Step 3: Implement.** Import `checklist`; add `"progress": {}` to the initial `result`;
  in the issue loop, before the status branches:

```python
        if status in ("status:in-dev", "status:in-review"):
            pr = pr_for.get(number)
            found = checklist.plan(item.get("body") or "")
            fixes = review.review_tasks(pr, owner) if pr else None
            result["progress"][str(number)] = {
                "pr": pr["number"] if pr else None,
                "plan": checklist.progress(found["tasks"]) if found else None,
                "review": ({key: fixes[key] for key in ("round", "done", "total", "next")}
                           if fixes and fixes["total"] else None),
            }
```

- [ ] **Step 4: Run** the sprint tests and the whole suite; all pass.
- [ ] **Step 5: Commit** "sprint-status reports plan and review task progress".

### Task 6: remove task agents' detached worktrees after a merge

**Files:**
- Modify: `lib/baton/merge.py` (`_after_merge`, new `_task_worktrees`)
- Test: `tests/test_merge.py`

**Interfaces:**
- Produces: `_task_worktrees(head, runner) -> [(path, locked)]`: worktrees whose HEAD is detached
  at a commit contained in `head` (`git merge-base --is-ancestor <sha> <head>` succeeds).

- [ ] **Step 1: Write the failing tests** in `MergeTest` (`tests/test_merge.py`), using its
  `replies` helper and the file's `HEAD` constant:

```python
    TASK_TREES = ("worktree /wt/agent-a\nHEAD 2222\ndetached\nlocked agent\n\n"
                  "worktree /wt/agent-b\nHEAD 3333\ndetached\n\n")

    def task_runner(self, dirty=False):
        replies = self.replies(make_pr())
        replies["git worktree list"] += "\n" + self.TASK_TREES
        if dirty:
            replies["git -C /wt/agent-a status --porcelain"] = "?? scratch.txt\n"
        fake = FakeRunner(replies)

        def runner(argv, cwd=None):
            if argv[:4] == ["git", "merge-base", "--is-ancestor", "3333"]:
                fake.calls.append(argv)
                raise run.RunError("not an ancestor")
            return fake(argv, cwd)
        return runner, fake

    def test_detached_task_worktrees_on_the_branch_are_removed(self):
        runner, fake = self.task_runner()
        self.assertEqual(merge.merge(9, make_config(), runner), [])
        lines = fake.lines()
        self.assertIn(f"git merge-base --is-ancestor 2222 {HEAD}", lines)
        self.assertIn("git worktree remove --force --force /wt/agent-a", lines)
        self.assertFalse(any("remove" in line and "/wt/agent-b" in line for line in lines))

    def test_dirty_task_worktree_is_kept_with_a_note(self):
        runner, fake = self.task_runner(dirty=True)
        notes = []
        self.assertEqual(merge.merge(9, make_config(), runner, notes), [])
        self.assertFalse(any("remove" in line and "/wt/agent-a" in line for line in fake.lines()))
        self.assertTrue(any("/wt/agent-a" in note for note in notes))
```

  (Import `run` from `baton` in the test file if it is not imported yet.)

- [ ] **Step 2: Run** `python3 -m unittest tests.test_merge -v`; the new tests FAIL.

- [ ] **Step 3: Implement** in `merge.py` (import `run` if not imported):

```python
def _task_worktrees(head, runner):
    """(path, locked) of detached worktrees whose commit is part of `head`'s history."""
    found, path, sha, locked, detached = [], None, None, False, False
    for line in runner(["git", "worktree", "list", "--porcelain"]).splitlines() + [""]:
        if line.startswith("worktree "):
            path, sha, locked, detached = line[len("worktree "):], None, False, False
        elif line.startswith("HEAD "):
            sha = line[len("HEAD "):]
        elif line == "detached":
            detached = True
        elif line.startswith("locked"):
            locked = True
        elif line == "" and path and detached and sha:
            try:
                runner(["git", "merge-base", "--is-ancestor", sha, head])
                found.append((path, locked))
            except run.RunError:
                pass
            path = None
    return found
```

  In `_after_merge`, after the branch's own worktree is handled and before `git branch -D`:

```python
    for path, locked in _task_worktrees(pr["headRefOid"], runner):
        if runner(["git", "-C", path, "status", "--porcelain"]).strip():
            notes.append(f"kept task worktree {path}: it has uncommitted changes")
            continue
        force = ["--force", "--force"] if locked else ["--force"]
        runner(["git", "worktree", "remove", *force, path])
```

  Leave `cli.cmd_merge_pr`'s tolerant list as it is: `git merge-base --is-ancestor` failing is
  the "no" answer and `_task_worktrees` catches it; `git worktree remove` and `git -C` are
  already tolerated cleanup commands.

- [ ] **Step 4: Run** the merge tests and the whole suite; all pass.
- [ ] **Step 5: Commit** "merge-pr removes the clean detached worktrees of a branch's tasks".

### Task 7: `baton usage`

**Files:**
- Create: `lib/baton/usage.py`
- Modify: `lib/baton/cli.py` (`usage` command)
- Test: `tests/test_usage.py`

**Interfaces:**
- Produces: `classify(prompt, agent_type=None) -> (role, key)`;
  `report(root, since=None) -> {"rows": [...], "windows": [...], "skipped": int}`;
  `table(report) -> str`. A row: `{"key", "role", "agents", "calls", "weighted", "peak",
  "large_share"}`. A window: `{"start", "end", "weighted", "by_role": {role: weighted}}`.
  CLI: `baton usage [--since <ISO time>] [--json] [--dir <projects dir>]`.

- [ ] **Step 1: Write the failing tests:**

```python
import json, pathlib, tempfile, unittest
from baton import usage


def call(req, ts, read=0, write=0, inp=10, out=100):
    return {"type": "assistant", "requestId": req, "timestamp": ts,
            "message": {"usage": {"input_tokens": inp, "cache_read_input_tokens": read,
                                  "cache_creation_input_tokens": write, "output_tokens": out}}}


def prompt(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def write(path, records, extra_lines=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r) for r in records] + list(extra_lines)
    path.write_text("\n".join(lines) + "\n")


class ClassifyTest(unittest.TestCase):
    def test_prompts(self):
        self.assertEqual(usage.classify("Do task 3 of issue 12."), ("task", "issue 12"))
        self.assertEqual(usage.classify("Do review task 1.2 of pull request 40 (issue 12)."),
                         ("fix", "issue 12"))
        self.assertEqual(usage.classify("Do review task 1.2 of pull request 40."),
                         ("fix", "pr 40"))
        self.assertEqual(usage.classify("Do spike issue 9."), ("spike", "issue 9"))
        self.assertEqual(usage.classify("Review pull request 40 (issue 12)."),
                         ("review", "issue 12"))
        self.assertEqual(usage.classify("Refine issues 5, 6, 7."), ("refine", "issues 5, 6, 7"))
        self.assertEqual(usage.classify("hello", "baton:baton-developer"),
                         ("developer", "unknown"))
        self.assertEqual(usage.classify("hello"), ("other", "other"))


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.dir.name)
        session = self.root / "-repo" / "s1.jsonl"
        write(session, [prompt("/baton:sprint"), call("r1", "2026-10-09T10:00:00Z", read=1000)])
        sub = self.root / "-repo" / "s1" / "subagents"
        write(sub / "agent-a.jsonl", [
            prompt("Do task 1 of issue 12."),
            call("r2", "2026-10-09T10:01:00Z", read=300_000, write=1000),
            call("r2", "2026-10-09T10:01:00Z", read=300_000, write=1000),   # same request
            call("r3", "2026-10-09T10:02:00Z", read=1000),
        ], extra_lines=["{not json"])
        write(self.root / "-other" / "s2.jsonl",
              [prompt("hi"), call("r4", "2026-10-09T16:00:00Z", read=10)])

    def tearDown(self):
        self.dir.cleanup()

    def rows(self, **kwargs):
        found = usage.report(self.root, **kwargs)
        return found, {(row["key"], row["role"]): row for row in found["rows"]}

    def test_rows(self):
        found, rows = self.rows()
        task = rows[("issue 12", "task")]
        self.assertEqual((task["agents"], task["calls"]), (1, 2))
        self.assertEqual(task["peak"], 301_010)
        big = 10 + 30_000 + 1250 + 500
        small = 10 + 100 + 500
        self.assertAlmostEqual(task["weighted"], big + small)
        self.assertAlmostEqual(task["large_share"], big / (big + small))
        self.assertIn(("session s1", "orchestrator"), rows)
        self.assertIn(("session s2", "other"), rows)
        self.assertEqual(found["skipped"], 1)

    def test_windows_and_since(self):
        found, _ = self.rows()
        self.assertEqual(len(found["windows"]), 2)
        found, rows = self.rows(since="2026-10-09T12:00:00+00:00")
        self.assertEqual(list(rows), [("session s2", "other")])

    def test_table_mentions_rows(self):
        text = usage.table(usage.report(self.root))
        self.assertIn("issue 12", text)
        self.assertIn("task", text)
```

- [ ] **Step 2: Run** `python3 -m unittest tests.test_usage -v`; FAIL (no module).

- [ ] **Step 3: Implement** `lib/baton/usage.py`:

```python
"""Quota use per issue and role, read from the local Claude Code transcripts.

Design spec, section 7b. Every call re-sends the agent's whole conversation, so
the cost of an agent is its calls times its conversation size. Weights follow the
price ratios: cache read 0.1, cache write 1.25 and output 5 times an input token.
"""
import datetime
import json
import pathlib
import re

WEIGHTS = {"input_tokens": 1.0, "cache_read_input_tokens": 0.1,
           "cache_creation_input_tokens": 1.25, "output_tokens": 5.0}
LARGE = 250_000
WINDOW = datetime.timedelta(hours=5)
_PROMPTS = [
    ("fix", re.compile(r"Do review task \d+\.\d+ of pull request #?(\d+)(?: \(issue #?(\d+)\))?")),
    ("task", re.compile(r"Do task \d+ of issue #?(\d+)")),
    ("spike", re.compile(r"Do spike issue #?(\d+)")),
    ("review", re.compile(r"Review pull request #?(\d+)(?: \(issue #?(\d+)\))?")),
    ("refine", re.compile(r"Refine issues? (#?\d+(?:(?:,\s*|\s+and\s+)#?\d+)*)")),
]
_TYPES = {"baton:baton-developer": "developer", "baton:baton-reviewer": "review",
          "baton:baton-refiner": "refine"}


def classify(prompt, agent_type=None):
    for role, pattern in _PROMPTS:
        match = pattern.search(prompt or "")
        if not match:
            continue
        if role in ("task", "spike"):
            return role, f"issue {match.group(1)}"
        if role in ("fix", "review"):
            return role, (f"issue {match.group(2)}" if match.group(2) else f"pr {match.group(1)}")
        numbers = re.findall(r"\d+", match.group(1))
        return role, "issues " + ", ".join(numbers)
    if agent_type in _TYPES:
        return _TYPES[agent_type], "unknown"
    return "other", "other"


def _time(text):
    when = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    return when if when.tzinfo else when.replace(tzinfo=datetime.timezone.utc)


def _prompt_text(record):
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def _read(path, seen):
    """(first prompt, calls) of one transcript; calls are de-duplicated by request id."""
    prompt, calls, skipped, by_request = None, [], 0, {}
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        if record.get("type") == "user" and prompt is None:
            prompt = _prompt_text(record)
        usage = (record.get("message") or {}).get("usage") if record.get("type") == "assistant" else None
        request = record.get("requestId")
        if not usage or not request or request in seen:
            continue
        try:
            by_request[request] = {"time": _time(record["timestamp"]),
                                   **{key: int(usage.get(key) or 0) for key in WEIGHTS}}
        except (KeyError, ValueError, TypeError):
            skipped += 1
    seen.update(by_request)
    for item in by_request.values():
        item["context"] = (item["input_tokens"] + item["cache_read_input_tokens"]
                           + item["cache_creation_input_tokens"])
        item["weighted"] = sum(item[key] * weight for key, weight in WEIGHTS.items())
        calls.append(item)
    return prompt, calls, skipped


def _agent_type(path):
    meta = path.with_name(path.name[:-len(".jsonl")] + ".meta.json")
    try:
        return json.loads(meta.read_text()).get("agentType")
    except (OSError, ValueError):
        return None


def report(root, since=None):
    root = pathlib.Path(root).expanduser()
    since = _time(since) if since else None
    seen, skipped, agents, sessions = set(), 0, [], []
    for path in sorted(root.rglob("*.jsonl")):
        prompt, calls, bad = _read(path, seen)
        skipped += bad
        calls = [call for call in calls if since is None or call["time"] >= since]
        if path.parent.name == "subagents":
            agents.append((path, classify(prompt, _agent_type(path)), calls))
        else:
            sessions.append((path, calls))
    baton_dirs = {path.parent.parent for path, (role, _), _ in agents if role != "other"}
    entries = [(role_key, calls) for _, role_key, calls in agents]
    for path, calls in sessions:
        role = "orchestrator" if path.with_suffix("") in baton_dirs else "other"
        entries.append(((role, f"session {path.stem[:8]}"), calls))

    rows, every = {}, []
    for (role, key), calls in entries:
        if not calls:
            continue
        row = rows.setdefault((key, role), {"key": key, "role": role, "agents": 0, "calls": 0,
                                            "weighted": 0.0, "peak": 0, "large": 0.0})
        row["agents"] += 1
        for call in calls:
            row["calls"] += 1
            row["weighted"] += call["weighted"]
            row["peak"] = max(row["peak"], call["context"])
            if call["context"] > LARGE:
                row["large"] += call["weighted"]
            every.append((call["time"], role, call["weighted"]))
    for row in rows.values():
        row["large_share"] = row.pop("large") / row["weighted"] if row["weighted"] else 0.0

    windows = []
    for when, role, weighted in sorted(every):
        if not windows or when >= windows[-1]["_start"] + WINDOW:
            windows.append({"_start": when, "weighted": 0.0, "by_role": {}})
        window = windows[-1]
        window["weighted"] += weighted
        window["by_role"][role] = window["by_role"].get(role, 0.0) + weighted
    for window in windows:
        start = window.pop("_start")
        window["start"] = start.isoformat()
        window["end"] = (start + WINDOW).isoformat()
    ordered = sorted(rows.values(), key=lambda row: -row["weighted"])
    return {"rows": ordered, "windows": windows, "skipped": skipped}


def table(found):
    lines = ["| Work | Role | Agents | Calls | Weighted (M) | Peak (K) | Above 250K |",
             "|---|---|---|---|---|---|---|"]
    for row in found["rows"]:
        lines.append(f"| {row['key']} | {row['role']} | {row['agents']} | {row['calls']} | "
                     f"{row['weighted'] / 1e6:.1f} | {row['peak'] / 1e3:.0f} | "
                     f"{row['large_share']:.0%} |")
    lines += ["", "| Window (UTC) | Weighted (M) | By role |", "|---|---|---|"]
    for window in found["windows"]:
        shares = ", ".join(f"{role} {value / window['weighted']:.0%}"
                           for role, value in sorted(window["by_role"].items(),
                                                     key=lambda item: -item[1]))
        lines.append(f"| {window['start'][:16]} | {window['weighted'] / 1e6:.1f} | {shares} |")
    if found["skipped"]:
        lines += ["", f"{found['skipped']} unreadable records skipped."]
    return "\n".join(lines)
```

  In `cli.py`:

```python
def cmd_usage(args):
    since, as_json, root = None, False, "~/.claude/projects"
    rest = list(args)
    while rest:
        flag = rest.pop(0)
        if flag == "--json":
            as_json = True
        elif flag in ("--since", "--dir") and rest:
            value = rest.pop(0)
            since, root = (value, root) if flag == "--since" else (since, value)
        else:
            print("usage: baton usage [--since <ISO time>] [--json] [--dir <projects dir>]",
                  file=sys.stderr)
            return 64
    found = usage.report(root, since)
    print(json.dumps(found, indent=2) if as_json else usage.table(found))
    return 0
```

  Register `"usage": cmd_usage`; import `usage`. It needs no Baton config.

- [ ] **Step 4: Run** the usage tests and the whole suite; all pass. Smoke on this machine:
  `bin/baton usage --since 2026-10-08T22:00:00Z | head -20` prints a table (do not paste its
  output anywhere public; it names local sessions only by id).
- [ ] **Step 5: Commit** "Add baton usage: quota use per issue and role".

### Task 8: agents

**Files:**
- Create: `agents/baton-refiner.md`
- Modify: `agents/baton-developer.md`, `agents/baton-reviewer.md` (description and body)
- Test: `tests/test_template.py`

- [ ] **Step 1: Update the tests:** add `"agents/baton-refiner.md"` to
  `test_plugin_files_exist`, and `"baton-refiner"` to `test_agents_run_at_medium_effort`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_template -v`; FAIL.
- [ ] **Step 3: Write the agents.**

`agents/baton-refiner.md`:

```markdown
---
name: baton-refiner
description: Use to refine forge issues in bulk for Baton. Give it the repository and at most about three issue numbers ("Refine issues <N>, <M>."). It brings each issue to the definition of ready, including a plan of small tasks, asks the maintainer on the issue where only they can decide, and returns a short report per issue.
model: sonnet
effort: medium
skills:
  - baton:refine
---

You refine the issues you were given, one after another, following the preloaded `refine` skill
step by step. Read code with filters (`rg -n`, `sed -n`, line ranges), not whole files, so your
conversation stays small across several issues.

You do not implement anything, open pull requests or start other agents.

Your final message is read by whoever dispatched you, not by a person: one short report per issue
as the skill's "Report" step defines, and nothing else.
```

`agents/baton-developer.md`: new description:
"Use to do one task during a Baton sprint: one task of an issue's plan ("Do task <n> of issue
<N>."), one task of a code review ("Do review task <r.n> of pull request <P> (issue <N>)."), or
a whole spike ("Do spike issue <N>."). It works alone in its own git worktree, never merges,
and returns DONE, FIXED, DECLINED, REPLAN, QUESTION or BLOCKED."
Body: "You have been assigned exactly one task. Do that task and nothing more, following the
preloaded `develop` skill ... Your final message ... one of the return forms the skill defines."
Keep `model`, `effort`, `isolation`, `skills`.

`agents/baton-reviewer.md`: description gains "Dispatch it only when every task of the issue's
plan (and of the previous review) is ticked. ... posts one review comment with a verdict and,
for changes, a task checklist ...". Prompt form `Review pull request <P> (issue <N>).`

- [ ] **Step 4: Run** the template tests and the whole suite; `claude plugin validate .` passes
  (the author warning is expected).
- [ ] **Step 5: Commit** "Add the refiner agent; developers do one task each".

### Task 9: develop and review skills

**Files:**
- Modify: `skills/develop/SKILL.md` (rewrite around one task)
- Modify: `skills/review/SKILL.md` (task checklist, when it runs, habits)
- Create: `references/habits.md` (the shared working habits)

No code tests; the check is a careful read against spec sections 7, 7a and 7b, and
`python3 -m unittest discover -s tests -t .` still passing.

- [ ] **Step 1: `references/habits.md`** with exactly the four habits of spec section 7b
  ("Read what you need", "Logs go to a file", "One call for related commands", "Wait once"),
  each with a one-line example, for instance:
  `rg -n "fn detect" src/ | head`, `sed -n '120,188p' reference/defade.py`,
  `cargo test > "$WORK/test.log" 2>&1; tail -5 "$WORK/test.log"; rg -n "FAILED|panicked" "$WORK/test.log" | head -20`,
  `cmd > log 2>&1 & pid=$!; wait $pid` or `until [ -f "$WORK/done" ]; do sleep 30; done` with a
  long timeout. State why: every call re-sends the whole conversation.

- [ ] **Step 2: Rewrite `skills/develop/SKILL.md`.** Keep: "What to trust", "When you need a
  decision", "Long work" (now spikes only), "Posting a comment", "Never" (add: tick boxes, edit
  an issue description or a review comment, or work on more than your one task). Replace the
  rest with:
  - Description front matter: "Use when assigned one task of a Baton sprint: one task of an
    issue's plan, one task of a code review, or a whole spike, as a Baton developer subagent or
    directly with /baton:develop. Covers reading only what the task needs, the stale-plan check,
    working on the issue's branch, the task's check, REPLAN, questions and the return report.
    Never merges."
  - "Read first: `${CLAUDE_PLUGIN_ROOT}/references/habits.md`."
  - **Steps for a plan task** (spec section 7 steps 1 to 7), with commands:
    1. `gh issue view <N> --json body,labels` and `baton tasks issue <N>`; read only Context,
       Acceptance criteria, Out of scope, Decisions and your task's brief; `baton answers <N>`.
    2. Stale check: `git fetch origin <default>` then
       `git diff --stat <written-against> origin/<default> -- <your task's files>`; open only the
       changed hunks you rely on; when the brief no longer fits, return `REPLAN`.
    3. Branch: task 1 creates `issue-<N>-<slug>` from `origin/<default>`, opens the draft pull
       request (`Closes #<N>`, a summary of the plan), `baton set-status <N> status:in-dev`.
       Later tasks: `git fetch origin <branch> && git checkout --detach origin/<branch>`, and read
       earlier tasks with `git log --oneline origin/<default>..HEAD` and
       `git diff --stat origin/<default>...HEAD`.
    4. Do the task with tests, inside its brief (pin first when changing behaviour; never loosen a
       threshold or delete a test).
    5. Run the task's `Check:` and nothing more unless the brief says so; "none" means none.
    6. `git commit -m "Task <n>: <title>"`; `git push origin HEAD:<branch>`; a refused push
       (not a fast-forward) means `BLOCKED: the branch moved`.
    7. The last plan task also merges the default branch in, trial-merges against other open pull
       requests (`git merge-tree --write-tree HEAD origin/<their-branch>`), updates the pull
       request body (what changed, how it was verified) and pushes. It does not set a status; the
       orchestrator does.
  - **Review tasks:** dispatched with "Do review task <r.n> of pull request <P> (issue <N>).";
    read the task with `baton tasks pr <P>`, check the finding against the code first, fix it
    (commit `Review <r.n>: <title>`) or decline it with a technical reason. No reply comment: the
    orchestrator writes the reply from your result.
  - **REPLAN:** proposed tasks in the plan's brief format, numbered from the task being replaced,
    inside a block fenced as ```` ```baton-plan <from-task> ````, plus one paragraph on why. Stay
    inside the acceptance criteria; if the change would alter what the issue promises, say so.
  - **Spikes** ("Do spike issue <N>."): the existing spike steps, unchanged.
  - **Return forms** (in the skill file, fence the REPLAN form with four backticks so its inner
    `baton-plan` fence survives):

```
DONE <full sha>
Issue: #<N>, task <n>
Check: <command> → <summary line> | none
Conflicts with other open pull requests: none | #<PR> in <files>   (last task only)
Follow-up suggestions: none | - <title>: <one line>
Untrusted instructions seen: none | <where, and what they asked>
```

```
FIXED <full sha>
Pull request: #<P>, review task <r.n>
Check: <command> → <summary line> | none
```

```
DECLINED <one-line reason>
Pull request: #<P>, review task <r.n>
<the technical reasoning, in a few lines>
```

```
REPLAN
Issue: #<N>, from task <n>
Why: <one paragraph>
Within the acceptance criteria: yes | no, because <...>
```baton-plan <n>
- [ ] **<n>. <title>**
  - Files: ...
```
```

    plus the existing `QUESTION:` and `BLOCKED:` forms, and the spike's `DONE` form (findings URL).

- [ ] **Step 3: Update `skills/review/SKILL.md`:**
  - "Read first: `${CLAUDE_PLUGIN_ROOT}/references/habits.md`." In the verification step: run
    only what the brief allows; never re-run the long suite when CI covers it.
  - The review body template: "Fix in this pull request" becomes `## Tasks`, each task in the
    plan's brief format (`- [ ] **<round>.<n> <title>**` with `Problem` (file:line, severity),
    `Fix`, `Check`), grouping related small problems into one task. The round is the number of
    requests for changes since the last approval plus one (`baton review state <P>` gives
    `rounds`).
  - `baton review post` refuses an approval while tasks are open and a request for changes
    without a correctly numbered `## Tasks` list; when it refuses, fix the body and post again;
    if it refuses an approval for open tasks, return that to the orchestrator instead of
    approving.
  - Later rounds: review the commits named `Review <r.n>:` since the last review, and judge each
    declined task's reason.

- [ ] **Step 4: Run** the whole suite (unchanged, must pass).
- [ ] **Step 5: Commit** "Develop does one task; reviews are task lists; shared working habits".

### Task 10: refine, sprint and sprint-plan skills, references, README, version

**Files:**
- Modify: `skills/refine/SKILL.md`, `skills/sprint/SKILL.md`, `skills/sprint-plan/SKILL.md`
- Modify: `references/github.md`, `README.md`, `.claude-plugin/plugin.json`
- Modify: `docs/superpowers/plans/2026-10-09-baton-task-plans.md` (an "As built" note)

- [ ] **Step 1: `skills/refine/SKILL.md`:**
  - Definition of ready table: a `## Plan` row ("Plan with `Written against:` and numbered task
    boxes; not for spikes" / "each task is one unit of work with its own check; the briefs name
    exact files, signatures and line ranges; tasks run in order").
  - "Read first: `${CLAUDE_PLUGIN_ROOT}/references/habits.md`."
  - New step after "Rewrite the description": **Write the plan.** Record
    `git rev-parse origin/<default>` as `Written against:`; one task per unit of work with its own
    check (a named test, a filtered subset, a build, or none); a final task for the
    `## Verification` command when it is long; `Uses`/`Provides` with exact signatures and
    `file:line`; `Port from` with line ranges; no code. Adapted from superpowers' `writing-plans`
    (credit it). The plan is part of the complete description written to a file.
  - Bulk refinement: dispatch `baton:baton-refiner` with "Refine issues <N>, <M>, <K>." and at
    most about three issues per agent; give each agent only its issue numbers.
  - Report: one line per issue, including the number of plan tasks.

- [ ] **Step 2: `skills/sprint/SKILL.md`:**
  - Pass step "Review and merge": the reviewer is dispatched with
    `Review pull request <P> (issue <N>).` only when `progress.<N>.plan` is complete and, from
    round 2, `progress.<N>.review` is complete; on `changes`: `baton set-status <N>
    status:in-dev` and work the review tasks; when every review task is ticked, post the reply
    with `baton comment pr <P> developer reply -`, one line per task (`<r.n> fixed in <sha>` /
    `<r.n> declined: <reason>`), then set `status:in-review`.
  - Dispatch: an issue in development gets one developer at a time; the prompt is exactly
    `Do task <n> of issue <N>.` for `progress.<N>.plan.next`, `Do review task <r.n> of pull
    request <P> (issue <N>).` for `progress.<N>.review.next`, or `Do spike issue <N>.`; new
    issues fill free slots as before.
  - Results: `DONE <sha>` / `FIXED <sha>`: `git ls-remote origin <branch>` must show that sha;
    then `baton task done issue <N> <n>` (or `pr <P> <r.n>`); after the last plan task,
    `baton set-status <N> status:in-review`. `DECLINED`: `baton task done pr <P> <r.n>`, keep the
    reason for the reply. `REPLAN`: within the acceptance criteria → write the fenced plan to a
    file, `baton plan replace <N> <from> <file>`, post an orchestrator `decision` comment (what
    changed and why), continue; otherwise `needs-maintainer` with the proposal in a `question`
    comment. Never chain these after another command; act on each result.
  - Resuming: for each in-dev issue, compare `git log --oneline origin/<default>..origin/<branch>`
    `Task <n>:` / `Review <r.n>:` commits with the ticked boxes; tick a task whose commit exists
    once its `Check:` passes on that commit (run it in a scratch worktree); otherwise dispatch the
    task again.
  - Retrospective: include `baton usage --since <sprint start>` as a "Quota use" section.
  - Never: tick a box, edit a plan or review by hand except through `baton task done` and
    `baton plan replace`.

- [ ] **Step 3: `skills/sprint-plan/SKILL.md`:** proposals list each issue's number of plan
  tasks (from `baton tasks issue <N>`), and only issues whose `ready-check` passes (which now
  includes the plan).

- [ ] **Step 4: `references/github.md`** rows: "An issue's plan tasks: `baton tasks issue <N>`",
  "A review's tasks: `baton tasks pr <P>`", "Tick a task: `baton task done <issue|pr> <N> <task>`
  (orchestrator only)", "Replace open plan tasks: `baton plan replace <N> <from> <file>`
  (orchestrator only)", "Quota use: `baton usage [--since <time>] [--json]`".

- [ ] **Step 5: README:** a "Plans of small tasks" section (why: quota; how: refine plans, one
  developer per task, reviews as tasks, one review per pull request), the new commands, the
  refiner agent, credit to superpowers' `writing-plans`; `plugin.json` version `0.3.0`, and its
  description mentions task plans.

- [ ] **Step 6: Verify:** the whole suite passes; `claude plugin validate .` passes;
  `bin/baton tasks`, `bin/baton task`, `bin/baton plan` and `bin/baton usage --bogus` print usage
  and exit 64; `bin/baton usage --since 2026-10-09T00:00:00Z` runs. Grep the skills for any
  leftover "answer the review on pull request" and "one issue to one pull request" wording.

- [ ] **Step 7: Commit** "Refine writes plans; the sprint dispatches one developer per task;
  0.3.0", push, then update the Baton memory file.
