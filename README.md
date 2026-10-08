# Baton

A Claude Code plugin that runs software sprints on a forge repository: an orchestrator agent
dispatches developer agents in parallel, has a reviewer agent review each pull request, merges
the approved ones, and writes a retrospective. The forge holds all state, so any session can resume a sprint.

The workflow is adapted from the GitLab handbook's
[Engineering Workflow](https://handbook.gitlab.com/handbook/engineering/workflow/) and
[Product Development Flow](https://handbook.gitlab.com/handbook/product-development/how-we-work/product-development-flow/).

The review loop is adapted from the [superpowers](https://github.com/obra/superpowers) plugin's
code-review skills.

- Design: [docs/superpowers/specs/2026-10-01-baton-design.md](docs/superpowers/specs/2026-10-01-baton-design.md)
- Plans: [docs/superpowers/plans/](docs/superpowers/plans/)

## Status

Two sprints have run on a real repository. Version 0.2.0 adds the designated reviewer.

## How a pull request gets merged

1. A developer agent implements the issue, verifies it, pushes, and leaves the pull request as
   a draft.
2. The orchestrator dispatches a reviewer agent. It reviews the head commit against the issue
   and the brief and posts one review: `approve`, or `changes` with each problem marked "fix in
   this pull request" or "follow-up issue".
3. On `changes`, a developer answers each finding (fixed, or declined with a reason) and the
   reviewer looks again, up to `review.max_rounds` rounds before the maintainer is asked.
4. Once approved, the orchestrator marks the pull request ready, so CI runs once, and
   `baton merge-pr` merges it after checking the approval still covers the head commit.

## Skills

| Skill | Purpose |
|---|---|
| `/baton:init` | Set a repository up: brief, labels, private files, git hooks |
| `/baton:refine <issue>` | Bring an issue up to the definition of ready |
| `/baton:sprint-plan` | Plan a sprint with the maintainer and create its milestone |
| `/baton:sprint` | Work a sprint: dispatch developers and reviewers, collect answers, merge, retrospective |
| `/baton:develop <issue>` | Take one issue to one pull request (used by the `baton-developer` subagent) |
| `/baton:review <pr>` | Review one pull request and post a verdict (used by the `baton-reviewer` subagent) |

Both subagents run on Sonnet at medium effort; `developer.model` and `reviewer.model` in the brief
change the model.

Typical use:

    /baton:sprint Pick issues that can be completed within 1 day. Use up to 4 parallel subagents.

Unattended runs are described in [docs/scheduling.md](docs/scheduling.md).

## Commands

| Command | Purpose |
|---|---|
| `baton config` | Print the resolved settings from `.baton/brief.md` |
| `baton labels [--create]` | Check or create the labels Baton uses |
| `baton set-status <issue> <status:name>` | Switch an issue's status label |
| `baton install-hooks` | Install the `pre-commit` and `pre-push` privacy hooks |
| `baton merge-pr <number>` | Check the review, CI and the rest, then merge |
| `baton comment <issue\|pr> <number> <role> <kind> <file \| ->` | Scan, stamp and post an agent comment |
| `baton review state <pr>` | The pull request's review state at its head commit |
| `baton review post <pr> <approve\|changes> <sha> <file \| ->` | Post a review of that commit |
| `baton answers <issue>` | The latest agent question and the maintainer's replies |
| `baton ready-check <issue>` | The mechanical half of the definition of ready |
| `baton sprint-status [milestone]` | The sprint's state and what may be dispatched now, as JSON |
| `baton lock <acquire\|release>` | Stop two scheduled runs overlapping |

## Rules the gate enforces in a Baton repository

- Comments posted by agents must start with `<!-- baton:<role>:<kind> -->`.
- Comment and issue text must be literal: inline, an inline heredoc, or a file written in an
  earlier step. Text from stdin, a variable or command substitution is refused.
- `gh pr merge` is refused; use `baton merge-pr <number>`.
- Git hooks cannot be skipped (`--no-verify`, `core.hooksPath`).
- A pull request that touches a protected path needs a maintainer comment
  `/baton approve <commit sha>` (at least 7 characters of the sha), naming the head commit or an
  earlier one with the same changes to the protected paths.
- Empty bodies are refused, so a failed edit cannot blank an issue description.

The gate guards against mistakes. It is not a sandbox: it only sees `gh` and `git` commands.

## Tests

    PYTHONPATH=lib python3 -m unittest discover -s tests -v
