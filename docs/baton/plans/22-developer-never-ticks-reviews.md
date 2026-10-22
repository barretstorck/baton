## Pattern

Developer rules live in `skills/develop/SKILL.md` (tick line at 93, review tasks from "## A review
task", `## Never` list near the end) and are summarised in `agents/baton-developer.md`. The
orchestrator side is `skills/sprint/SKILL.md` section 8 (lines 248-249) and the table row in
`references/github.md:38`. Text-presence tests live in `tests/test_template.py`. `baton task done` is
`cmd_task` in `lib/baton/cli.py:173`; `review.tick_review` is `lib/baton/review.py:195`.

## Verification

`PYTHONPATH=lib python3 -m unittest discover -s tests -v` (what CI runs).

## Design notes

- Role detection (acceptance criterion 2) is practical only as a warning. Baton cannot know the
  caller's role: a developer would have to set an environment variable itself, which is the very
  step it forgot, and the orchestrator may itself run in a linked worktree. So `baton task done pr`
  warns on stderr when run from a linked git worktree unless `BATON_ROLE=orchestrator` is set,
  and still ticks. It does not refuse, to avoid blocking a legitimate orchestrator.
- Detecting a linked worktree: `git rev-parse --path-format=absolute --git-dir` differs from
  `--git-common-dir`.

## Plan

Written against: dc7d89f8e49ade16665665d7312fe65a2c9ac435

- [x] **1. Developer instructions: who ticks what**
  - Files: `skills/develop/SKILL.md`, `agents/baton-developer.md`, `tests/test_template.py`
  - Provides: in `skills/develop/SKILL.md`, directly under the plan-task tick line (step 4, line 93) a short two-row contrast: plan task, the developer ticks its own box in the plan file with `baton task done issue <N> <n>` and commits it with the code; review task, the developer never runs `baton task done pr`, it returns `FIXED <sha>` or `DECLINED <reason>` and the orchestrator checks the sha and ticks. Repeat the review-task half in "## A review task" step 3, and add a `## Never` bullet "Run `baton task done pr`". Add one sentence with the same rule to `agents/baton-developer.md`. In `tests/test_template.py` add a test that both files contain `never run \`baton task done pr\`` (case-insensitive) .
  - Check: `PYTHONPATH=lib python3 -m unittest tests.test_template -v`

- [x] **2. Warn when `baton task done pr` runs in a linked worktree**
  - Files: `lib/baton/review.py`, `lib/baton/cli.py`, `tests/test_review.py`
  - Uses: `cmd_task` (`lib/baton/cli.py:173`), the runner convention of `tests/fakes.py:1` (`FakeRunner`)
  - Provides: `def in_linked_worktree(runner, cwd) -> bool` in `review.py`, true when `git rev-parse --path-format=absolute --git-dir` and `... --git-common-dir` (run with `cwd`) differ. In `cmd_task`'s `pr` branch, when `os.environ.get("BATON_ROLE") != "orchestrator"` and `in_linked_worktree(run.run, os.getcwd())`, print to stderr `baton: warning: task done pr is the orchestrator's; developers report FIXED or DECLINED instead. Set BATON_ROLE=orchestrator to silence this.` then tick as before (exit code unchanged).
  - Check: `PYTHONPATH=lib python3 -m unittest tests.test_review -v`, with new tests for `in_linked_worktree` (equal paths false, different true) through `FakeRunner` replies.

- [x] **3. Orchestrator side: name the contrast and the variable**
  - Files: `skills/sprint/SKILL.md`, `references/github.md`, `tests/test_template.py`
  - Uses: the warning from task 2
  - Provides: in `skills/sprint/SKILL.md` section 8 (lines 248-249) a sentence that only the orchestrator runs `baton task done pr`, from the main checkout, or with `BATON_ROLE=orchestrator` set when it runs from a linked worktree; the same note on the `baton task done pr` entry of the table at `references/github.md:38`. Add a test that `skills/sprint/SKILL.md` contains `BATON_ROLE`.
  - Check: `PYTHONPATH=lib python3 -m unittest tests.test_template -v`

- [ ] **4. Full suite**
  - Files: none
  - Check: `PYTHONPATH=lib python3 -m unittest discover -s tests -v`
