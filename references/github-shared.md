# GitHub reference for Baton skills: shared

Read by every role except the developer. Commands used by two or more roles other than the developer. The commands every role uses are in `${CLAUDE_PLUGIN_ROOT}/references/github.md`, and its rules for writing to the forge apply here too.

## Reading

| Step | Command |
|---|---|
| Does it meet the definition of ready | `baton ready-check <N>` |
| Full verification of a pull request (reviewer) | `baton verify <PR> <worktree>`: runs the brief's `verify.command` in the worktree (at the pull request's head, clean); prints `verify <state> commit=<sha7> exit=<n> duration=<s>s`, `log: <path>` and the last 40 lines; writes nothing to the pull request. Exit 0 green, 1 red or refused, 3 timeout |
| Run a command in the isolated sandbox | `baton sandbox run <command...>`: runs it through the local brief's `## Sandbox` `Command:` template in a fresh checkout of the default branch; prints `sandbox exit=<n>`, `log: <path>` and the last 40 lines. Error (exit 1) when no sandbox is configured; exit status is the command's, 3 on timeout |
| A pull request's review state | `baton review state <N>`: `none`, `changes`, `replied`, `stale`, `approved`, `escalate`, `replan`, `blocked` or `repeated` (the same failing tests in two reviews in a row) |
| Clean up leftovers (orchestrator) | `baton clean`: removes agent worktrees under `.claude/worktrees/` that are not on an open issue's branch, are not locked by a live process and have no unmerged commits (uncommitted files are removed with the worktree), and the log folders of closed or merged pull requests; prints `removed`/`kept` lines, exit 1 if one could not be removed; `baton merge-pr` hints at it when more than `clean.hint_worktrees` (default 10) agent worktrees exist |
| Quota use | `baton usage --repo <owner/repo> [--since <UTC ISO time>] [--json]`: per issue and role, from local transcripts; transcripts record streaming-time output tokens, so `Output>=` and `output_lower_bound` are lower bounds |
| Spike token limit | `baton budget`, checked through `baton hook` (the combined PreToolUse hook), not run by hand; keeps a per-agent offset file in the temp directory; blocks a researcher's tools once its `Timebox:` token limit is spent, except `baton comment`; it rests on input and cache tokens, as output is undercounted |
| Repository owner and default branch | `gh repo view --json owner,defaultBranchRef` |

## Writing

| Step | Command |
|---|---|
| Add or remove another label | `baton issue edit <N> --add-label <label>` / `--remove-label <label>`; use the names from `labels.map` in `baton config` |
| Notify the maintainers | `baton notify <N> <cause> -` (text on standard input, or a file path). Causes: `stuck`, `approval`, `triage-unsure`, `spike-infeasible`, `repro-skipped`, `rule`. Adds `needs-maintainer` and posts one `status` comment that mentions every maintainer, once per issue per cause (only an earlier notify `status` comment by a maintainer or a Baton login counts; prints `already notified` otherwise). `baton notify --rules` does this with cause `rule` for each open issue matching the brief's `notify:` rules. `/baton:open-questions` is unchanged. |
| Close or reopen | `baton close\|reopen <issue\|pr> <N> <role> <reason-file \| ->`: posts a `baton:<role>:closed`/`reopened` marker comment with the reason, then closes or reopens. `close` on an issue also takes `--reason spam\|duplicate\|support\|wont-do\|completed`, `--duplicate-of <N>` (required with `duplicate`; links the original and comments on it) and `--lock`: `spam`, `support` and `wont-do` add the label `closed:<reason>` and close as not planned, `duplicate` adds `closed:duplicate`, `completed` adds no label; `--lock` locks the issue after closing. None of these apply to a pull request or to `reopen`. The label `awaiting-feedback` is only a label you may set; no command acts on it. Do not run `gh ... close\|reopen`. An item closed with no such marker was closed by the maintainer: do not reopen it without asking. |
| Edit an issue description | Write the full new description to a file, then `baton issue edit <N> --body-file <file>` (it scans the text and refuses an empty body). It keeps an existing `## Developer Plan` when the new text has none; plans change only through `baton plan` |
| Create an issue | Write the description to a file, then `baton issue create --title "<title>" --label <label> <file>` (repeat `--label`; `-` reads the body from standard input) |
| Post a review | `baton review post <N> <approve\|changes\|blocked\|replan> <full head sha> -` with the body as an inline heredoc. It refuses if the head moved. `blocked` needs a `Cause: default-branch` or `Cause: environment` line and a `## Reason` section, takes no `## Tasks`, and does not count as a review round. `replan` needs a non-empty `## Feedback` section, takes no `## Tasks`, and counts as a round. Failures are listed under `## Failures` as `- <id>: <caused\|default-branch\|flaky\|environment>`. |
| Answer a review | `baton comment pr <N> developer reply -`, one line per task (orchestrator, from the developers' results) |
| Migrate a plan | `baton plan migrate <N>` (refiner or orchestrator): converts an older plan (on a branch or in a legacy `## Plan` section) into `## Developer Plan` and removes the `Plan:` line |
| Replace a plan's open tasks | `baton plan replace <N> <from-task> <file> [--drop]` (orchestrator only; refuses fewer new tasks than replaced unless `--drop`; edits the body) |
| Insert tasks into a plan | `baton plan insert <N> <before-task> <file>` (orchestrator only; the new tasks go before that number, later open tasks are renumbered with their `task <n>` references; done tasks are refused; `total + 1` appends) |
