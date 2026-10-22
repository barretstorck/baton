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
| A pull request's review state | `baton review state <N>`: `none`, `changes`, `replied`, `stale`, `approved` or `escalate` |
| An issue's plan tasks | `baton tasks issue <N>` (JSON: `written_against`, `tasks`, `done`, `total`, `next`); reads the plan file on the issue branch, or a legacy `## Plan` in the body |
| An issue's plan file | the issue's first line is `Plan: [path](link)`; in a checkout of `issue-<N>-<slug>` it is `<docs.dir>/plans/<N>-<slug>.md` (`docs.dir` from `baton config`) |
| A review's tasks | `baton tasks pr <N>` (the latest review's `## Tasks`; none after an approval) |
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
| Link an issue to its plan | `baton plan link <N> <branch> <path>` (refiner, after pushing the plan file); `baton plan link <N> --at <sha>` pins it to a commit |
| Migrate a legacy plan | `baton plan migrate <N> <branch>` (refiner or orchestrator, for an issue not yet in development) |
| Tick a task | `baton task done issue <N> <task>` (developer: ticks the plan file in the checkout, committed with the code); `baton task done pr <P> <r.n>` (orchestrator only, review tasks; from a linked worktree set `BATON_ROLE=orchestrator` or it warns) |
| Replace a plan's open tasks | `baton plan replace <N> <from-task> <file> [--drop]` (orchestrator only; refuses fewer new tasks than replaced unless `--drop`; a linked plan is committed to the issue branch and pushed) |
| Merge | `baton merge-pr <N>` only. `gh pr merge` is refused. |

Roles: `orchestrator`, `developer`, `refine`, `reviewer`. Kinds: `question`, `status`,
`decision`, `plan`, `review`, `findings`, `reply`. Reviews are posted only with
`baton review post`, which adds the verdict and the commit to the marker. Task boxes are ticked
only with `baton task done`. A merge removes the plan file from the branch first; run
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
