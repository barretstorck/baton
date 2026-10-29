# GitHub reference for Baton skills

How each step in the Baton skills is done on GitHub. Run everything from inside the repository.
`baton` is on `PATH` when the plugin is enabled; otherwise use `${CLAUDE_PLUGIN_ROOT}/bin/baton`.

## Reading

| Step | Command |
|---|---|
| Resolved settings | `baton config` |
| An issue with its comments | `gh issue view <N> --json number,title,author,body,labels,state,milestone,comments` |
| Does it meet the definition of ready | `baton ready-check <N>` |
| The latest agent question and the maintainer's replies | `baton answers <N>` |
| Every open issue waiting on the maintainer | `baton open-questions` (JSON list sorted most pressing first: `number`, `title`, `kind`, `question_url`, `tier`, `repair`, `blocks`, `age_days`; `[]` when nothing) |
| Open pull requests | `gh pr list --state open --json number,title,headRefName,isDraft,closingIssuesReferences` |
| A pull request's checks | `gh pr checks <N>` |
| A pull request's draft state and CI state (reviewer) | `baton pr-state <N>` (JSON: `draft`, `ci`: `green`, `red`, `cancelled` (only cancelled checks failed: CI did not finish), `pending` or `none`, `verify`: `none`, `missing`, `green`, `red` or `timeout`, `problems`) |
| Full verification of a pull request (reviewer) | `baton verify <PR> <worktree>`: runs the brief's `verify.command` in the worktree (at the pull request's head, clean); prints `verify <state> commit=<sha7> exit=<n> duration=<s>s`, `log: <path>` and the last 40 lines; writes nothing to the pull request. Exit 0 green, 1 red or refused, 3 timeout |
| Run a command in the isolated sandbox | `baton sandbox run <command...>`: runs it through the local brief's `## Sandbox` `Command:` template in a fresh checkout of the default branch; prints `sandbox exit=<n>`, `log: <path>` and the last 40 lines. Error (exit 1) when no sandbox is configured; exit status is the command's, 3 on timeout |
| Check the sandbox is isolated | `baton sandbox check`: prints `none`, `isolated`, `not-isolated` or `probe-failed`; exit 0 for `none` and `isolated`, else 1 |
| A pull request's review state | `baton review state <N>`: `none`, `changes`, `replied`, `stale`, `approved`, `escalate`, `replan`, `blocked` or `repeated` (the same failing tests in two reviews in a row) |
| An issue's plan tasks | `baton tasks issue <N>` (JSON: `written_against`, `tasks`, `done`, `total`, `next`); reads the `## Developer Plan` section of the issue body |
| An issue's plan | the `## Developer Plan` section of the issue body: `gh issue view <N> --json body -q .body`. The body is limited to 65536 characters; a longer one is refused and the issue must be split. Older issues: a plan still on `refs/plans/<N>` or on a branch is moved into the body by `baton plan migrate <N>`. |
| A review's tasks | `baton tasks pr <N>` (the latest review's `## Tasks`; none after an approval) |
| Clean up leftovers (orchestrator) | `baton clean`: removes agent worktrees under `.claude/worktrees/` that are not on an open issue's branch, are not locked by a live process and have no unmerged commits (uncommitted files are removed with the worktree), and the log folders of closed or merged pull requests; prints `removed`/`kept` lines, exit 1 if one could not be removed |
| Quota use | `baton usage --repo <owner/repo> [--since <UTC ISO time>] [--json]`: per issue and role, from local transcripts |
| Repository owner and default branch | `gh repo view --json owner,defaultBranchRef` |

## Writing

| Step | Command |
|---|---|
| Set an issue's status | `baton set-status <N> status:<name>` (removes the previous status) |
| Set an issue's priority | `baton set-priority <N> <1-4>` (1 urgent, 2 high, 3 medium, 4 low; replaces any other `priority:` label) |
| Set a bug's severity | `baton set-severity <N> <1-4>` (replaces any other `severity:` label; refused unless the issue is a bug). Severity is ranking information only: no due dates, it never pulls a bug into a sprint or interrupts one. |
| Create the Baton labels | `baton labels --create` creates every missing label, including `priority:1`-`4` and `severity:1`-`4` |
| Add or remove another label | `baton issue edit <N> --add-label <label>` / `--remove-label <label>`; use the names from `labels.map` in `baton config` |
| Notify the maintainers | `baton notify <N> <cause> -` (text on standard input, or a file path). Causes: `stuck`, `approval`, `triage-unsure`, `spike-infeasible`, `repro-skipped`, `rule`. Adds `needs-maintainer` and posts one `status` comment that mentions every maintainer, once per issue per cause (prints `already notified` otherwise). `baton notify --rules` does this with cause `rule` for each open issue matching the brief's `notify:` rules. `/baton:open-questions` is unchanged. |
| Post a comment | `baton comment <issue\|pr> <N> <role> <kind> -` with the body as an inline heredoc on standard input, or a file path in place of `-`. It scans the text, adds the Baton marker and prints the comment URL. |
| Close or reopen | `baton close\|reopen <issue\|pr> <N> <role> <reason-file \| ->`: posts a `baton:<role>:closed`/`reopened` marker comment with the reason, then closes or reopens. `close` on an issue also takes `--reason spam\|duplicate\|support\|wont-do\|completed`, `--duplicate-of <N>` (required with `duplicate`; links the original and comments on it) and `--lock`: `spam`, `support` and `wont-do` add the label `closed:<reason>` and close as not planned, `duplicate` adds `closed:duplicate`, `completed` adds no label; `--lock` locks the issue after closing. None of these apply to a pull request or to `reopen`. The label `awaiting-feedback` is only a label you may set; no command acts on it. `gh ... close\|reopen` is refused. An item closed with no such marker was closed by the maintainer: do not reopen it without asking. |
| Mark an issue as missing its sprint | `baton issue missed <N> "<milestone title or number>"` (adds a `Missed sprint:` line under Pointers, the `missed-sprint` label, and removes the milestone) |
| Edit an issue description | Write the full new description to a file, then `baton issue edit <N> --body-file <file>` (it scans the text and refuses an empty body) |
| Edit a pull request | `baton pr edit <N>` takes the same options as `baton issue edit` (`--title`, `--body-file`, `--add-label`, `--remove-label`, `--milestone`) |
| Create an issue | Write the description to a file, then `baton issue create --title "<title>" --label <label> <file>` (repeat `--label`; `-` reads the body from standard input) |
| Open a draft pull request | `baton pr create --draft --title "<title>" <file>` with `Closes #<N>` on its own line in the body |
| Mark a pull request ready | `baton pr ready <N>`, or `baton pr ready <N> --undo` to turn it back into a draft (the orchestrator, once the review is approved) |
| Post a review | `baton review post <N> <approve\|changes\|blocked\|replan> <full head sha> -` with the body as an inline heredoc. It refuses if the head moved. `blocked` needs a `Cause: default-branch` or `Cause: environment` line and a `## Reason` section, takes no `## Tasks`, and does not count as a review round. `replan` needs a non-empty `## Feedback` section, takes no `## Tasks`, and counts as a round. Failures are listed under `## Failures` as `- <id>: <caused\|default-branch\|flaky\|environment>`. |
| Answer a review | `baton comment pr <N> developer reply -`, one line per task (orchestrator, from the developers' results) |
| Set a plan | `baton plan set <N> <file\|->` (refiner): writes the whole `## Developer Plan` section (heading included) into the issue body, replacing an earlier one |
| Deprecated plan commands | `baton plan commit`, `baton plan link` and `baton plan pin` do nothing; do not use them |
| Migrate a plan | `baton plan migrate <N>` (refiner or orchestrator): converts an older plan (on a branch or in a legacy `## Plan` section) into `## Developer Plan` and removes the `Plan:` line |
| Tick a task | `baton task done issue <N> <task> [--as-built <text>]` (developer: ticks the task in the issue body, re-reading the body just before writing; `--as-built` adds the `Provides (as built):` line); `baton task done pr <P> <r.n>` (orchestrator only, review tasks; from a linked worktree set `BATON_ROLE=orchestrator` or it warns) |
| Replace a plan's open tasks | `baton plan replace <N> <from-task> <file> [--drop]` (orchestrator only; refuses fewer new tasks than replaced unless `--drop`; edits the body) |
| Insert tasks into a plan | `baton plan insert <N> <before-task> <file>` (orchestrator only; the new tasks go before that number, later open tasks are renumbered with their `task <n>` references; done tasks are refused; `total + 1` appends) |
| Set a plan's `Written against:` | `baton plan against <N> <full sha>` (refiner; edits the body) |
| Create or edit a milestone | `baton milestone create --title <t> [--description <d>] [--due-on <ISO date>]`; `baton milestone edit <number or title> [--title <t>] [--description <d>] [--due-on <date>] [--state open\|closed]` |
| Merge | `baton merge-pr <N>` only. `gh pr merge` is refused. |

Roles: `orchestrator`, `developer`, `refine`, `reviewer`. Kinds: `question`, `status`,
`decision`, `plan`, `review`, `findings`, `reply`. Reviews are posted only with
`baton review post`, which adds the verdict and the commit to the marker. Task boxes are ticked
only with `baton task done`. `baton merge-pr` merges in one pass; the plan lives in the issue body, so the branch holds no plan file.

Where a row says "write ... to a file", an inline heredoc works too and needs no file:
`--body "$(cat <<'EOF'` on one line, the text, then `EOF` and `)"` on the last two lines.

## Rules the publish gate enforces

The gate is a text scanner. It checks commands that talk to a forge (`gh`, `glab`, `tea`, `fj`,
`curl`, `wget`, `http`, `https`, `xh`) and git commit, push and tag. It refuses only:

- a privacy finding in the quoted text, heredoc or body file of such a command;
- text it cannot read: a body from stdin (`-F -`, `--body-file -`) without a heredoc or pipe, a
  body file that does not exist yet (write the file in one step, post it in the next), or `$(...)`
  or backticks in a double-quoted title, body or message. Inline text and inline heredocs
  (`"$(cat <<'EOF' ... EOF)"`) are fine;
- a hook bypass on a git commit, push or tag: `--no-verify`, `core.hooksPath`, or overriding
  `HOME` or `GIT_*` settings;
- missing git hooks.

It does not judge which command you use, labels, markers or directories. Other commands are allowed.

- The hooks refuse a commit or push that adds a line matching the brief's `forbid_added` rules
  (naming the line), unless the plan has `Allows: forbid_added "<glob>"`. Remove the entry.
- If the gate reports a privacy finding, replace the detail with a placeholder ("the NAS",
  "family-a/wedding", "the review share"). Never add the detail to `privacy_allow`.

## Closing keywords

GitHub closes an issue for `close`, `fix` or `resolve` (any form, with or without a colon) followed
by `#N`, anywhere in a pull request body or commit message. Write `Closes #<N>` once, for the one
issue the pull request resolves. Refer to any other issue as "see #N" or "related to #N".
