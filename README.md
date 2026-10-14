# Baton

A Claude Code plugin that runs software sprints on a forge repository: an orchestrator agent
dispatches developer agents in parallel, has a reviewer agent review each pull request, merges
the approved ones, and writes a retrospective. The forge holds all state, so any session can resume a sprint.

The workflow is adapted from the GitLab handbook's
[Engineering Workflow](https://handbook.gitlab.com/handbook/engineering/workflow/) and
[Product Development Flow](https://handbook.gitlab.com/handbook/product-development/how-we-work/product-development-flow/).

The review loop is adapted from the [superpowers](https://github.com/obra/superpowers) plugin's
code-review skills, and plans of small tasks from its `writing-plans` skill.

- Design: [docs/superpowers/specs/2026-10-01-baton-design.md](docs/superpowers/specs/2026-10-01-baton-design.md)
- Plans: [docs/superpowers/plans/](docs/superpowers/plans/)

## Status

Two sprints have run on a real repository. Version 0.2.0 added the designated reviewer;
version 0.3.0 works each issue as a plan of small tasks, to keep a sprint inside a subscription's
quota.

## Plans of small tasks

Every tool call sends an agent's whole conversation to the model again, so an agent that works a
whole issue grows large and expensive: in the second sprint, single developers reached 300K to
680K tokens and two of them used a 5-hour quota window in about an hour. So:

- `refine` writes a plan file for each issue (except spikes) to `<docs.dir>/plans/<N>-<slug>.md`
  (`docs.dir` in `.baton/brief.md`, default `docs/baton`) on the issue branch, and links it from
  the issue's first line (`Plan:`). The issue stays short for a person to read. The plan holds
  numbered tasks, each a short brief with the files, the exact signatures it uses and provides,
  the lines to port from, and the check that proves it done.
- The sprint gives **each task to a fresh developer**, one after another on the issue's branch.
  No developer's conversation grows large. A developer ticks its task in the plan file and
  commits the tick with its code; the orchestrator confirms the tick in the pushed head.
- `merge-pr` removes the plan file from the branch just before the merge, after pinning the
  issue's link to the last commit that has it, so the default branch collects no plan files and
  the link still opens the final plan. Sprint retrospectives go to `<docs.dir>/retros/`. An older
  issue with a `## Plan` in its body is still read, and `baton plan migrate` moves it to a file.
- The reviewer runs **once**, when every task is ticked. What it wants fixed is a `## Tasks`
  checklist in its review, worked the same way, one developer per task.
- `baton usage` measures the result per issue and role from the local transcripts, and the
  retrospective includes its table.

## How a pull request gets merged

1. Developer agents do the issue's plan, one task each, pushing to the branch; the pull request
   stays a draft.
2. The orchestrator dispatches a reviewer agent. It reviews the head commit against the issue
   and the brief and posts one review: `approve`, or `changes` with a `## Tasks` checklist of
   what to fix in this pull request, and follow-up issues for the rest.
3. On `changes`, one developer per task fixes it or declines it with a reason, the orchestrator
   posts the reply, and the reviewer looks again, up to `review.max_rounds` rounds before the
   maintainer is asked. An approval is refused while any task is open.
4. Once approved, the orchestrator marks the pull request ready, so CI runs once, and
   `baton merge-pr` merges it after checking the approval still covers the head commit.

## Skills

| Skill | Purpose |
|---|---|
| `/baton:init` | Set a repository up: brief, labels, private files, git hooks |
| `/baton:refine <issue>` | Bring an issue up to the definition of ready, including its plan |
| `/baton:sprint-plan` | Plan a sprint with the maintainer and create its milestone |
| `/baton:sprint` | Work a sprint: dispatch developers and reviewers, collect answers, merge, retrospective |
| `/baton:develop` | Do one task: a plan task, a review task or a spike (used by the `baton-developer` subagent) |
| `/baton:review <pr>` | Review one pull request and post a verdict (used by the `baton-reviewer` subagent) |

The developer, reviewer and refiner subagents run on Sonnet at medium effort; `developer.model`
and `reviewer.model` in the brief change the model, and `developer.effort` (`low`, `medium`,
`high`, `xhigh` or `max`) overrides the developer's effort. Medium is the default because a
developer's thinking is about half of its context and every call re-reads that context, so
thinking is the largest single cost of a sprint. For bulk refinement, dispatch
`baton-refiner` agents with at most about three issues each.

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
| `baton tasks <issue\|pr> <number>` | An issue's plan tasks, or the latest review's tasks, as JSON |
| `baton task done <issue\|pr> <number> <task>` | Tick one task: in the plan file of the current checkout for an issue (the developer commits it), in the review for a pull request (the orchestrator) |
| `baton plan replace <issue> <from-task> <file \| ->` | Replace a plan's open tasks from that number on; a linked plan is committed to its branch and pushed |
| `baton plan link <issue> <branch> <path>` / `--at <sha>` | Write the issue's `Plan:` link to the file on the branch, or pin it to a commit |
| `baton plan migrate <issue> <branch>` | Move a plan from the issue body to a plan file on a new branch |
| `baton usage [--repo <owner/repo>] [--since <time>] [--json]` | Quota use per issue and role, from local transcripts |
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
