## Pattern

`skills/sprint/SKILL.md` step 4 ("Review and merge") already marks a draft pull request ready
once `progress.<N>.plan.next` is null (the "Not a spike, ... `draft` is true" bullet) and
dispatches the reviewer only when the pull request is ready and `ci` is `green` or `none`. Step 8
ends the reply with `baton set-status <N> status:in-review`, and the next pass runs that bullet.
So after a re-draft, "ready again, CI, then reviewer" needs no new code: only the re-draft, a
guard on the ready bullet, and the docs. Wording tests that read the skill live in
`tests/test_template.py` (`test_sprint_skill_names_orchestrator_role_variable`, line 48).
`sprint-status` already reports `draft` per `in_review` entry (`lib/baton/sprint.py:262`), and
its output does not change, so there is no code or sprint-status test change.

## Verification

`PYTHONPATH=lib python3 -m unittest discover -s tests` (the command CI runs).

## Design notes

- Order matters in step 4: the ready bullet comes before the `changes` bullet. A re-drafted
  pull request whose review is still `changes` or `escalate` must not be marked ready by it, so
  that bullet gets the condition "`review` is not `changes` or `escalate`".
- `gh pr ready <PR> --undo` is run only when `draft` is false, so it is not repeated on a later
  pass or after a resume.
- The `escalate` case re-drafts when the maintainer's answer sends the tasks back to a
  developer (step 3 records the decision, step 7 works the tasks).

## Plan

Written against: ca35e49f8d8ccc60810e48b641d55936677276d0

- [x] **1. Re-draft on changes and guard the ready bullet**
  - Files: `skills/sprint/SKILL.md`
  - Uses: step 4 bullets "Not a spike, `progress.<N>.plan.next` is null and `draft` is true" (about line 119), "`changes`" (about line 131) and "`escalate`" (about line 133)
  - Provides: the `changes` bullet runs `gh pr ready <PR> --undo` (only when `draft` is false) together with `baton set-status <N> status:in-dev`, and says why (one CI run per "developer done", not one per pushed fix task); the `escalate` bullet says the same re-draft happens when the maintainer's answer sends the review tasks to a developer; the ready bullet gains the condition that `review` is not `changes` or `escalate`
  - Check: none (prose; task 3 tests the wording)

- [x] **2. Say ready-again happens after the reply**
  - Files: `skills/sprint/SKILL.md`
  - Uses: step 8 reply bullet ("When the last review task is ticked, post one reply", about line 297) and the "every task ticked" fallback in step 7 (about line 237)
  - Provides: step 8 and the step 7 fallback say that after the reply the pull request, still a draft, is marked ready by the next pass (step 4), CI runs once, and the reviewer is dispatched only when `ci` is green or `none`; no separate `gh pr ready` call in step 8
  - Check: none (prose; task 3 tests the wording)

- [ ] **3. Document the flow and test the wording**
  - Files: `README.md`, `tests/test_template.py`
  - Uses: README section "How a pull request gets merged", items 1 to 3 (`README.md:48-58`); `test_sprint_skill_names_orchestrator_role_variable` (`tests/test_template.py:48`) as the model
  - Provides: a README sentence after item 3 saying a `changes` review turns the pull request back into a draft while its fix tasks are worked, and it is marked ready again after the developer's reply; `test_sprint_skill_redrafts_on_changes` asserting `gh pr ready <PR> --undo` appears in `skills/sprint/SKILL.md`
  - Check: `PYTHONPATH=lib python3 -m unittest discover -s tests`
