## Pattern

`lib/baton/merge.py` `_check_ci(pr, cfg)` turns `statusCheckRollup` into problems; `merge.check`
calls it and `merge.merge` owns the `notes` list the CLI prints. `lib/baton/sprint.py` `_pr_ci`
calls `merge._check_ci` too. Tests: `tests/test_merge.py` (`make_pr`, `make_config`, `FakeRunner`
from `tests/fakes.py`, whose replies match by command-line prefix) and `tests/test_sprint.py`
(`replies`, `snap`).

## Verification

`PYTHONPATH=lib python3 -m unittest discover -s tests` (the command CI runs).

## Design notes

- A rollup check run carries `detailsUrl` ending in `/actions/runs/<run id>/job/<job id>`. Take the
  run id from it and ask `gh run view <id> --json status,conclusion,workflowName,url`, only for a
  check run whose `status` is not `COMPLETED` (so no extra calls on the normal path). Items
  without such a URL (third-party apps, status contexts) are never changed.
- Run `conclusion` is lower case (`success`); upper-case it to compare with `_PASSING`.
- If the run is not `completed`, or `gh run view` fails, leave the item unfinished, as today.
- This issue's note text must name the check and the run (its url), so a lagging check can be told
  from a real one.

## Plan

Written against: dc7d89f8e49ade16665665d7312fe65a2c9ac435

- [x] **1. Settle a lagging check run from its workflow run**
  - Files: `lib/baton/merge.py`, `tests/test_merge.py`
  - Uses: `_check_ci(pr, cfg)` (`lib/baton/merge.py:46`), `_latest_runs(rollup)` (`lib/baton/merge.py:32`), `RunError` (`lib/baton/run.py`)
  - Provides: `_settle(item, runner, notes) -> dict` (returns the item unchanged, or a copy with `status` COMPLETED and `conclusion` set from the finished run, appending one note such as `check 'build' was still in progress; used the result of run <url> (success)`); `_check_ci(pr, cfg, runner=None, notes=None)` applies `_settle` after `_latest_runs`; `check(pr, cfg, owner, default_branch, runner, notes=None)` and `merge(...)` pass `runner` and their notes list through
  - Check: `PYTHONPATH=lib python3 -m unittest tests.test_merge`
  - Tests to add, with `FakeRunner` replying to `gh run view 123`: lagging check on a successful run passes and a note names the check and run; on a failed run reports `concluded FAILURE`; on a run still `in_progress` reports `has not finished`; with `runner=None` or an unreachable run the old behaviour holds

- [ ] **2. Use it in sprint-status**
  - Files: `lib/baton/sprint.py`, `tests/test_sprint.py`
  - Uses: `_pr_ci(pr, cfg)` (`lib/baton/sprint.py:42`), `merge._check_ci(pr, cfg, runner, notes)` (task 1), the snapshot call site `"ci": _pr_ci(pr, cfg)` (`lib/baton/sprint.py:152`)
  - Provides: `_pr_ci(pr, cfg, runner)`; call site passes `runner`
  - Check: `PYTHONPATH=lib python3 -m unittest tests.test_sprint`
  - Test to add: an in-review issue whose PR has a lagging check on a successful run reports `ci: green` (extra reply `gh run view` via the `extra` argument of `replies`)
