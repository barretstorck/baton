# GitHub reference for Baton skills: refiner

Read by the refiner. Commands only the refiner uses. The commands every role uses are in `${CLAUDE_PLUGIN_ROOT}/references/github.md`, and its rules for writing to the forge apply here too.

## Reading

| Step | Command |
|---|---|
| Find open issues naming a file | `gh issue list --state open --search "\"<path>\" in:body" --json number,title` |
| Check a sandbox is configured | `baton sandbox check`: prints `configured` or `none`; exit 0 for `configured`, 1 for `none` |

## Writing

| Step | Command |
|---|---|
| Set a plan | `baton plan set <N> <file\|->` (refiner): writes the whole `## Developer Plan` section (heading included) into the issue body, replacing an earlier one |
| Set a plan's `Written against:` | `baton plan against <N> <full sha>` (refiner; edits the body) |
