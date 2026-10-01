# Baton plan 3: sprint planning and execution

**Spec:** `docs/superpowers/specs/2026-10-01-baton-design.md` (sections 5, 6, 8).
**Executed 2026-10-01**, inline. This records what was built; the code and skill files are authoritative.

## Built

| File | Purpose |
|---|---|
| `lib/baton/sprint.py` | `snapshot`: one read of the forge giving the sprint's state and the issues that may be dispatched now; `branch_state`; `overlaps`; the run lock |
| `baton sprint-status [milestone]` | Prints the snapshot as JSON |
| `skills/sprint-plan/SKILL.md` | Goal, length and limit; proposal; milestone creation after approval |
| `skills/sprint/SKILL.md` | The pass: default branch, budgets, answers, finished work, verification, follow-ups, dispatch, closing |
| `templates/retro.md` | Retrospective layout |

## Decisions made while building

- Dispatch selection is code, not prose: ready, written by the maintainer, blockers closed, no
  overlap in `Expected files:` with work in flight, within free slots. With a red default branch
  only the repair issue is selectable.
- Order within a sprint: repair issue, then `deliverable` before `stretch`, then the milestone
  description's `Order:` line, then issue number.
- The milestone description carries `Goal:`, `Parallel:` and `Order:` lines.
- A sprint milestone is one whose title starts with `Sprint`; other milestones are ignored.
- "Default branch state" is judged from the workflow runs on its newest commit.
- The orchestrator's acceptance check stays brief (criteria addressed, closing reference,
  verification line); the mechanical checks are in `baton merge-pr`.
