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
| Open pull requests | `gh pr list --state open --json number,title,headRefName,isDraft,closingIssuesReferences` |
| A pull request's checks | `gh pr checks <N>` |
| A pull request's draft state and CI state (reviewer) | `baton pr-state <N>` (JSON: `draft`, `ci`: `green`, `red`, `pending` or `none`, `verify`: `none`, `missing`, `green`, `red` or `timeout`, `problems`) |
| Full verification of a pull request (reviewer) | `baton verify <PR> <worktree>`: runs the brief's `verify.command` in the worktree (at the pull request's head, clean); prints `verify <state> commit=<sha7> exit=<n> duration=<s>s`, `log: <path>` and the last 40 lines; writes nothing to the pull request. Exit 0 green, 1 red or refused, 3 timeout |
| A pull request's review state | `baton review state <N>`: `none`, `changes`, `replied`, `stale`, `approved` or `escalate` |
| An issue's plan tasks | `baton tasks issue <N>` (JSON: `written_against`, `tasks`, `done`, `total`, `next`); reads the plan file from `refs/plans/<N>`, or (older way) on the issue branch, or a legacy `## Plan` in the body |
| An issue's plan file | the issue's first line is `Plan: [path](link)`. A plan on the ref is read with `git fetch --quiet origin +refs/plans/<N>:refs/baton/plans/<N>`, then `git show refs/baton/plans/<N>:<path>` (the path is in `baton tasks issue <N>`; it is `<docs.dir>/plans/<N>-<slug>.md`, `docs.dir` from `baton config`). Only a plan still on the branch (the older way) is a file in a checkout of `issue-<N>-<slug>`. |
| A review's tasks | `baton tasks pr <N>` (the latest review's `## Tasks`; none after an approval) |
| Clean up leftovers (orchestrator) | `baton clean`: removes agent worktrees under `.claude/worktrees/` that are not on an open issue's branch, have no uncommitted changes and no unmerged commits, and the log folders of closed or merged pull requests; prints `removed`/`kept` lines, exit 1 if one could not be removed |
| Quota use | `baton usage --repo <owner/repo> [--since <UTC ISO time>] [--json]`: per issue and role, from local transcripts |
| Repository owner and default branch | `gh repo view --json owner,defaultBranchRef` |

## Writing

| Step | Command |
|---|---|
| Set an issue's status | `baton set-status <N> status:<name>` (removes the previous status) |
| Add or remove another label | `gh issue edit <N> --add-label <label>` / `--remove-label <label>`; use the names from `labels.map` in `baton config` |
| Post a comment | `baton comment <issue\|pr> <N> <role> <kind> -` with the body as an inline heredoc on standard input, or a file path in place of `-`. It scans the text, adds the Baton marker and prints the comment URL. |
| Edit an issue description | Write the full new description to a file, then `gh issue edit <N> --body-file <file>` |
| Create an issue | Write the description to a file, then `gh issue create --title "<title>" --body-file <file> --label <labels>` |
| Open a draft pull request | `gh pr create --draft --title "<title>" --body-file <file>` with `Closes #<N>` on its own line in the body |
| Mark a pull request ready | `gh pr ready <N>` (the orchestrator, once the review is approved) |
| Post a review | `baton review post <N> <approve\|changes> <full head sha> -` with the body as an inline heredoc. It refuses if the head moved. |
| Answer a review | `baton comment pr <N> developer reply -`, one line per task (orchestrator, from the developers' results) |
| Commit a plan | `baton plan commit <N> <path> <file\|->` (refiner): commits the plan file to `refs/plans/<N>` (no checkout needed) and pushes it |
| Link an issue to its plan | `baton plan link <N> refs/plans/<N> <path>` (refiner, after `plan commit`); `baton plan link <N> <branch> <path>` is the older way for a plan on a branch; `baton plan link <N> --at <sha>` pins it to a commit |
| Pin a plan link | `baton plan pin <N>` pins the `Plan:` link to the ref's tip. `baton merge-pr` does it; the orchestrator runs it for an issue closed without a merge. |
| Migrate a legacy plan | `baton plan migrate <N>` (refiner or orchestrator, for an issue not yet in development; moves it to `refs/plans/<N>`; the older `baton plan migrate <N> <branch>` form still works) |
| Tick a task | `baton task done issue <N> <task> [--as-built <text>]` (developer: for a plan on the ref, commits the tick (and with `--as-built` the `Provides (as built):` line) to `refs/plans/<N>`, prints the sha and does not push; push it with the code in one atomic push: `git push --atomic origin HEAD:refs/heads/<branch> <sha>:refs/plans/<N>`. For a plan on the branch it ticks the file in the checkout, committed with the code); `baton task done pr <P> <r.n>` (orchestrator only, review tasks; from a linked worktree set `BATON_ROLE=orchestrator` or it warns) |
| Replace a plan's open tasks | `baton plan replace <N> <from-task> <file> [--drop]` (orchestrator only; refuses fewer new tasks than replaced unless `--drop`; a plan on the ref is committed to `refs/plans/<N>` and pushed; a plan on a branch is committed to the issue branch and pushed) |
| Merge | `baton merge-pr <N>` only. `gh pr merge` is refused. |

Roles: `orchestrator`, `developer`, `refine`, `reviewer`. Kinds: `question`, `status`,
`decision`, `plan`, `review`, `findings`, `reply`. Reviews are posted only with
`baton review post`, which adds the verdict and the commit to the marker. Task boxes are ticked
only with `baton task done`. For a plan on the ref, `baton merge-pr` pins the link and merges in one pass. Only for a plan still on a branch (the older way) does a merge remove the plan file from the branch first; run
`baton merge-pr <P>` again after the checks pass on the removal commit.

Where a row says "write ... to a file", an inline heredoc works too and needs no file:
`--body "$(cat <<'EOF'` on one line, the text, then `EOF` and `)"` on the last two lines.

## Rules the publish gate enforces

- Body files must exist before the command that posts them runs. Write the file in one step and
  post it in the next.
- No text from stdin, a shell variable or `$(...)` in a title, body or commit message. Inline text
  and inline heredocs (`"$(cat <<'EOF' ... EOF)"`) are fine.
- No `--no-verify`, `core.hooksPath` or overriding `HOME` or `GIT_*` settings on a commit or push.
- If the gate reports a privacy finding, replace the detail with a placeholder ("the NAS",
  "family-a/wedding", "the review share"). Never add the detail to `privacy_allow`.

## Closing keywords

GitHub closes an issue for `close`, `fix` or `resolve` (any form, with or without a colon) followed
by `#N`, anywhere in a pull request body or commit message. Write `Closes #<N>` once, for the one
issue the pull request resolves. Refer to any other issue as "see #N" or "related to #N".
