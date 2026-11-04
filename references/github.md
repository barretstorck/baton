# GitHub reference for Baton skills

How each step in the Baton skills is done on GitHub. Run everything from inside the repository.
`baton` is on `PATH` when the plugin is enabled; otherwise use `${CLAUDE_PLUGIN_ROOT}/bin/baton`. `baton <command> --help` prints a command's usage.

Baton has no version number. To identify the installed build, read the commit SHA in
`~/.claude/plugins/installed_plugins.json` (the `version` of the `baton@baton` entry).

Other roles also read `${CLAUDE_PLUGIN_ROOT}/references/github-shared.md` and their own `references/github-<role>.md`.

## Reading

| Step | Command |
|---|---|
| Resolved settings | `baton config` (settings and the `identity` tier) |
| Commit as the `baton` App (tier 1; no-op at tier 0) | `baton identity worktree` (run in a linked worktree) |
| Push a branch | `baton push <source>:refs/heads/<branch>`: `git push origin <refspec>` at tier 0, HTTPS with the `baton` App token at tier 1. The branch must not be the default branch, and the refspec has no leading `-` or `+` and no empty source, and no `*`, `^`, `?`, `[`, `\` or whitespace; the destination must pass `git check-ref-format`, and at tier 0 `origin` must be the repository `gh repo view` resolves; anything else is refused with exit code 1 |
| An issue with its comments | `baton issue read <N>`: text by non-maintainers is withheld as `[untrusted text from @<author> withheld; see Triage summary]`; never use `gh issue view` for issue text. Output: a header (`#<N> <title>`, `State:`, `Author:`, `Labels:`), a line `--- body ---`, the body, a line `--- comments ---`, then per comment `--- comment by @<login> ---` and its text |
| The latest agent question and the maintainer's replies | `baton answers <N>` (at tier 1 the Baton logins' marker comments count, but their replies never count as answers; `/baton ready` and `/baton approve` also work only from maintainers) |
| Open pull requests | `gh pr list --state open --json number,title,headRefName,isDraft,closingIssuesReferences` |
| An issue's plan tasks | `baton tasks issue <N>` (JSON: `written_against`, `tasks`, `done`, `total`, `next`); reads the `## Developer Plan` section of the issue body |
| An issue's plan | the `## Developer Plan` section of the issue body: `baton issue read <N> | sed -n '/^## Developer Plan/,/^--- comments ---$/p'`. The body is limited to 65536 characters; a longer one is refused and the issue must be split. Older issues: a plan still on `refs/plans/<N>` or on a branch is moved into the body by `baton plan migrate <N>`. |
| A review's tasks | `baton tasks pr <N>` (the latest review's `## Tasks`; none after an approval) |

Every skill reads issue text with `baton issue read <N>`, never with raw `gh issue view` or `gh api`: text by anyone but a maintainer is withheld, and the `## Triage summary` section stands in for it.

## Writing

These commands are the rule, not only what the gate checks: every write to the forge goes through the `baton` command in this table. A raw `gh` write (`gh issue comment`, `gh pr create`, `gh pr merge`, `gh label ...`, `gh api -X POST ...`) is not allowed even where the gate lets it through. The gate lets a clean raw `gh` write pass, but the `baton` command adds the Baton marker, refuses an empty body, and at tier 1 writes as the `baton` App (a raw `gh` write runs with the maintainer's own login).

| Step | Command |
|---|---|
| Set an issue's status | `baton set-status <N> status:<name>` (removes the previous status) |
| Post a comment | `baton comment <issue\|pr> <N> <role> <kind> -` with the body as an inline heredoc on standard input, or a file path in place of `-`. It scans the text, adds the Baton marker and prints the comment URL. |
| Open a draft pull request | `baton pr create --draft --title "<title>" [--head <branch>] <file>` (from a detached checkout it uses the pushed branch at HEAD as the head) with `Closes #<N>` on its own line in the body |
| Tick a task | `baton task done issue <N> <task> [--as-built <text>]` (developer: ticks the task in the issue body, re-reading the body just before writing; `--as-built` adds the `Provides (as built):` line); `baton task done pr <P> <r.n>` (orchestrator only, review tasks; from a linked worktree set `BATON_ROLE=orchestrator` or it warns) |

Roles: `orchestrator`, `developer`, `refine`, `reviewer`. Kinds: `question`, `status`,
`decision`, `plan`, `review`, `findings`, `reply`. Reviews are posted only with
`baton review post`, which adds the verdict and the commit to the marker. Task boxes are ticked
only with `baton task done`. `baton merge-pr` merges in one pass; the plan lives in the issue body, so the branch holds no plan file.

Where a row says "write ... to a file", an inline heredoc works too and needs no file:
`--body "$(cat <<'EOF'` on one line, the text, then `EOF` and `)"` on the last two lines.

## Rules the publish gate enforces

The gate is a text scanner. It checks commands that talk to a forge (`gh`, `glab`, `tea`, `fj`,
`curl`, `wget`, `http`, `https`, `xh`) and git commit, push and tag. It refuses:

- a privacy finding in the quoted text, heredoc or body file of such a command;
- text it cannot read: a body from stdin (`-F -`, `--body-file -`, `--description -`) without a heredoc
  (a pipe does not count), a body file that does not exist yet (write the file in one step, post
  it in the next), or a variable (`$B`, `${B}`), `$(...)` or backticks in a title, body, message
  or field value. Unquoted title, body and field values are scanned like quoted ones. Pass the
  text literally, as an inline heredoc, or as a file. Inline text and inline heredocs
  (`"$(cat <<'EOF' ... EOF)"`) are fine. A body file is read relative to the last literal `cd <dir>`
  before it in the same command (else the working directory), and a double-quoted body whose only
  substitutions are `$(cat <literal path>)` is scanned by reading those files; any other `$(...)`,
  backticks or variable, or a `cd` to a non-literal target, is still refused as unreadable text;
- a hook bypass on a git commit, push or tag: `--no-verify`, `core.hooksPath`, or overriding
  `HOME` or `GIT_*` settings;
- missing git hooks;
- in a Baton repository, a raw `gh issue|pr close|reopen` without `--comment`. From Claude Code
  2.1.290 the gate adds the `baton:<role>:closed` (or `reopened`) marker to the comment through a
  rewrite that keeps the permission prompt; otherwise, or for a compound command, it refuses and
  names the marker line to add;
- in a repository with a brief, a raw `gh issue create`, `gh pr create`, `gh issue edit` or
  `gh pr edit` whose `--body` / `-b` value or `--body-file` / `-F` file is empty or only
  whitespace; write the full text first.

It does not judge which command you use, labels, markers or directories. Other commands are allowed.

- The hooks refuse a commit or push that adds a line matching the brief's `forbid_added` rules
  (naming the line), unless the plan has `Allows: forbid_added "<glob>"`. Remove the entry.
- If the gate reports a privacy finding, replace the detail with a placeholder ("the NAS",
  "family-a/wedding", "the review share"). Never add the detail to `privacy_allow`.

## Closing keywords

GitHub closes an issue for `close`, `fix` or `resolve` (any form, with or without a colon) followed
by `#N`, anywhere in a pull request body or commit message. Write `Closes #<N>` once, for the one
issue the pull request resolves. Refer to any other issue as "see #N" or "related to #N".
