# Baton plan 2: developer and refinement

**Spec:** `docs/superpowers/specs/2026-10-01-baton-design.md` (sections 4, 7, 8, 9.2).
**Executed 2026-10-01**, inline. This records what was built; the code and skill files are authoritative.

## Built

| File | Purpose |
|---|---|
| `lib/baton/issue.py` | Parses `Blocked by:` and `Expected files:` lines and headed sections; `ready_problems` is the mechanical half of the definition of ready |
| `lib/baton/comment.py` | `post` scans, stamps and posts an agent comment; `pending_answers` finds the maintainer's replies to the latest agent question |
| `baton comment`, `baton answers`, `baton ready-check` | Command-line entry points |
| `skills/develop/SKILL.md` | One issue to one pull request; resume, questions, spikes, return contract |
| `skills/refine/SKILL.md` | Definition of ready; splitting; questions; failing-default-branch diagnosis |
| `agents/baton-developer.md` | Subagent: Sonnet, isolated worktree, `develop` preloaded |
| `references/github.md` | Maps each neutral step to a `gh` or `baton` command |
| `templates/issue.md` | Issue description template |

## Decisions made while building

- Agents post comments through `baton comment`, which scans and stamps in one place. The publish
  gate still covers direct `gh` use.
- Labels `type:feature`, `type:bug` and `type:maintenance` were added to the default set, because
  the definition of ready requires a type.
- The orchestrator's `decision` comment (an answer the maintainer gave in the session) counts as
  an answer in `pending_answers`.
- The developer subagent has no restricted tool list. The spec asked for one; restricting tools
  would also disable the code-review skill it must run. Its limits come from the skill and the gate.
- The skills have not been pressure-tested with agents yet. That happens in the pilot sprint.
