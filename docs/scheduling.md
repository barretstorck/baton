# Running a sprint on a schedule

A scheduled run notices what happened while no session was open (an answer left on an issue, CI
finishing) and moves the sprint forward. It never starts a sprint on its own; plan one with
`/baton:sprint-plan` first.

## What a scheduled run does

1. Takes the run lock, and exits at once if another run holds it.
2. Works the open sprint exactly as an interactive run does, without asking questions in the
   session: anything that needs the maintainer is left on the issue with `needs-maintainer`.
3. Waits for the developers it started, then releases the lock and exits.

## With cron

Run from a checkout of the target repository that has the Baton git hooks installed.

```cron
# every 30 minutes; the lock makes overlapping firings harmless
*/30 * * * * cd /path/to/repo && CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 claude -p "/baton:sprint --scheduled" --permission-mode auto --permission-prompts none >> "$HOME/.baton/sprint.log" 2>&1
```

Notes:

- `claude -p` stays open while background developers are running. By default it stops waiting
  after ten idle minutes, which is shorter than most issues take;
  `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0` removes that limit.
- `--permission-prompts none` denies anything that would have asked a person. Allow the commands
  the developers need (`git`, `gh`, `baton`, the project's test command) in the repository's
  `.claude/settings.json` permission rules, or they will be refused.
- If the plugin is not installed for your user, add `--plugin-dir /path/to/baton`.
- The lock is a file in the repository's `.git` directory. A lock older than six hours is treated
  as abandoned; change that with `baton lock acquire --ttl-hours N` in your own wrapper.
- Developers run as subagents of the scheduled session, so each firing can use a lot of quota.
  Set `developer.max_parallel` in the brief accordingly.

## With Claude Code routines

A routine that starts a fresh session on each firing with the prompt `/baton:sprint --scheduled`
does the same job. The session's environment needs the repository, `gh` signed in, the plugin
enabled and `~/.baton/private-terms.txt` present; a fresh cloud container has none of the files in
`~/.baton`, so the privacy scan would only apply its generic patterns there.

## Triage on a schedule

`/baton:triage --scheduled` triages new issues while no session is open. It uses polling, not
webhooks, and needs no forge CI. A run:

1. Takes the same run lock as a sprint run, so the two never overlap, and exits at once if another
   run holds it.
2. On its first run, records the start time and triages nothing; later runs only look at issues
   created after it, never at older ones.
3. Triages open issues that have no Baton status label and no `closed:*` label, oldest first, one
   triager per issue. At most `triage.max_per_run` are triaged per run; the rest wait for the next
   run. If an issue has waited more than a day, the maintainer is notified once.
4. Closes `awaiting-feedback` issues with no new comment in `triage.stale_days` days, with a
   comment saying they can be reopened.
5. Releases the lock and prints one line with the counts.

With cron, from a checkout of the repository:

```cron
# every 30 minutes; hourly is also fine
*/30 * * * * cd /path/to/repo && claude -p "/baton:triage --scheduled" --permission-mode auto --permission-prompts none >> "$HOME/.baton/triage.log" 2>&1
```

With a Claude Code routine, use the same prompt. The container has no `~/.baton`, so the start time
would be "now" on every firing and nothing would be triaged: set `triage.since` in the brief to an
ISO 8601 time.

Brief keys:

- `triage.since`: start time (default empty: recorded on the first run);
- `triage.max_per_run`: issues triaged per run (default 10);
- `triage.stale_days`: days without feedback before closing (default 14);
- `triage.max_reproductions_per_day`: bugs handed to the refiner for reproduction per 24 hours
  (default 3, 0 turns hand-off off).

After triage, the run hands triaged bugs to the refiner: it posts a `<!-- baton:repro-handoff -->`
comment on each and starts a refiner, up to `triage.max_reproductions_per_day`. The refiner
reproduces only in the sandbox (`baton sandbox`). Without a sandbox configured it posts the
`repro-skipped` notice instead and does not reproduce. An issue from outside still needs a
maintainer's `/baton ready` comment.

## Stopping

Remove the cron line or disable the routine. To stop a run in progress, end the process and run
`baton lock release`. The sprint's state is on the forge, so nothing is lost.
