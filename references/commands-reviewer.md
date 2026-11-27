# Command reference for Baton skills: reviewer

Read by the reviewer. Commands only the reviewer uses. The commands every role uses are in `${CLAUDE_PLUGIN_ROOT}/references/commands.md`, and its rules for writing to the forge apply here too.

## Reading

| Step | Command |
|---|---|
| A pull request's checks | `baton pr checks <N>` (JSON rows `name`, `state`, `url`; the run id is in the url) |
| A failed run's log | `baton run log <id>`, with the run id from the check's url (exit 2 "not supported" on Forgejo and GitLab: open the check's url) |
| A pull request's draft state and CI state (reviewer) | `baton pr-state <N>` (JSON: `draft`, `ci`: `green`, `red`, `cancelled` (only cancelled checks failed: CI did not finish), `pending` or `none`, `verify`: `none`, `missing`, `green`, `red` or `timeout`, `problems`) |
