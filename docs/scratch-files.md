# Where agents keep scratch files

Baton agents often need scratch files: diffs, logs, a draft decision. Each agent has one folder for them, and the session and the other agents are never touched.

## The folders

- **Developer, refiner, reviewer and researcher** (the agents with their own worktree) keep every scratch file in `.baton-scratch/` at the root of their own worktree. They never stage or commit it (they stage files by name). Before returning, they remove only that folder. The folder is inside the worktree because Claude Code's worktree isolation refuses commands outside it.
- **The triager** has no worktree. It writes its decision to `<tmp>/baton-triager-<N>/triage-<N>.json`, where `<N>` is the issue number. After `baton triage apply` it removes only `<tmp>/baton-triager-<N>`.

`<tmp>` is the temporary directory the triager was given: the session scratchpad when Claude Code names one, else `$TMPDIR`, else `/tmp`.

## What agents must not do

The session scratchpad, `$TMPDIR` and `/tmp` are shared with the session and the other agents. No agent writes directly in them, and no agent removes them or their contents. Each agent removes only its own folder.

## Leftovers

`baton clean` removes leftover worktrees together with their `.baton-scratch/`.

## The gate

The gate refuses a Baton agent's `rm` of `/tmp`, `$TMPDIR` or a `scratchpad` folder, or of all their contents. It recognises these by shape, because the hook does not see the scratchpad path. It is a guardrail against accidents, not a boundary: a command it cannot split into words is allowed.
