## Pattern

`lib/baton/sprint.py` `snapshot(cfg, runner, milestone)` runs `gh` through `runner` and reads
each reply with `json.loads(runner([...]) or "[]")`; `branch_state(runs)` judges the runs.
`lib/baton/cli.py` `main` maps exceptions to exit codes. Tests: `tests/test_sprint.py` (`replies`,
`snap`, `run`) with `FakeRunner` from `tests/fakes.py`, which returns "" for any command no reply
key prefixes, so every new `gh` call needs a default reply in `replies()`.

## Verification

`PYTHONPATH=lib python3 -m unittest discover -s tests` (the command CI runs).

## Design notes

- Cause of problem 2: the `or "[]"` fallbacks turn an empty reply (what `gh` prints on some
  failures that exit 0, or a truncated page) into an empty list. Replace them with a strict read.
  A real empty result is the text `[]`, never an empty string. A failing `gh` call already raises
  `RunError` (exit 2 through `cli.main`); do not catch it.
- Problem 1: today's state comes from `gh run list --branch <default>`, which is not tied to the
  head commit. Read the head commit (sha and committer date) with
  `gh api repos/<owner>/<repo>/commits/<branch>`, then list push runs for it with
  `gh run list --branch <branch> --commit <sha> --event push --limit 20 --json status,conclusion,headSha,workflowName`.
- Runs appear on the forge a short time after a merge. So when no run is listed, a push workflow
  exists at that commit and the commit is younger than 300 seconds (`PUSH_RUN_GRACE_SECONDS`),
  report `pending`; otherwise `none`. This is the maintainer-visible choice recorded here: the
  acceptance criterion "no run was triggered" cannot be told from "not registered yet" without it.
- A push workflow is a file in `.github/workflows` at that commit (`.yml`/`.yaml`) whose `on:`
  section mentions `push` (forms: `on: push`, `on: [push, ...]`, `on:` block with a `push:` key).
  A missing `.github/workflows` directory (the API answers 404) means none.

## Plan

Written against: dc7d89f8e49ade16665665d7312fe65a2c9ac435

- [x] **1. Strict reads: never turn a bad reply into empty lists**
  - Files: `lib/baton/sprint.py`, `lib/baton/cli.py`, `tests/test_sprint.py`
  - Uses: the `or "[]"` reads for milestones, issues, prs and runs in `snapshot` (`lib/baton/sprint.py:69-130`); `main` (`lib/baton/cli.py:322`)
  - Provides: `class SnapshotError(Exception)` and `_load(runner, argv, what) -> list` in `sprint.py` (raises `SnapshotError("could not read <what>: ...")` on an empty reply, invalid JSON or a non-list); `cli.main` prints `baton: <message>` and returns 2 for `SnapshotError`
  - Check: `PYTHONPATH=lib python3 -m unittest tests.test_sprint`
  - Tests to add: an empty reply for `gh issue list` raises `SnapshotError`; so does one for `gh pr list` and for the milestones call; a `[]` reply still gives a valid empty snapshot; a `RunError` from the runner propagates

- [ ] **2. Pending while a push run is in progress**
  - Files: `lib/baton/sprint.py`, `tests/test_sprint.py`
  - Uses: `branch_state(runs)` (`lib/baton/sprint.py:22`, keep it and its tests), `_load` (task 1), `snapshot` line `"default_branch": {...}` (`lib/baton/sprint.py:79`)
  - Provides: `default_branch_state(runner, owner, repo, branch, now=None) -> str`, `push_workflow_exists(runner, owner, repo, sha) -> bool`, `triggers_on_push(workflow_text) -> bool`, `PUSH_RUN_GRACE_SECONDS = 300`; `snapshot` uses `default_branch_state`
  - Check: `PYTHONPATH=lib python3 -m unittest tests.test_sprint`
  - Tests to add (give `replies()` default replies for `gh api repos/octo/proj/commits/main` and the workflows listing, so the existing tests keep passing): push run in progress on the head gives `pending`; no push workflow gives `none`; push workflow present, no run, commit older than the grace gives `none`; younger gives `pending`; a failed run gives `red`; `triggers_on_push` for the three forms and for a pull_request-only file

- [ ] **3. Update the sprint skill**
  - Files: `skills/sprint/SKILL.md`
  - Uses: the verification rules at `skills/sprint/SKILL.md:163-170`
  - Provides: the `none` bullet says it now means no push workflow or no run triggered for that commit; one sentence that `sprint-status` exits non-zero when forge data could not be read, and the orchestrator must not act on a pass that failed
  - Check: none
