# Baton

A Claude Code plugin that runs software sprints on a forge repository: an orchestrator agent
dispatches developer agents in parallel, has a reviewer agent review each pull request, merges
the approved ones, and writes a retrospective. The forge holds all state, so any session can resume a sprint.

The workflow is adapted from the GitLab handbook's
[Engineering Workflow](https://handbook.gitlab.com/handbook/engineering/workflow/) and
[Product Development Flow](https://handbook.gitlab.com/handbook/product-development/how-we-work/product-development-flow/).

The review loop is adapted from the [superpowers](https://github.com/obra/superpowers) plugin by
Jesse Vincent (MIT): its code-review skills, plans of small tasks from its `writing-plans` skill,
and a fresh developer per task from its plan-execution skills. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

- Original design and plans (kept as history): [docs/superpowers/](docs/superpowers/)

## Status

Baton is young and changes quickly; it is developed in its own sprints. It has run sprints on
several real repositories, all on GitHub. See the latest version in
[.claude-plugin/plugin.json](.claude-plugin/plugin.json).

## Install

Requirements: [Claude Code](https://docs.claude.com/en/docs/claude-code), `git`, the
[GitHub CLI](https://cli.github.com/) (`gh`) signed in to an account that can push to your
repository, and Python 3 (CI tests 3.12). Only GitHub is supported as the forge for now.

    claude plugin marketplace add barretstorck/baton
    claude plugin install baton@baton

Then, in Claude Code inside your repository, run `/baton:init` to write `.baton/brief.md`, create
the labels and install the git hooks, and `/baton:sprint-plan` to plan a first sprint.

## Safety

Baton's agents act with **your** `gh` and `git` credentials: they push branches, comment, label,
and merge pull requests. The publish gate and the git hooks catch mistakes (private details in
published text, state changes without a marker comment), but they are not a sandbox; see
[Rules the gate enforces](#rules-the-gate-enforces-in-a-baton-repository) and
[SECURITY.md](SECURITY.md). Only issues written or marked ready by the maintainer enter a sprint,
and only the maintainer's comments count as answers. Try it on a repository you can afford to
experiment on first.

## Plans of small tasks

Every tool call sends an agent's whole conversation to the model again, so an agent that works a
whole issue grows large and expensive: in the second sprint, single developers reached 300K to
680K tokens and two of them used a 5-hour quota window in about an hour. So:

- `refine` writes a plan into the issue body (except for spikes) under `## Developer Plan`: one
  checkbox line per task (`- [ ] Task <n>: <title>`) with the brief in a `<details>` block, so the
  issue's progress bar follows the tasks. Acceptance criteria are plain lists. The plan holds
  numbered tasks, each a short brief with the files, the exact signatures it uses and provides,
  the lines to port from, and the check that proves it done. The body is limited to 65536
  characters; a longer one is refused and the issue must be split.
- The sprint gives **each task to a fresh developer**, one after another on the issue's branch.
  No developer's conversation grows large. A developer ticks its task in the issue body
  (`baton task done issue`) and pushes its code; the orchestrator confirms the tick.
- `merge-pr` merges the pull request; the branch holds no plan file. Sprint retrospectives
  go to `<docs.dir>/retros/`. Older issues: a plan on `refs/plans/<N>`, on a branch or in a legacy
  `## Plan` section is moved into the body by `baton plan migrate`.
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

## Custom refs: spike prototypes

Baton keeps some work in custom refs, which no branch or pull request holds. A spike that writes prototype code keeps it in the custom ref `refs/spikes/<N>`, where `<N>` is the
spike's issue number. No pull request and no branch is left behind; the findings comment ends with
a `Prototype:` link to the commit.

- Fetch one with `git fetch origin refs/spikes/<N>`. A normal clone does not fetch these refs, and
  `git push --mirror` from such a clone deletes them on the remote.
- GitHub rulesets cover only branches and tags, so anyone with write access can delete a spike ref.
- **Before making a repository public**, list the refs with `git ls-remote origin 'refs/spikes/*'`
  then review them or delete them with
  `git push origin --delete refs/spikes/<N>`. A deleted ref breaks its
  findings link, but the findings text stays. Deleting a ref does not erase the commits:
  GitHub can still serve them by SHA from cached views, forks and old pull request refs
  (`refs/pull/*`, including earlier spike drafts) until GitHub Support runs a garbage collection.

## Skills

| Skill | Purpose |
|---|---|
| `/baton:init` | Set a repository up: brief, labels, private files, git hooks |
| `/baton:refine <issue>` | Bring an issue up to the definition of ready, including its plan |
| `/baton:sprint-plan` | Plan a sprint with the maintainer and create its milestone |
| `/baton:sprint` | Work a sprint: dispatch developers and reviewers, collect answers, merge, retrospective |
| `/baton:develop` | Do one task: a plan task or a review task (used by the `baton-developer` subagent) |
| `/baton:research <issue>` | Do one spike and post its findings (used by the `baton-researcher` subagent) |
| `/baton:review <pr>` | Review one pull request and post a verdict (used by the `baton-reviewer` subagent) |

Each of the five roles (triager, refiner, researcher, developer, reviewer) has its own `model`
and `effort` in the brief. The defaults are Haiku for the triager, Opus for the refiner,
researcher and reviewer, and Sonnet for the developer, all at medium effort. The allowed models
are `opus`, `sonnet`, `fable` and `haiku` (efforts: `low`, `medium`, `high`, `xhigh` or `max`). Medium is the default because a
developer's thinking is about half of its context and every call re-reads that context, so
thinking is the largest single cost of a sprint. The researcher has web access; the developer has no web tools. For bulk refinement, dispatch
`baton-refiner` agents with at most about three issues each.
The refiner runs in its own worktree, where it can build the project and run quick experiments; for a
`type:bug` issue it reproduces the bug and writes a `## Diagnosis` section into the description.

The `follow_ups` setting says where each follow-up goes, by severity (`critical`, `important`,
`minor`). The placements are `deliverable`, `stretch`, `goal` (join as `stretch` only when it
serves the sprint goal, else backlog), `backlog` (`status:refinement`, outside the sprint) and
`skip`. The defaults are `critical: deliverable`, `important: goal`, `minor: skip`.
`follow_ups.scheduled` sets the same for scheduled runs (defaults `critical: deliverable`,
`important: backlog`); a severity missing there uses the interactive value. With no open sprint
everything but `skip` goes to the backlog, and a rule in the milestone description overrides the
brief for that sprint.

Typical use:

    /baton:sprint Pick issues that can be completed within 1 day. Use up to 4 parallel subagents.

Unattended runs are described in [docs/scheduling.md](docs/scheduling.md).

## Commands

| Command | Purpose |
|---|---|
| `baton config` | Print the resolved settings from `.baton/brief.md` |
| `baton labels [--create]` | Check or create the labels Baton uses |
| `baton set-status <issue> <status:name>` | Switch an issue's status label |
| `baton install-hooks` | Install the `pre-commit` and `pre-push` privacy hooks. The hooks record the plugin version's `baton` and fall back to `baton` on `PATH`; when a plugin update removes that version, the gate refuses commits and pushes until you run `baton install-hooks` again |
| `baton verify <pr> <worktree>` | Run the brief's `verify.command` on the pull request's head; prints the result, the log path and the last 40 lines |
| `baton sandbox run <command...>` | Run a command through the local brief's `## Sandbox` template in a fresh checkout of the default branch; prints the exit status, log path and last 40 lines; an error when no sandbox is configured |
| `baton sandbox check` | Probe the sandbox for network access: prints `none`, `isolated`, `not-isolated` or `probe-failed`; exit 0 for `none` and `isolated` |
| `baton merge-pr <number>` | Check the review, CI and the rest, then merge |
| `baton comment <issue\|pr> <number> <role> <kind> <file \| ->` | Scan, stamp and post an agent comment |
| `baton issue create\|edit ...` | Create an issue, or edit its title, description, labels or milestone; scans the text first |
| `baton pr create\|edit\|ready ...` | Open a (draft) pull request, edit it, or mark it ready (`--undo` back to draft) |
| `baton milestone create\|edit ...` | Create a milestone, or edit its title, description, due date or state |
| `baton close <issue\|pr> <number> <role> <reason-file \| ->` | Post a `baton:<role>:closed` marker comment with the reason, then close; raw `gh ... close` is refused |
| `baton reopen <issue\|pr> <number> <role> <reason-file \| ->` | Post a `baton:<role>:reopened` marker comment with the reason, then reopen; raw `gh ... reopen` is refused |
| `baton review state <pr>` | The pull request's review state at its head commit |
| `baton review post <pr> <approve\|changes\|blocked\|replan> <sha> <file \| ->` | Post a review of that commit |
| `baton answers <issue>` | The latest agent question and the maintainer's replies |
| `baton open-questions` | List open issues waiting on the maintainer (unanswered question, `status:blocked` or `needs-maintainer`) as JSON, most pressing first |
| `baton ready-check <issue>` | The mechanical half of the definition of ready |
| `baton tasks <issue\|pr> <number>` | An issue's plan tasks, or the latest review's tasks, as JSON |
| `baton task done <issue\|pr> <number> <task>` | Tick one task: in the issue body's plan for an issue (the developer), in the review for a pull request (the orchestrator) |
| `baton plan replace <issue> <from-task> <file \| -> [--drop]` | Replace a plan's open tasks from that number on |
| `baton plan insert <issue> <before-task> <file \| ->` | Insert new tasks before that task number; later open tasks are renumbered (and their `task <n>` references shifted) |
| `baton plan against <issue> <sha>` | Set the `Written against:` commit of the body plan (the refiner, after a replan) |
| `baton plan set <issue> <file \| ->` | Write the `## Developer Plan` section into the issue body (the refiner); `plan commit`, `link` and `pin` are deprecated no-ops |
| `baton plan migrate <issue>` | Convert an older plan into `## Developer Plan` |
| `baton usage [--repo <owner/repo>] [--since <time>] [--json]` | Quota use per issue and role, from local transcripts |
| `baton sprint-status [milestone]` | The sprint's state and what may be dispatched now, as JSON; `waiting_links` gives each `waiting_on_maintainer` issue's question comment URL and `progress.<N>.next_task_url` links the next plan task; `progress.<N>.stale`, `branch_behind` and `merge_first` report plans naming missing paths (a ready issue with two or more is held for re-refinement) and branches far behind the default branch |
| `baton clean` | Remove leftover agent worktrees (kept: an open issue's branch, a lock held by a live process, unmerged detached work; uncommitted files are removed with the worktree) and the log folders of closed or merged pull requests; exit 1 if a worktree could not be removed |
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

## Maintainers

`maintainers` in `.baton/brief.md` lists the GitHub logins whose comments, reviews, approvals and
issues Baton trusts (default `[]`, meaning the repository owner alone). Agents post under a
maintainer's login, so every login on the list can answer questions, approve a merge with
`/baton approve` and write issues that are ready to develop. `baton config` prints the resolved
list. Log folders and API paths still use the repository owner.

## Forbidden diff patterns

`forbid_added` in `.baton/brief.md` maps a path glob to a regular expression, for example
`"pyproject.toml": "per-file-ignores"` (default `{}`). The `pre-commit` and `pre-push` hooks refuse a
commit or push that adds a line matching the expression in a file matching the glob, and name the
line; `baton merge-pr` repeats the check over the whole pull request diff. A plan that needs such a
line says so with `Allows: forbid_added "<glob>"` (the glob as written in `forbid_added`); only
then is that rule skipped. Inline `# noqa` comments are not judged.

## Work mix

`work_mix` in `.baton/brief.md` is an optional target share of work by type, for example
`{feature: 60, maintenance: 30, bug: 10}` (percentages that add up to 100; default `{}`, no target).
`/baton:sprint-plan` shows the proposed mix next to it and the difference in percentage points. It is
information only and is never enforced.

## Rules the gate enforces in a Baton repository

- Comments posted by agents must start with `<!-- baton:<role>:<kind> -->`.
- Raw forge writes are refused from any directory, and the message names the `baton` command to
  use instead (`baton comment`, `baton issue`, `baton pr`, `baton milestone`, `baton review post`,
  `baton close|reopen`, `baton merge-pr`, `baton labels`, `baton set-status`). `gh` is allowed
  only for reads (`view`, `list`, `checks`, `diff`, ...); any other `gh api` call is refused
  unless it is a plain GET.
- A shell or evaluator (`bash -c`, `xargs`, `eval`, `python -c`) that mentions `gh` or `git` is
  refused, because the gate cannot tell whether it writes to the forge.
- A git publish (`commit`, `push`, `tag`) must be a plain command in the repository's own
  directory: no `cd`, no `-C`, no `-c` option. The brief is found from the working directory
  only; without one the command is refused.
- Git hooks cannot be skipped (`--no-verify`, `core.hooksPath`).
- A pull request that touches a protected path needs a maintainer comment
  `/baton approve <commit sha>` (at least 7 characters of the sha), naming the head commit or an
  earlier one with the same changes to the protected paths.
- The `baton` write commands scan their text and refuse empty bodies, so a failed edit cannot blank an issue description.
- The git hooks do not report `private-ip` in Python dependency files (matched by basename:
  `uv.lock`, `poetry.lock`, `pdm.lock`, `Pipfile.lock`, `requirements*.txt`, `requirements*.in`,
  `constraints*.txt`, `pylock*.toml`), where four-part package versions look like addresses. Email,
  host-path, internal-domain and private terms are still checked in those files.

The gate guards against mistakes. It is not a sandbox: it only sees `gh` and `git` commands, and cannot see through every wrapper.

## Tests

    PYTHONPATH=lib python3 -m unittest discover -s tests -v

## Contributing and licence

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md). Baton is released under the
[MIT License](LICENSE); material adapted from other projects is listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
