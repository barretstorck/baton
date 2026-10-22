## Pattern

`sprint._pr_ci` (`lib/baton/sprint.py:42`) already turns a pull request's `statusCheckRollup` into
green, red, pending or none through `merge._check_ci` (`lib/baton/merge.py:46`), which keeps only the
newest run of each check (`merge._latest_runs`, `merge.py:25`) so a replaced `skipped` draft run is
ignored. The reviewer gets the same answer through one new command, `baton pr-state <PR>`, instead
of reading checks itself. Follow the shape of `cmd_sprint_status` (`lib/baton/cli.py:274`) for the
command and of `tests/test_sprint.py:128` for the pull request fixtures.

## Verification

`PYTHONPATH=lib python3 -m unittest discover -s tests -v` (what CI runs).

## Design notes

- The command calls `sprint._pr_ci` rather than copying its rules, so the fix for #16 (empty rollup
  reports `pending` when `merge.allow_no_ci` is false; a head whose only checks are `skipped`
  reports `pending`, not `red`) reaches the reviewer with no further change.
- Blocked by #16 and #17, and written for the code after both are merged: #16 makes an all-skipped
  head `pending`, and #17 adds a `runner` argument to `_pr_ci` and `merge._check_ci` so a check run
  GitHub has not closed yet is settled from its workflow run. `pr_state` passes the runner through,
  so `baton pr-state` and `sprint-status` always give the same `ci` for the same pull request. If
  either signature differs from what is named here when this issue starts, return `REPLAN`.
- The reviewer is not made to wait or refuse on a non-green head; the sprint orchestrator decides
  when to dispatch it (#16). The reviewer only reports the state correctly.

## Plan

Written against: dc7d89f8e49ade16665665d7312fe65a2c9ac435

- [x] **1. A pr_state helper in sprint.py**
  - Files: `lib/baton/sprint.py`, `tests/test_sprint.py`
  - Uses: `_pr_ci(pr, cfg, runner)` (`lib/baton/sprint.py`, after #16 and #17), `merge._check_ci(pr, cfg, runner=None, notes=None)` (`lib/baton/merge.py`, after #17)
  - Provides: `def pr_state(pr, cfg, runner=None) -> dict` returning `{"draft": bool, "ci": "green|red|pending|none", "problems": [str]}`; `draft` is `bool(pr["isDraft"])`, `ci` is `_pr_ci(pr, cfg, runner)`, `problems` is `merge._check_ci(pr, cfg, runner)`
  - Check: `PYTHONPATH=lib python3 -m unittest tests.test_sprint -v`, with new tests: (a) a ready pull request whose rollup has a `skipped` run and a later `SUCCESS` run of the same name (give both `startedAt`) gives `draft` false and `ci` green; (b) a draft pull request whose only check is `skipped` gives `draft` true and `ci` pending (the all-skipped rule from #16) with a problem mentioning "skipped"; (d) with a `FakeRunner` replying to `gh run view 123`, a check run with `detailsUrl` ending `/actions/runs/123/job/1`, still `IN_PROGRESS` whose workflow run has finished with `success` gives `ci` green, the same as `sprint-status` (#17); (c) a draft whose rollup has a passing check still gives `draft` true.

- [ ] **2. The `baton pr-state` command**
  - Files: `lib/baton/cli.py`, `tests/test_sprint.py`
  - Uses: `sprint.pr_state(pr, cfg, runner)` (task 1), `_require_config()` (`lib/baton/cli.py:52`), `run.run`
  - Provides: `baton pr-state <PR>` prints `json.dumps(sprint.pr_state(pr, cfg, run.run), indent=2)` for `gh pr view <PR> --json isDraft,statusCheckRollup`; no number prints `usage: baton pr-state <PR>` to stderr and returns 64; add `"pr-state": cmd_pr_state` to `COMMANDS` (`cli.py:299`)
  - Check: `bin/baton pr-state 13` (a merged pull request in this repository) prints JSON with a `draft` key and a `ci` key; `bin/baton pr-state` exits 64.

- [ ] **3. Tell the reviewer to use it**
  - Files: `skills/review/SKILL.md`, `references/github.md`, `tests/test_template.py`
  - Uses: the command from task 2
  - Provides: in `skills/review/SKILL.md` step 4, a paragraph saying: run `baton pr-state <PR>`; `draft` is the pull request's own draft state and `ci` is judged on the newest run of each check, so a `skipped` run replaced by a later run does not count; state a pull request is a draft, or that CI did not run, only from that output, never from the first check seen in `gh pr checks` or the rollup; record the output's `draft` and `ci` under "Checked" in the review; add a matching bullet to the skill's `## Never` list. In `references/github.md` add a table row for `baton pr-state <N>` (reviewer: draft state and CI state of a pull request). In `tests/test_template.py` add a test that `skills/review/SKILL.md` contains `baton pr-state`.
  - Check: `PYTHONPATH=lib python3 -m unittest tests.test_template -v`

- [ ] **4. Full suite**
  - Files: none
  - Check: `PYTHONPATH=lib python3 -m unittest discover -s tests -v`
