# GitHub reference for Baton skills: triager

Read by the scheduled-run session and maintainers. The `baton-triager` agent cannot open this file (it has only `Bash` and `Write`), so `skills/triage/SKILL.md` writes out the `baton triage apply` row below; change both together. Commands only the triager uses. The commands every role uses are in `${CLAUDE_PLUGIN_ROOT}/references/github.md`, and its rules for writing to the forge apply here too.

## Reading

| Step | Command |
|---|---|
| Search for duplicates | `gh search issues "<key words>" --repo <owner>/<repo>` and `gh issue list --state all --search "<key words>"`. `gh search issues` takes no `--state all`; without `--state` it searches open and closed issues. |

## Writing

| Step | Command |
|---|---|
| Triage queue | `baton triage queue`. Prints JSON for a scheduled triage run: `first_run`, `since`, `batch` (up to `triage.max_per_run` oldest pending issue numbers), `waiting`, `overdue` (oldest waiting issue once it has waited 24 hours, else null) and `stale` (`awaiting-feedback` issues quiet for `triage.stale_days`) and `reproduce` (triaged bugs to hand to the refiner: open, bug label, `status:refinement`, a `## Triage summary`, written by someone outside (author not a maintainer or Baton login, or an `Origin: outside` line), no `<!-- baton:repro-handoff -->` comment yet, not `awaiting-feedback`, oldest first, at most `triage.max_reproductions_per_day` minus the hand-offs of the last 24 hours; 0 turns hand-off off). The first run records the start time in the Baton home folder and triages nothing. Writes nothing else. |
| Apply a triage decision | `baton triage apply <N> <decision.json>`. Checks a triager's decision, then acts on it. Keys: `category` (spam, duplicate, support, unclear, feature, bug, maintenance, spike, unsure), `duplicate_of`, `priority`, `severity` (bugs only), `comment`, `summary` (symptom, expected, actual, area, version, type). Refuses a label outside the known set, a missing, same or unknown `duplicate_of`, a severity on a non-bug, `spike` for an issue without `type:spike`, or feature, bug or maintenance for one with it, a comment that fails the privacy scan or links outside the repository and its docs, and a summary with code blocks, commands, links or over-long fields. Spam: `closed:spam` and lock. Duplicate: `closed:duplicate` with back-links. Support: comment, then `closed:support`. Unclear: comment, then `awaiting-feedback`. Feature, bug, maintenance, spike: type label, `priority:`, `severity:` (bugs), `status:refinement`, and a `## Triage summary` section (with `Origin: outside (@author)` for a non-maintainer). Unsure: notifies the maintainers (`triage-unsure`). It never closes a maintainer's issue. |
