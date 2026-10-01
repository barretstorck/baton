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
| Repository owner and default branch | `gh repo view --json owner,defaultBranchRef` |

## Writing

| Step | Command |
|---|---|
| Set an issue's status | `baton set-status <N> status:<name>` (removes the previous status) |
| Add or remove another label | `gh issue edit <N> --add-label <label>` / `--remove-label <label>`; use the names from `labels.map` in `baton config` |
| Post a comment | Write the body to a file with the Write tool, then `baton comment <issue\|pr> <N> <role> <kind> <file>`. It scans the text, adds the Baton marker and prints the comment URL. |
| Edit an issue description | Write the full new description to a file, then `gh issue edit <N> --body-file <file>` |
| Create an issue | Write the description to a file, then `gh issue create --title "<title>" --body-file <file> --label <labels>` |
| Open a draft pull request | `gh pr create --draft --title "<title>" --body-file <file>` with `Closes #<N>` on its own line in the body |
| Mark a pull request ready | `gh pr ready <N>` |
| Merge | `baton merge-pr <N>` only. `gh pr merge` is refused. |

Roles: `orchestrator`, `developer`, `refine`. Kinds: `question`, `status`, `decision`, `plan`,
`review`, `findings`.

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
