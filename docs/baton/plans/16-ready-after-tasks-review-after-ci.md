## Pattern

`lib/baton/sprint.py` `_pr_ci` delegates to `merge._check_ci` (`lib/baton/merge.py:46`), which already keeps only the newest run per check name (`_latest_runs`, `merge.py:32`) and rejects an all-skipped head. Tests build pull requests with a `statusCheckRollup` list as in `tests/test_sprint.py:125-136` (`test_pull_requests_are_attached_to_their_issue`) and `tests/test_merge.py:make_pr`.

## Verification

`PYTHONPATH=lib python3 -m unittest discover -s tests`

## Design notes

- `_pr_ci` is the only place that decides `ci`; the skill must not guess. Empty rollup becomes `pending` unless `merge.allow_no_ci` is true (then `none`), so a pull request just marked ready is not reviewed before its checks register.
- An all-skipped head (a draft run) becomes `pending`, not `red`: a skipped run alone means "no CI yet". `merge.py` is not changed, so `merge-pr` still refuses it. Re-runs of the same check already win by newest `startedAt` (`test_skipped_draft_run_then_success_passes`, `tests/test_merge.py:78`), so the skipped-then-success case in the issue's comment needs no change there.
- The review-fix re-draft is split out to #26.
- Overlap: #18 and #19 also edit `skills/sprint/SKILL.md` (other sections: step 6; Start step 4, step 3 and step 7). This issue edits step 4 "Review and merge" and step 8 only.

## Plan

Written against: dc7d89f8e49ade16665665d7312fe65a2c9ac435

- [ ] **1. Report pending for a ready pull request with no checks yet**
  - Files: `lib/baton/sprint.py`, `tests/test_sprint.py`
  - Uses: `merge._check_ci(pr, cfg) -> list[str]` (`lib/baton/merge.py:46`); `cfg.data["merge"]["allow_no_ci"]`
  - Provides: `_pr_ci(pr, cfg)` returns `pending` for an empty `statusCheckRollup` when `allow_no_ci` is false and `none` when it is true; returns `pending` when the only problem is the all-skipped one; unchanged otherwise
  - Check: `PYTHONPATH=lib python3 -m unittest tests.test_sprint`; add tests: empty rollup with allow_no_ci false -> `pending`; with true -> `none`; only a skipped run -> `pending`; skipped plus later success -> `green`; failed run -> `red`
- [ ] **2. Move the ready step and gate the reviewer on CI in the sprint skill**
  - Files: `skills/sprint/SKILL.md`
  - Uses: `sprint-status` fields `in_review[].draft`, `.ci`, `.review`, `progress.<N>.plan.next`
  - Provides: in step 4 "Review and merge", a rule before the `review` rules (lines 105-111): not a spike, `progress.<N>.plan.next` null and `draft` true: `gh pr ready <PR>`, look again next pass; the reviewer rule (`none`, `replied`, `stale`) additionally requires `ci` `green`, or `ci` `none` (which only appears when `allow_no_ci` is true); `pending` waits; `red` or `CONFLICTING` follows the existing "Fix CI" dispatch before any review; the `approved` / `not-required` list drops its first sub-step "Still a draft" down to a fallback for a pull request that is still a draft (lines 117-118); step 8 last-task sentence stays `status:in-review` with the next pass doing the ready step
  - Check: `rg -n "gh pr ready" skills/sprint/SKILL.md` shows the new rule before the reviewer rule and the fallback; read the step through once for contradictions
- [ ] **3. Describe the order in the README and the brief template**
  - Files: `README.md`, `templates/brief.md`
  - Uses: `README.md:47-57` ("How a pull request gets merged"); `templates/brief.md:32` (the `allow_no_ci` comment)
  - Provides: README steps say the orchestrator marks the pull request ready once the last plan task is ticked, CI runs once, the reviewer starts only when CI is green (or right away with `allow_no_ci: true`), and a red CI is fixed before review; the `allow_no_ci` comment says it also lets the reviewer start with no CI. Front matter values unchanged
  - Check: `PYTHONPATH=lib python3 -m unittest tests.test_template`
- [ ] **4. Full verification**
  - Files: none
  - Check: `PYTHONPATH=lib python3 -m unittest discover -s tests`
