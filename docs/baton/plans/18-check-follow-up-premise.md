## Pattern

Prose edits to the sprint skill and the issue template; no code. Follow the existing wording style of `skills/sprint/SKILL.md` step 6 (lines 171-179) and `templates/issue.md` (the Context placeholder is "What is wrong or missing, and why it matters.").

## Verification

`PYTHONPATH=lib python3 -m unittest discover -s tests` (the tests read the templates), plus `rg -n "unverified" skills/sprint/SKILL.md templates/issue.md` showing both edits.

## Design notes

- Checking happens before filing, with the orchestrator's own tools: `rg` or `git show origin/<default>:<path>` for a claim about code, `gh issue view <N> --comments` for a claim about a findings comment. "Cheaply" means a few commands, not an investigation.
- The unverified marker goes in the new issue's Context, so the refiner sees it and checks it.
- Overlap: #16 (step 4, step 8) and #19 (Start step 4, step 3, step 7) also edit `skills/sprint/SKILL.md`; this issue edits only step 6.

## Plan

Written against: dc7d89f8e49ade16665665d7312fe65a2c9ac435

- [x] **1. Add the premise check to the follow-up step**
  - Files: `skills/sprint/SKILL.md`
  - Uses: the step 6 paragraph "Developers return follow-up suggestions..." (`skills/sprint/SKILL.md:171-175`)
  - Provides: a paragraph in step 6, before the `joins this sprint` bullets, saying: before filing, check the follow-up's central claim ("X currently does Y", "Z was not measured") against the code on the default branch or the findings comment it came from; if it cannot be checked in a few commands, write in the issue's Context that the claim is unverified and name its source ("per the developer of #N"); do not repeat an agent's claim as fact
  - Check: `rg -n "unverified" skills/sprint/SKILL.md` finds the new text inside step 6
- [x] **2. Add the same guidance to the issue template**
  - Files: `templates/issue.md`
  - Uses: the `## Context` placeholder line
  - Provides: Context guidance extended with one sentence: a claim about current behaviour or past measurements is checked against the code or a linked comment, or marked unverified with its source
  - Check: `PYTHONPATH=lib python3 -m unittest discover -s tests`
