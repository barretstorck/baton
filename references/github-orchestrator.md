# GitHub reference for Baton skills: orchestrator

Read by the sprint orchestrator (the sprint, sprint-plan, open-questions and init skills). Commands only the orchestrator uses, and maintainer-only commands that no skill runs. The commands every role uses are in `${CLAUDE_PLUGIN_ROOT}/references/github.md`, and its rules for writing to the forge apply here too.

## Reading

| Step | Command |
|---|---|
| Check the identity setup | `baton doctor` (tier, and each App's login, token and repository access) |
| Every open issue waiting on the maintainer | `baton open-questions` (JSON list sorted most pressing first: `number`, `title`, `kind`, `question_url`, `tier`, `repair`, `blocks`, `age_days`; `[]` when nothing) |
| Spikes first, capped | `baton sprint-order <N>...` (JSON: `order` with spikes first, `spikes`, `left_out` for spikes over `spike.max_per_sprint`, `cap`; exit 64 on a number that is not an open issue) |
| Reconnect to the orchestrator (parent session) | `baton orchestrator-agent [--dir <projects dir>]`: prints the agent ID of this session's `baton:baton-orchestrator` subagent, exit 1 for none |
| Compaction reminder | `baton compact-reminder`: a SessionStart hook, not run by hand; after any compaction it reminds the sprint orchestrator to run `baton sprint-status` before acting (the hook cannot tell which agent compacted, so the text is conditional) |
| Sprint metrics | `baton sprint-metrics <milestone> [--since <UTC ISO time>] [--dir <projects dir>]`: seven `key: value` lines for the retro's `## Metrics` section |
| Sprint capacity | `baton sprint-capacity <days> [--retros <n>]`: reads the last retros' `## Metrics` and proposes somewhat less (a 0.8 buffer) than their average issues and plan tasks for `<days>`, as JSON |

## Writing

| Step | Command |
|---|---|
| Set an issue's priority | `baton set-priority <N> <1-4>` (1 urgent, 2 high, 3 medium, 4 low; replaces any other `priority:` label) |
| Set a bug's severity | `baton set-severity <N> <1-4>` (replaces any other `severity:` label; refused unless the issue is a bug). Severity is ranking information only: no due dates, it never pulls a bug into a sprint or interrupts one. |
| Create the Baton labels | `baton labels --create` creates every missing label, including `priority:1`-`4` and `severity:1`-`4` |
| Mark an issue as missing its sprint | `baton issue missed <N> "<milestone title or number>"` (adds a `Missed sprint:` line under Pointers, the `missed-sprint` label, and removes the milestone) |
| Edit a pull request | `gh pr edit <N>` takes the same options as `gh issue edit` (`--title`, `--body-file`, `--add-label`, `--remove-label`, `--milestone`) |
| Mark a pull request ready | `gh pr ready <N>`, or `gh pr ready <N> --undo` to turn it back into a draft (the orchestrator, once the review is approved) |
| Deprecated plan commands | `baton plan commit`, `baton plan link` and `baton plan pin` do nothing; do not use them |
| Create or edit a milestone | `baton milestone create --title <t> [--description <d>] [--due-on <ISO date>]`; `baton milestone edit <number or title> [--title <t>] [--description <d>] [--due-on <date>] [--state open\|closed]` |
| Merge | `baton merge-pr <N>` only. Do not run `gh pr merge`. |
