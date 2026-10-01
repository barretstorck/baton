# Baton

A Claude Code plugin that runs software sprints on a forge repository: an orchestrator agent
dispatches developer agents in parallel, checks and merges their pull requests, and writes a
retrospective. The forge holds all state, so any session can resume a sprint.

The workflow is adapted from the GitLab handbook's
[Engineering Workflow](https://handbook.gitlab.com/handbook/engineering/workflow/) and
[Product Development Flow](https://handbook.gitlab.com/handbook/product-development/how-we-work/product-development-flow/).

- Design: [docs/superpowers/specs/2026-10-01-baton-design.md](docs/superpowers/specs/2026-10-01-baton-design.md)
- Plans: [docs/superpowers/plans/](docs/superpowers/plans/)

## Status

Foundation only: `/baton:init`, the privacy publish gate, git hooks, label setup and `baton merge-pr`.
The `develop`, `refine`, `sprint-plan` and `sprint` skills come in later plans.

## Commands

| Command | Purpose |
|---|---|
| `baton config` | Print the resolved settings from `.baton/brief.md` |
| `baton labels [--create]` | Check or create the labels Baton uses |
| `baton set-status <issue> <status:name>` | Switch an issue's status label |
| `baton install-hooks` | Install the `pre-commit` and `pre-push` privacy hooks |
| `baton merge-pr <number>` | Run the acceptance checks and merge |

## Rules the gate enforces in a Baton repository

- Comments posted by agents must start with `<!-- baton:<role>:<kind> -->`.
- Comment and issue text must be literal: inline, an inline heredoc, or a file written in an
  earlier step. Text from stdin, a variable or command substitution is refused.
- `gh pr merge` is refused; use `baton merge-pr <number>`.
- Git hooks cannot be skipped (`--no-verify`, `core.hooksPath`).
- A pull request that touches a protected path needs a maintainer comment
  `/baton approve <head commit sha>` (at least 7 characters of the sha).

The gate guards against mistakes. It is not a sandbox: it only sees `gh` and `git` commands.

## Tests

    PYTHONPATH=lib python3 -m unittest discover -s tests -v
