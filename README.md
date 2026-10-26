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
version 0.4.0 works each issue as a plan of small tasks, to keep a sprint inside a subscription's
quota.

## Plans of small tasks

Every tool call sends an agent's whole conversation to the model again, so an agent that works a
whole issue grows large and expensive: in the second sprint, single developers reached 300K to
680K tokens and two of them used a 5-hour quota window in about an hour. So:

- `refine` writes a plan file for each issue (except spikes) to `<docs.dir>/plans/<N>-<slug>.md`
  (`docs.dir` in `.baton/brief.md`, default `docs/baton`) and keeps it in the custom ref
  `refs/plans/<N>`, not on the issue branch; the issue's first line (`Plan:`) links to the ref's
  commit history. The issue stays short for a person to read. The plan holds
  numbered tasks, each a short brief with the files, the exact signatures it uses and provides,
  the lines to port from, and the check that proves it done.
- The sprint gives **each task to a fresh developer**, one after another on the issue's branch.
  No developer's conversation grows large. A developer ticks its task on the plan ref and
  pushes the tick with its code in one atomic push; the orchestrator confirms the tick on the ref.
- `merge-pr` pins the issue's link to the plan's last commit and merges in the same pass, so the
  default branch collects no plan files and the link still opens the final plan. (A plan still
  on the branch from before refs is removed in a commit before the merge.) Sprint retrospectives
  go to `<docs.dir>/retros/`. An older issue with a `## Plan` in its body is still read, and
  `baton plan migrate` moves it to the ref.
- The reviewer runs when every task is ticked and the pull request is ready. What it wants fixed is a `## Tasks`
  checklist in its review, worked the same way, one developer per task.
- `baton usage` measures the result per issue and role from the local transcripts, and the
  retrospective includes its table.

## How a pull request gets merged

1. Developer agents do the issue's plan, one task each, pushing to the branch; the pull request
   stays a draft.
2. Once the last plan task is ticked, the orchestrator marks the pull request ready, so CI runs
   on the finished work, and dispatches a reviewer agent straight away; it does not wait for CI.
3. The reviewer starts the full verification and a wait for CI in the background, reads the code
   while they run, then waits once for both. It turns every failure (tests, CI, lint) into a fix
   task, and classifies each as caused by the change, flaky, failing on the default branch, or
   environmental. It posts one review: `approve`, or `changes` with a `## Tasks` checklist of
   what to fix in this pull request and follow-up issues for the rest, or `blocked` when the
   only failures are default-branch or environment ones, or `replan` when the plan itself is the
   problem: the issue goes back to refinement, the refiner revises the open tasks against the branch,
   developers continue on the same branch, and a replan counts as a review round. If the same tests fail in two reviews
   in a row, the pull request goes to the maintainer instead of another fix round.
   A `changes` review turns the pull request back into a draft while its fix tasks are worked,
   and it is marked ready again after the developer's reply, so CI runs once per round.
4. On `changes`, one developer per task fixes it or declines it with a reason, the orchestrator
   posts the reply, and the reviewer looks again, up to `review.max_rounds` rounds before the
   maintainer is asked. An approval is refused while any task is open.
5. Once approved, `baton merge-pr` merges it after checking CI and that the approval still
   covers the head commit.

## Custom refs: spike prototypes and plans

Baton keeps some work in custom refs, which no branch or pull request holds. Plans live in
`refs/plans/<N>`, with the same caveats as below. A spike that writes prototype code keeps it in the custom ref `refs/spikes/<N>`, where `<N>` is the
spike's issue number. No pull request and no branch is left behind; the findings comment ends with
a `Prototype:` link to the commit.

- Fetch one with `git fetch origin refs/spikes/<N>` (a plan: `git fetch origin refs/plans/<N>`). A normal clone does not fetch these refs, and
  `git push --mirror` from such a clone deletes them on the remote.
- GitHub rulesets cover only branches and tags, so anyone with write access can delete a spike or plan ref.
- **Before making a repository public**, list the refs with `git ls-remote origin 'refs/spikes/*'`
  and `git ls-remote origin 'refs/plans/*'`, then review them or delete them with
  `git push origin --delete refs/spikes/<N>` (or `refs/plans/<N>`). A deleted ref breaks its
  findings or `Plan:` link, but the findings text stays. Deleting a ref does not erase the commits:
  GitHub can still serve them by SHA from cached views, forks and old pull request refs
  (`refs/pull/*`, including earlier spike drafts) until GitHub Support runs a garbage collection.

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
The refiner runs in its own worktree, where it can build the project and run quick experiments; for a
`type:bug` issue it reproduces the bug and writes a `## Diagnosis` section into the description.

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
| `baton verify <pr> <worktree>` | Run the brief's `verify.command` on the pull request's head; prints the result, the log path and the last 40 lines |
| `baton merge-pr <number>` | Check the review, CI and the rest, then merge |
| `baton comment <issue\|pr> <number> <role> <kind> <file \| ->` | Scan, stamp and post an agent comment |
| `baton close <issue\|pr> <number> <role> <reason-file \| ->` | Post a `baton:<role>:closed` marker comment with the reason, then close; raw `gh ... close` is refused |
| `baton reopen <issue\|pr> <number> <role> <reason-file \| ->` | Post a `baton:<role>:reopened` marker comment with the reason, then reopen; raw `gh ... reopen` is refused |
| `baton review state <pr>` | The pull request's review state at its head commit |
| `baton review post <pr> <approve\|changes\|blocked\|replan> <sha> <file \| ->` | Post a review of that commit |
| `baton answers <issue>` | The latest agent question and the maintainer's replies |
| `baton open-questions` | List open issues waiting on the maintainer (unanswered question, `status:blocked` or `needs-maintainer`) as JSON, most pressing first |
| `baton ready-check <issue>` | The mechanical half of the definition of ready |
| `baton tasks <issue\|pr> <number>` | An issue's plan tasks, or the latest review's tasks, as JSON |
| `baton task done <issue\|pr> <number> <task>` | Tick one task: in the plan file of the current checkout for an issue (the developer commits it), in the review for a pull request (the orchestrator) |
| `baton plan replace <issue> <from-task> <file \| -> [--drop]` | Replace a plan's open tasks from that number on; a linked plan is committed to its branch and pushed |
| `baton plan insert <issue> <before-task> <file \| ->` | Insert new tasks before that task number; later open tasks are renumbered (and their `task <n>` references shifted) |
| `baton plan against <issue> <sha>` | Set the `Written against:` commit of a plan on `refs/plans/<N>` (the refiner, after a replan) |
| `baton plan link <issue> <branch> <path>` / `--at <sha>` | Write the issue's `Plan:` link to the file on the branch, or pin it to a commit |
| `baton plan migrate <issue> <branch>` | Move a plan from the issue body to a plan file on a new branch |
| `baton usage [--repo <owner/repo>] [--since <time>] [--json]` | Quota use per issue and role, from local transcripts |
| `baton sprint-status [milestone]` | The sprint's state and what may be dispatched now, as JSON; `waiting_links` gives each `waiting_on_maintainer` issue's question comment URL and `progress.<N>.next_task_url` links the next plan task; `progress.<N>.stale`, `branch_behind` and `merge_first` report plans naming missing paths (a ready issue with two or more is held for re-refinement) and branches far behind the default branch |
| `baton clean` | Remove leftover agent worktrees (kept: an open issue's branch, uncommitted changes, unmerged detached work) and the log folders of closed or merged pull requests; exit 1 if a worktree could not be removed |
| `baton lock <acquire\|release>` | Stop two scheduled runs overlapping |

## Full verification

The reviewer runs the repository's full suite with `baton verify`; developers run only each task's `Check:`.
Set `verify.command` (and `verify.timeout_minutes`) in `.baton/brief.md`. Local verification and CI are two
settings, which give four cases:

| `verify.command` | `merge.allow_no_ci` | What a merge needs |
| --- | --- | --- |
| set | false | a green local verification recorded in the review, and green CI |
| set | true | a green local verification recorded in the review; no CI exists |
| empty | false | green CI only |
| empty | true | neither (not recommended) |

Each run writes its log and a result file to `<BATON_HOME or ~/.baton>/<owner>-<repo>/logs/pr-<number>/`, outside
every checkout; `baton merge-pr` deletes the folder after a merge. The result goes into the review's marker
(`baton review post` refuses an approval unless it is `green`), and `baton pr-state` and `baton sprint-status`
show it as `verify`. With `review.required: false` no review carries the result, so `verify` reads `none`.
When an approved pull request's head moves only by merging the default branch in, the reviewer re-approves it briefly: it runs `baton verify` again and posts a new review for the new head.

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
- The git hooks do not report `private-ip` in Python dependency files (matched by basename:
  `uv.lock`, `poetry.lock`, `pdm.lock`, `Pipfile.lock`, `requirements*.txt`, `requirements*.in`,
  `constraints*.txt`, `pylock*.toml`), where four-part package versions look like addresses. Email,
  host-path, internal-domain and private terms are still checked in those files.

The gate guards against mistakes. It is not a sandbox: it only sees `gh` and `git` commands.

## Tests

    PYTHONPATH=lib python3 -m unittest discover -s tests -v
