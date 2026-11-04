# GitHub reference for Baton skills: reviewer

Read by the reviewer. Commands only the reviewer uses. The commands every role uses are in `${CLAUDE_PLUGIN_ROOT}/references/github.md`, and its rules for writing to the forge apply here too.

## Reading

| Step | Command |
|---|---|
| A pull request's checks | `gh pr checks <N>` |
| A pull request's draft state and CI state (reviewer) | `baton pr-state <N>` (JSON: `draft`, `ci`: `green`, `red`, `cancelled` (only cancelled checks failed: CI did not finish), `pending` or `none`, `verify`: `none`, `missing`, `green`, `red` or `timeout`, `problems`) |
