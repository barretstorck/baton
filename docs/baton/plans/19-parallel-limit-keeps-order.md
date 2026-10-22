## Pattern

`Blocked by:` already holds an issue in `sprint-status` until its blocker is closed (`lib/baton/sprint.py:178`, `lib/baton/issue.py:28`), and a merged pull request closes its issue, so "run after #N merges" is exactly `Blocked by: #N`. Prose edits to `skills/sprint/SKILL.md`; no code.

## Verification

`PYTHONPATH=lib python3 -m unittest discover -s tests`, plus `rg -n "parallel limit" skills/sprint/SKILL.md` showing the new rules.

## Design notes

- Decision on the optional `After: #N`: not added. `Blocked by:` already gives the same hold, `held` already reports it, and a second keyword would need parser, `planfile.py:158` Pointers handling, refine and template changes for no new behaviour. Instead the skill says an order the maintainer decides is recorded both in Decisions and as a `Blocked by:` line.
- A raised parallel limit only adds free slots; it never clears a hold or a recorded order. If the only way to fill a slot is to start an issue against a recorded decision, ask the maintainer (an orchestrator `question` comment on that issue plus `needs-maintainer`).
- Overlap: #16 (step 4, step 8) and #18 (step 6) also edit `skills/sprint/SKILL.md`; this issue edits Start step 4 (`:34-37`), step 3 (`:74-92`) and step 7 (`:181-230`).

## Plan

Written against: dc7d89f8e49ade16665665d7312fe65a2c9ac435

- [x] **1. State that a parallel-limit change does not override a recorded order**
  - Files: `skills/sprint/SKILL.md`
  - Uses: Start step 4 ("Take the parallel limit from the request...", `:34-37`); step 7 "Dispatch", the paragraph beginning "Look at `held` too" (`:219`)
  - Provides: in Start step 4, a sentence that changing the limit only changes `free_slots`, never an order or decision recorded on an issue; in step 7 beside the `held` paragraph, a rule that when a free slot could only be filled by an issue whose Decisions or Pointers say it comes later, the orchestrator leaves the slot empty and asks the maintainer on that issue (`baton comment issue <N> orchestrator question <file>`, label `needs-maintainer`)
  - Check: `rg -n "recorded" skills/sprint/SKILL.md` finds both
- [x] **2. Record maintainer-decided order as Blocked by**
  - Files: `skills/sprint/SKILL.md`
  - Uses: step 3 "Answers", item 2 (`:81-85`)
  - Provides: in step 3 item 2, when a decision sets the order of two issues, also add `Blocked by: #N` to the later issue's Pointers in the same description edit, because `sprint-status` reads that line and not Decisions; and a sentence that this is used instead of prose alone
  - Check: `rg -n "Blocked by" skills/sprint/SKILL.md` finds it in step 3; `PYTHONPATH=lib python3 -m unittest discover -s tests`
