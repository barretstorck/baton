# Redesign of the publish gate (spike, issue #133)

Status: proposal from a spike, not yet approved. Supersedes the hardening list in #114.

## Summary

Today the gate parses shell text to decide whether a command is a forge write, then refuses every
write that is not a `baton` command. The parser is the problem: it is tied to `gh`, every evasion
found adds rules, and each rule refuses more harmless work. In a replay of 58,708 recorded `Bash`
commands it refused 3,857 (6.6%); 3,854 of them carried no private detail.

The proposal inverts the question. The gate stops asking "is this a write, and is it routed
through `baton`?" and asks only "would this command send text that is private or that I cannot
read?". It looks at the text a forge-facing command would send (quoted strings, heredocs, body
files), refuses on a privacy finding, on a body it cannot read, and on a git hook bypass. It
needs no knowledge of which subcommand writes. Everything about following Baton's workflow moves
to the skills. In the same replay the prototype refuses 251 commands (0.4%), keeps all 3 real
catches, and refuses 5 harmless non-publishing commands instead of 1,325.

## Privacy and safety

The gate and the git hooks guard exactly this, and nothing else:

1. **Privacy.** No private detail (private IPs, e-mail addresses, host paths, internal domains,
   private terms from the local list) in text that leaves the machine: issue and pull request
   titles and bodies, comments, review text, API payloads, commit and tag messages, and added
   lines in diffs. Scanning uses `scan.scan_text` with the brief's `privacy_allow`.
2. **No unscannable text.** A forge-facing command is refused when its body cannot be read at
   decision time: `-F -`, `--body-file -` or `--input -` with no heredoc or literal pipe in the
   same command, a body file that does not exist and is not created by the command, and a body in
   double quotes that contains a command substitution or backticks (the shell would run it, and
   the result is never seen).
3. **No hook bypass.** `--no-verify`, `-n` on commit or push, `-c core.hooksPath=`, setting
   `core.hooksPath`, and overriding `HOME`, `XDG_CONFIG_HOME`, `GIT_CONFIG_*` or
   `BATON_PRIVATE_TERMS` on a git publishing command. A bypass switches off the diff scan, which is
   the only scan of file content.
4. **Hooks installed.** `git commit` and `git push` are refused in a repository whose Baton hooks
   are missing (`githooks.installed`), as today.

Not safety, and not the gate's job: which `baton` command to use, labels, the Baton marker,
merging rules, and the repository or directory a command runs in.

What this lets through compared with today:

- Any raw `gh` write with a clean, scannable body (`gh issue comment`, `gh pr create`,
  `gh pr edit`, `gh pr merge`, `gh label create`, `gh api -X POST`). Today: refused with "use
  `baton ...`".
- `git commit` and `git push` in compound commands (`cd x && git commit`, `git add -A && git
  commit -m ... && git push`), with a variable, with `-C`, and with a message supplied from a
  command substitution. The hooks scan the message (pre-push) and the diff; today the gate refuses
  anything that is not "plain".
- Reads and local work whose text merely mentions `gh` or `git`: `python3 - <<EOF` scripts,
  `docker run`, `ssh`, `xargs`, `bash -c` strings that only read, `gh api` reads with extra
  options, commands whose `cd` target is a variable.
- Commands that name another repository (`gh -R`).

What it still refuses: a forge-facing command whose quoted text, heredoc or body file holds a
private detail; a body that cannot be read; a hook bypass. Nothing that the gate catches today
for privacy is let through (the replay shows the same 3 real catches).

## Workflow rules leave the gate

Each rule below enforces Baton's workflow, not privacy or safety. The gate stops refusing it.

| Rule today | Taken over by | Gate hint |
|---|---|---|
| Route writes through `baton` (`_USE`): comment, issue/pr create and edit, pr ready, review, close and reopen | `references/github.md` (command table) and the `develop`, `refine`, `review` and `sprint` skills, which already name each command | none; the table is already in the skills |
| Baton marker on agent comments (`<!-- baton:`; the gate only held an unused `MARKER` constant, the `baton` writers add the marker) | `baton comment` adds it; the skills tell agents to use it so that comments can be told apart. Add one sentence to the "What to trust" block of each skill saying a raw comment has no marker and is treated as maintainer text | none |
| `baton merge-pr` for merges (checks that CI is green, the review is done and the head branch names the issue) | `sprint` skill (orchestrator only), and `SKILL.md` "Never" lists for the other roles: "Merge any pull request" | one line when `gh pr merge` is seen: "`baton merge-pr <N>` checks CI and review first; the sprint skill says who merges" |
| Label changes (`gh label ...`, `gh issue edit --add-label`) and status labels | `baton set-status`, `baton labels` and the skills' status steps | none |
| `gh api` method and option checks (`_api_problem`) | nothing is needed: the scan covers the payload | none |

A hint is optional and non-blocking: it is an `additionalContext` string on an allowed command (if
the hook protocol offers it; otherwise stderr with exit 0). It is sent at most once per session
and only for the merge case, because every hint costs context tokens. The other three need none.

If a skill gap turns up in practice, the fix is a sentence in the skill, not a gate rule.

## Friction today

Method. The Claude Code projects folder under the home directory holds the transcripts that `baton usage` reads. Sessions
whose first prompt is a Baton dispatch (`usage.classify` returns a role other than "other") were
taken: 2,590 sessions. Every `Bash` tool call in them was replayed through `gate.decide` on the
default branch (f86c9cc), with the working directory set to a directory that has a brief, so the
gate is armed everywhere. This is an upper bound on what a session saw: the live gate does nothing
outside a brief directory, and older gate versions had other rules. The transcripts themselves
hold 298 gate refusals of the older versions.

Each refused command is classified by the scan of the text it would publish, plus a pattern for
"publishes at all" (a `gh` write verb, `gh api` with a write method or payload, `git commit`,
`push` or `tag`):

- **harmless**: not a publish at all (reads, greps, scripts, other programs that mention `gh` or
  `git`);
- **clean publish**: publishes, and the text is clean, so the refusal only forced the agent to
  rewrite the command;
- **private publish**: publishes text with a private finding (a real catch).

Results (counts of commands, 50,429 distinct):

| | Commands |
|---|---|
| `Bash` commands replayed | 58,708 |
| Publish-shaped | 5,346 |
| Refused | 3,857 (6.6%) |
| of which harmless | 1,325 |
| of which clean publish | 2,529 |
| of which private publish | 3 |

By first message:

| Message | Harmless | Clean publish | Private |
|---|---|---|---|
| "`<gh command>` is not allowed here; use `<baton command>`" | 0 | 1,343 | 1 |
| "cannot tell whether this writes to the forge" | 1,045 | 586 | 1 |
| "git publishing must be a plain command in the repository's own directory" | 0 | 549 | 0 |
| "a directory named by the command cannot be resolved" | 251 | 36 | 1 |
| "`gh api` must name exactly one endpoint" | 19 | 0 | 0 |
| "`<gh command>` is not allowed here; no baton command does this" | 6 | 1 | 0 |
| "`gh api` option <x> is not a plain read" | 4 | 0 | 0 |
| body file not found / stdin body (files from earlier sessions are gone in the replay, so partly an artefact) | 0 | 13 | 0 |
| git hook bypass | 0 | 1 | 0 |

Examples (placeholders): `python3 - <<EOF ... EOF` with a `git log` call in the script;
`docker run ... git ...`; `ssh <host> '... gh ...'`; `cd $DIR && gh issue view <N>` (variable
directory); `cd <dir> && git commit -m "<msg>" && git push`; `gh pr edit <N> --body "<text>"`;
`gh issue comment <N> --body-file <file>`. Of the 3,857 refusals, 34% were commands that did not
publish anything. During this spike the gate also refused three of the developer's own
file-writing commands (heredocs holding Python source that mention `git` and `gh`).

One more cost: 3 commands made `gate.decide` run for more than 2 seconds (a pattern blow-up in
the tokenizer or the `sed` check). The first replay took over 12 minutes for 58,000 commands
before a timeout was added.

## What the gate guards on GitHub, GitLab and Forgejo, and telling a write from a read

The proposal does not tell a write from a read. It does not need to, because the scan only
refuses when it finds something private, and a clean read passes unchanged. What it needs is the
list of programs that talk to a forge, as data:

```
forge_clis: gh, glab, tea, fj, curl, wget, http, https, xh      (default list in the gate, extendable in the brief)
```

A command is "forge-facing" when one of these appears in command position (start of the command
or after `;`, `&`, `|`, `(`, a quote, a newline, `sudo`, `env`, `xargs`, `exec`, `time` or
`nohup`, optionally with `NAME=value` words and a path prefix). The check is a single regular
expression over the raw text. It does not tokenize, so `env -S`, escapes, `bash -c '...'` strings
and wrapper chains need no handling: the quote and the wrapper are themselves command-position
markers.

For a forge-facing command the gate scans:

- every quoted string and every heredoc body in the command (so `--body`, `--title`, `-m`,
  `-f key=value`, `--jq` and inline heredocs are all covered, whatever the CLI calls them);
- the contents of a body file named by a flag (`-F`, `--body-file`, `--input`, `@file`) when it
  exists;
- for `curl`-like clients, only the values of data flags, not the URL (many agents call local
  services, and a private address in a URL is not a leak to a forge).

Bare words (paths, URLs, issue numbers) are not scanned: scanning the whole command refused
harmless commands that name a local path (the first prototype run refused 621 of them on the
host-path pattern).

Per forge nothing differs: `glab mr create --description "<text>"` and `tea issues create
--description "<text>"` are caught by the same quoted-string rule. A per-CLI table is only needed
for the body-file flag names (`gh -F`/`--body-file` and the equivalents of other CLIs; these are data
rows, not a parser) and can start empty, since a body file is also scanned by the `baton` writers
and a missing flag name only means the file content is not scanned.

Git is guarded separately from the forge CLIs: `git commit`, `push` and `tag` are not parsed for
their messages. The hooks scan them (the `pre-push` hook already scans every pushed commit
message and diff). Two hook changes make this complete: a `commit-msg` hook (scan at commit time,
so a bad message is fixed before it is part of history) and a tag-message check in `pre-push`
(annotated tag objects are not scanned today).

## Forge MCP tools

Add a second `PreToolUse` matcher, `mcp__.*`, running the same entry point. For an MCP call the
tool input is structured JSON, so the gate collects every string value (recursively) and runs
`scan.scan_text` on them; it refuses on a finding and otherwise allows. It never looks at the tool
name, so it works for the Forgejo MCP server, a GitHub or GitLab server, or a non-forge server
such as a chat tool.

Trade-offs:

- (+) No read/write allowlist to maintain per server, and no new parser; exact text, no guessing.
- (+) It also covers non-forge publishing (messages, documents) at no extra cost.
- (-) A read whose arguments contain a private-looking string is refused (for example a search for
  a private IP). That is rare, and the refusal names the finding, so the agent can rephrase.
- (-) One more process start per MCP call (about 50 ms) and a hook registration to keep in
  `hooks/hooks.json`.
- (-) File upload tools receive a path; the content is not scanned (accepted).
- Alternatives considered: an allowlist of read tools (needs upkeep per server and refuses new
  read tools), and leaving MCP out (the status quo; a documented hole).

## HTTP clients

`curl`, `wget`, `http`/`https` (HTTPie) and `xh` are forge-facing in the list above, so a write to
a forge API gets the same treatment: the values of `-d`/`--data*`/`--json`/`-F`/`--form` and the
content of `@file` are scanned; `@-` or `-d -` with no heredoc or literal pipe in the same command
is refused as unscannable. The URL is not scanned.

Left outside, on purpose: HTTP from scripts (`python3` with `requests`, `node` with `fetch`,
`ruby`). Seeing into them would need the interpreter parsing the gate removes. They are covered by
the git hooks when their output is committed, and the design note for the gate says what it
cannot see (as the docstring does now). If a script publishes private text, that is the agent's
responsibility and a skill sentence ("do not publish from scripts; use `baton`") is the control.

## Findings 1 to 7

| # | Finding | Outcome | Reason |
|---|---|---|---|
| 1 | `env -S`, abbreviations, escapes, split words | Fixed by the design | No wrapper parsing. A wrapper cannot hide the quoted text or heredoc from the scan. A trigger written as `g\h` or split across words is not matched; accepted (not a sandbox) |
| 2 | exporter assignments | Already fixed | The bypass check for `HOME=`, `XDG_CONFIG_HOME=`, `GIT_CONFIG*=` and `BATON_PRIVATE_TERMS=` is a regular expression over the raw text of a git publishing command |
| 3 | `git -c include.path`, `includeIf`, aliases, bad environment on non-publishing git | Partly fixed, partly accepted | The hooks run for every commit and push whatever the subcommand name is, so an alias does not avoid the scan. Refused: `-c core.hooksPath`, `--config-env`, `GIT_CONFIG*`. `include.path` pointing at a file that sets `core.hooksPath` is accepted: it needs an agent to write such a file first. Follow-up: a CI check that runs the same scan on the pull request as a backstop that does not depend on local hooks |
| 4 | `_DATA_PROGRAMS` can run programs; variable program words | Fixed by the design | The list is deleted. No program is inferred to be a launcher; only the forge-CLI list matters |
| 5 | Repository selectors, unresolvable `cd` | Fixed by the design | The scan does not depend on the repository or directory. A brief is used only for `privacy_allow`; when none is found from the working directory the default `privacy_allow` applies and the scan still runs. The gate no longer allows a command because it could not resolve a `cd` |
| 6 | `sed` and `awk` program detection | Fixed by the design | Removed with the "may run gh" inference. Accepted residue: a `sed 'e'` that builds a forge command from non-literal text |
| 7 | Dashed git, `bash -c` strings, non-shell heredocs | Fixed by the design, with one accepted gap | `(?:\S*/)?git(?:-\w+)?` is matched by the trigger; a quote in command position starts a trigger inside a `bash -c` string; a heredoc passed to `python3` is not a command unless the forge CLI is in command position inside it. Accepted: `subprocess.run(["gh", ...])` in a script |

Weighed against refusals: no finding needs a new refusal of a harmless command, because none of
the fixes is a parsing rule. Findings 1, 4, 5, 6 and 7 are removed by deleting the code that
produced them. The two rows with residue (3, 7) add only the three follow-ups below.

## Friction of the proposal

A throwaway prototype (`prototypes/gate-133/proposed.py`, not for use) implements the rules above:
trigger regular expression, bypass check, unscannable-body checks and the quoted-string and
heredoc scan. The same 58,708 commands through it (the file-existence check is off in the replay,
because the body files of old sessions are gone):

| | Current gate | Prototype |
|---|---|---|
| Refused | 3,857 | 251 |
| harmless (not a publish) | 1,325 | 5 |
| clean publish | 2,529 | 243 |
| private publish | 3 | 3 |

What remains:

- 233 clean-publish refusals for a body in double quotes that holds a backtick or `$(`. Most are
  markdown inline code in a `--body "..."`: the shell runs those backticks, so the body that is
  posted is not the body that was written. The refusal is useful, but the prototype regular
  expression is crude and some of the 233 are likely single-quoted or escaped. Tuning is left to
  the implementation. The replacement for the agent is a heredoc or file, as the skills already
  say.
- 10 refusals for the hook-bypass pattern; most are `-e HOME=...` in a `docker run`, which the
  implementation should limit to `git` words before the match (a prototype artefact).
- 5 harmless refusals for a private address or a host path in a quoted string of a forge-facing
  command (a `ssh` command that also uses `gh`). Correct by the privacy rule.
- the 3 private publishes are still found.

Refusals fall by 93% overall, by 99.6% for commands that do not publish, and by 90% for clean
publishes, with no real catch lost. The prototype was tuned on this corpus; expect some
regression on commands not in it, but the two largest groups (1,343 "use baton" and 549 "not
plain") are removed by design, not by tuning.

## GitHub-specific parts of `gate.py`

After the redesign, nothing in the gate needs `gh` semantics. What remains GitHub-specific:

- the default `forge_clis` list (a data line; `gh` is one entry);
- the optional merge hint (names `gh pr merge` and `baton merge-pr`);
- body-file flag names per CLI (`gh -F`/`--body-file`), data rows.

Deleted with the redesign: `_GH_READ_ACTIONS`, `_GH_READ_GROUPS`, `_USE`, `_gh_words`,
`_api_problem`, `_gh_problems`, `_METHOD_OPTS`, `_API_*`, `_DATA_PROGRAMS`, `_FIND_RUNNERS`,
`_SED_RUNS`, `_AWKS`, `_WRAPPERS`, the `cd`/directory resolution and the git option parser. The
file becomes small (an estimate: under 150 lines, from 591). The `baton` writers (`publish.py`,
`comment.py`, `review.py`, `state.py`, `merge.py`) stay GitHub-specific until forge support lands,
and are separate from the gate.

## Follow-ups

1. **Rewrite the gate as a text scanner (replace `_gh_problems` and the git parser).** Implement
   the four safety rules and the forge-facing scan above, with fail-closed behaviour only when a
   forge-facing trigger matched (an exception on any other command allows it), and a time limit.
2. **Add a `commit-msg` hook and scan annotated tag messages in `pre-push`.** Moves message
   scanning out of the gate completely.
3. **Add a `PreToolUse` matcher for `mcp__.*` that scans string inputs.** One entry point, no
   tool-name list.
4. **Skill text for the rules that leave the gate.** The marker sentence in the "What to trust"
   block, the "use `baton` for these writes" table in each role skill (already mostly there),
   and the optional merge hint.
5. **Backstop scan in CI for pull requests (privacy only).** Covers a local hook bypass by
   `include.path` or a script, without more parsing in the gate. Optional.
6. **Replay harness as a test.** Keep a small fixed corpus of anonymised command shapes (from this
   replay) that must not be refused, so that new rules show their cost in a test. The
   replay script in the prototype directory can seed it.
7. **Forge support config.** `forge_clis` and body-file flag names in the brief, so GitLab or
   Forgejo needs data only; the `baton` writers remain a separate effort.
