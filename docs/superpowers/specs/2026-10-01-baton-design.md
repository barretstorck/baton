# Baton: design

**Status:** approved (revision 4: plans of small tasks and quota use, 2026-10-09; revision 3: a
designated code reviewer, 2026-10-08)
**Date:** 2026-10-01

Baton is a Claude Code plugin that runs software sprints on a forge repository. A manager agent
(the orchestrator) works a planned sprint: it dispatches developer agents in parallel, checks and
merges their pull requests, files follow-ups, and writes a retrospective. The forge is the only
place state and conversation live, so any new Claude Code session can pick a sprint up where the
last one stopped.

## Credit

Baton's workflow is adapted from the GitLab handbook:

- [Engineering Workflow](https://handbook.gitlab.com/handbook/engineering/workflow/)
- [Product Development Flow](https://handbook.gitlab.com/handbook/product-development/how-we-work/product-development-flow/)

Sections below link to the handbook wherever Baton follows it. Skill files carry the same links.

## 1. Goals and non-goals

**Goals**

- One command works a sprint:
  `/baton:sprint Pick issues in <repo> that can be completed within 1 day. Use up to 4 parallel subagents.`
- The same skill runs interactively or from a schedule.
- A sprint fits the maintainer's subscription quota. Work is cut into small tasks, each done by
  a fresh agent, so no agent's conversation grows large (section 7b).
- A sprint can be any length. The orchestrator keeps nothing in its own context that it cannot
  rebuild from the forge, so the session can be cleared or replaced at any time.
- All questions and answers are issue comments. The maintainer may answer on the issue or in the
  Claude Code session; session answers are mirrored to the issue.
- Nothing personal is ever published to the forge, and text from untrusted authors is never
  treated as instruction.
- Each retrospective can improve the next sprint.

**Non-goals**

- Deciding *what* to build. The maintainer is the product manager and stakeholder. GitLab's
  validation track has no counterpart in Baton.
- Deployment and release management.
- Weights, story points and capacity estimates. They may be added later.
- GitLab and Forgejo support in the first version (section 11).

## 2. Components

| Component | Kind | Purpose |
|---|---|---|
| `init` | skill | Sets a repository up for Baton: writes `.baton/brief.md`, creates or maps labels. |
| `refine` | skill | Brings an issue up to the definition of ready, including a plan of small tasks, or says what is missing. |
| `sprint-plan` | skill | Builds a sprint: asks for the goal, length and limits, then compiles ready issues into a milestone. |
| `sprint` | skill | Orchestrator. Works a planned milestone: dispatches, checks, merges, files follow-ups, runs the retrospective. Never writes product code. Calls `sprint-plan` when given no planned sprint. |
| `develop` | skill | Developer. Does one task: one task of an issue's plan, one task of a review, or a whole spike. Never merges. |
| `review` | skill | Reviewer. Reviews one pull request against its issue and the project's rules, and decides for each problem whether it is fixed in the same pull request (as a task) or becomes a follow-up issue. Read-only on the code. |
| `baton-developer` | subagent definition | Preloads `develop`, runs in an isolated git worktree with a restricted tool list. Uses **Claude Sonnet** at medium effort by default; the repo brief may override the model. |
| `baton-refiner` | subagent definition | Preloads `refine`, for refining issues in bulk. Uses **Claude Sonnet** at medium effort. |
| `baton-reviewer` | subagent definition | Preloads `review`, runs in an isolated git worktree, never edits the branch and never starts subagents. Uses **Claude Sonnet** at medium effort by default; the repo brief may override the model. |
| publish gate | hooks | Blocks personal data before anything is published; stamps agent comments. |
| `merge-pr` | script | The only way a pull request is merged. |
| `tasks`, `task done`, `plan replace` | script | Read and tick the task checklists in issues and reviews; replace a plan's open tasks. Agents never edit these checklists by hand. |
| `usage` | script | Reports quota use per issue and role from the local agent transcripts (section 7b). |
| `references/github.md` | reference | Maps each neutral step ("post a question") to `gh` commands. |

The skills are generic. Everything specific to a project comes from two briefs:

- **Repo brief** `.baton/brief.md`, committed in the target repository. Its YAML front matter
  holds settings (section 3); its body holds prose rules for agents: test command, definition of
  done, conventions.
- **Local brief** `~/.baton/<owner>-<repo>/brief.local.md`, never committed: hosts, paths,
  credential locations and anything else private that agents need to do the work.

## 3. Repo brief settings

```yaml
# .baton/brief.md front matter (defaults shown)
developer:
  model: sonnet
  max_parallel: 2
reviewer:
  model: sonnet
review:
  required: true              # false: merge-pr does not ask for a reviewer's approval
  max_rounds: 2               # review rounds before a disagreement goes to the maintainer
refine:
  may_mark_ready: true        # false: refine proposes, the maintainer applies the label
labels:
  auto_create: true           # false: a missing label stops the run with needs-maintainer
  map:                        # Baton's name -> the label this repository uses
    status:refinement: "status:refinement"
    status:ready: "status:ready"
    status:in-dev: "status:in-dev"
    status:blocked: "status:blocked"
    status:in-review: "status:in-review"
    status:verification: "status:verification"
    status:complete: "status:complete"
    type:spike: "type:spike"
    deliverable: "deliverable"
    stretch: "stretch"
    broken-default-branch: "broken-default-branch"
    needs-maintainer: "needs-maintainer"
protected_paths: [".github/", ".baton/", ".claude/"]
fixtures_paths: []            # the only places new image files may be added
budgets: {}                   # optional checks, e.g. CI minutes, disk
```

**Labels.** Skills always refer to Baton's own label names and translate through `labels.map`, so
a repository can keep its existing labels (for example map `status:in-dev` to `In Progress`).
`init` checks every mapped label and creates the missing ones. At run time a missing label is
created when `auto_create` is true; otherwise the run stops and reports which label is missing.

## 4. Issue lifecycle

Status is one label at a time. Setting a status removes the previous one. The statuses follow
GitLab's [build track](https://handbook.gitlab.com/handbook/product-development/how-we-work/product-development-flow/#build-track)
and [workflow labels](https://handbook.gitlab.com/handbook/engineering/workflow/#updating-workflow-labels-throughout-development).

| Status | Set by | Meaning |
|---|---|---|
| `status:refinement` | maintainer, orchestrator | Needs work before it is ready |
| `status:ready` | `refine` or maintainer | Meets the definition of ready; may be dispatched |
| `status:in-dev` | developer | Claimed; branch and draft pull request exist; tasks are being worked |
| `status:blocked` | developer | Waiting on an answer or a dependency |
| `status:in-review` | orchestrator | Every plan task done; in code review, then CI, then merge |
| `status:verification` | orchestrator | Merged; waiting for the check on the default branch |
| `status:complete` | orchestrator | Verified; issue closed |

**Definition of ready** (checked by `refine`):

- A specific title, and context that says why the work matters.
- Acceptance criteria as checkboxes that can be verified objectively.
- An out-of-scope section.
- Pointers to the likely files and the existing pattern to follow.
- The exact verification command, or "repo default".
- One outcome only; anything larger is split.
- A `Blocked by: #N` line for each dependency, and an `Expected files:` line used for overlap checks.
- A type.
- A plan of small tasks (below), except for spikes.

**The issue description is the single source of truth**
([GitLab](https://handbook.gitlab.com/handbook/product-development/how-we-work/product-development-flow/#issue-descriptions-as-the-single-source-of-truth-ssot)).
When a question is answered, the answer is folded into the description under a `Decisions`
heading, and each entry ends with a markdown link to the comment that gave it, for example:

```markdown
## Decisions
- Imperceptible output differences are acceptable. ([answer](https://github.com/<o>/<r>/issues/12#issuecomment-123))
```

### The plan

Adapted from the superpowers plugin's `writing-plans` skill. `refine` adds a `## Plan` section to
every issue that is not a spike. Each task is a short brief that a fresh developer can do without
searching the code:

```markdown
## Plan
Written against: <full commit sha of the default branch>

- [ ] **1. Detect faded prints**
  - Files: `src/defade/detect.rs` (new), `src/defade/mod.rs`
  - Uses: `Image::histogram(&self) -> Histogram` (`src/image.rs:40`)
  - Provides: `pub fn detect(img: &Image) -> Option<Fade>`
  - Port from: `reference/defade.py:120-188`
  - Check: new tests `detects_faded_print`, `ignores_neutral_print`; `cargo test defade::detect`
- [ ] **2. ...**
```

- A task is one unit of work with its own check. The check is what proves that task done: a
  named test or a filtered subset, one build, or nothing (for example a documentation task). The
  full suite runs only where the plan says so, usually in a final task taken from
  `## Verification`; CI covers the rest.
- Tasks run in order and may rely only on what earlier tasks provide.
- `Uses` and `Provides` give exact signatures, `Port from` and `Files` give exact paths and line
  ranges. The plan gives no code; the developer writes it.
- There is no limit on the number of tasks. An issue is split for having more than one outcome,
  not for having many tasks.
- `Written against:` names the default-branch commit the plan was written from, so a developer can
  tell what changed since.
- Only the scripts tick boxes (`baton task done`) and replace open tasks (`baton plan replace`,
  section 6). The ticked boxes, together with commits named `Task <n>:`, are the issue's progress.

### When refinement happens

- **Before a sprint.** The maintainer runs `/baton:refine` on backlog issues, or `sprint-plan`
  runs it on candidates that are close to ready. This is the main use.
- **During a sprint**, in three cases:
  1. a follow-up issue filed mid-sprint that the orchestrator wants to pull in;
  2. an issue a developer returns as under-specified (it goes back to `status:refinement`);
  3. the broken-default-branch issue (section 6), which `refine` diagnoses before dispatch.
- **After a sprint.** Nothing. Issues raised by the retrospective enter `status:refinement` and
  are refined before the next sprint.

`refine` runs as a subagent of the orchestrator during a sprint and does not take a developer slot.
Bulk refinement dispatches `baton-refiner` subagents with at most about three issues each, since
writing a plan means reading code and every issue grows the refiner's conversation.

### Spikes

Issues of `type:spike` deliver a findings comment and proposed follow-up issues. There is no pull
request. A spike that writes prototype code keeps it on `issue-<N>-<slug>` while it runs, then
pushes the head to `refs/spikes/<N>` (named by issue number, never by SHA), deletes the remote
branch and ends its findings comment with a `Prototype:` line linking the tree at the full commit
SHA, with the `git fetch origin refs/spikes/<N>` command. A re-run spike overwrites the ref with
`--force-with-lease=refs/spikes/<N>:<old sha>`, the old sha taken from `git ls-remote`. No branch is
left behind. The spike is complete when its findings are posted. Promoted work starts on a fresh
branch and uses the prototype only as a reference. The retrospective lists the spike prototypes
for reference; the maintainer has nothing to close.

## 5. Sprint planning (`sprint-plan`)

- **A milestone is the sprint.** Its description holds the goal, the parallel limit and any other
  settings, so a later session can read them back. Its due date is the planned end.
- `sprint-plan` asks the maintainer for whatever the prompt did not supply: the sprint goal, how
  long it should run, and the parallel limit. It then proposes issues from the `status:ready`
  queue that serve the goal, in priority and dependency order, marked `deliverable` or `stretch`
  ([GitLab's Deliverable label](https://handbook.gitlab.com/handbook/product-development/how-we-work/product-development-flow/#build-phase-1-plan)).
  Without weights, how many issues fit is the maintainer's call.
- The milestone is created only after the maintainer approves the proposal.
- `/baton:sprint` given an existing milestone works it directly. Given none, and with no open
  sprint, it calls `sprint-plan`.
- **Scheduled runs never start a sprint.** With no open sprint they post the proposed plan as an
  issue labelled `needs-maintainer` and exit.
- No project board is required.

## 6. Sprint execution (`sprint`)

### Events, passes and runs

The sprint moves forward on **events**, all of which are visible on the forge:

- a developer finishes a task (`DONE`, `REPLAN`, `FIXED` or `DECLINED`);
- every plan task of an issue is ticked;
- a reviewer posts a review on a pull request;
- a developer asks a question (`status:blocked` plus a question comment);
- the maintainer answers on an issue;
- CI concludes on a pull request or on the default branch;
- a label or milestone changes.

A **pass** is the orchestrator reading the current forge state and acting on every event it finds.
It holds no memory between passes, which is what allows a new session to resume a sprint.

A **run** is one session of the orchestrator: it repeats passes until nothing is in flight and
nothing can be dispatched, then exits.

- *Interactive run:* a pass happens whenever a developer subagent returns or the maintainer
  speaks, and the run stays open.
- *Scheduled run:* the schedule is what notices events that happened while no session was open,
  such as an answer the maintainer left overnight. The run may dispatch developers and waits for
  them, because a subagent cannot outlive its session. A lock makes a new scheduled run exit at
  once if another run is still active.

### One pass

1. **Reconcile.** Read the sprint milestone, its issues, status labels, open pull requests and
   new comments.
2. **Default branch check** (see below). If red, only the repair issue proceeds.
3. **Check budgets** named in the repo brief. Over budget means no dispatch.
4. **Collect answers.** For each `status:blocked` issue with a maintainer comment newer than the
   agent's question, fold the answer into `Decisions` with its link and set `status:ready`.
5. **Review, then merge finished work.** For each `status:in-review` pull request, act on its
   review state (section 7a):
   - no review yet, or the last review's tasks are all ticked and answered: dispatch a reviewer;
   - changes asked for: back to `status:in-dev` and work the review's tasks (step 8);
   - every review task ticked: post the `developer` `reply` comment on the developers' behalf, listing each task as fixed (with its
     commit) or declined (with its reason);
   - rounds used up and still changes: `needs-maintainer`, with the disagreement summarised;
   - approved: mark the pull request ready for review (so CI runs once), wait for CI, then
     `merge-pr`, which checks:
     - a reviewer's approval of the change at the head commit;
     - CI concluded SUCCESS on the head commit;
     - mergeable against the default branch;
     - closing reference correct, and no stray closing keywords;
     - no protected path touched without maintainer approval (section 9).

   `merge-pr` sets `status:verification`. Merges are one at a time; remaining pull requests are
   updated afterwards. Merging the default branch into a pull request does not void its approvals
   when the pull request's own change is unchanged by it.
6. **Verify.** When the default branch is green after a merge, set `status:complete`. The issue
   itself was already closed by the merge.
7. **File follow-ups.** Developer suggestions become new issues in `status:refinement`. They join
   the sprint only if they serve the sprint goal.
8. **Dispatch.** An issue in development holds one slot and has one developer at a time, working
   its tasks in order: the lowest open plan task ("Do task n of issue N"), or, during a review
   round, the lowest open review task ("Do review task r.n of pull request P"). On the results:
   - `DONE <sha>` or `FIXED <sha>`: check the commit is the branch tip on the forge, tick the
     task, dispatch the next one. When the last plan task is ticked, set `status:in-review`.
   - `DECLINED <reason>`: tick the task and keep the reason for the reply.
   - `REPLAN`: the developer proposes new task briefs and says why. If they stay within the
     acceptance criteria, `baton plan replace <N> <from> <file>` replaces the open tasks from
     that number on (ticked tasks are kept), a `decision` comment records what changed and why,
     and work continues. If they would change what the issue promises, add `needs-maintainer`
     and wait.
   - A task commit pushed without its box ticked (an interrupted run) is ticked once its check
     is confirmed; an unfinished task restarts with a fresh developer.

   Then fill free slots, up to the parallel limit (the milestone's `Parallel:` line, otherwise
   `developer.max_parallel`), with `status:ready` deliverables in priority order, skipping any
   whose `Blocked by` is open or whose `Expected files` overlap a running issue.
9. **Close the sprint** when every issue is complete or waiting on the maintainer: write the
   retrospective, move unfinished issues out with `missed:<sprint>`
   ([GitLab's milestone cleanup](https://handbook.gitlab.com/handbook/engineering/workflow/#milestone-cleanup)),
   and close the milestone.

### Red default branch

Following GitLab's [broken master](https://handbook.gitlab.com/handbook/engineering/workflow/#broken-master)
rule, a failing default branch takes priority over everything else.

1. The orchestrator opens an issue documenting the failure (failing job, first bad commit, log
   excerpt), labels it `broken-default-branch` and `deliverable`, and adds it to the sprint at the
   top of the order. If one is already open it is reused.
2. `refine` diagnoses it and writes the acceptance criteria. Reverting the offending merge is the
   preferred fix when it is available
   ([GitLab](https://handbook.gitlab.com/handbook/engineering/workflow/#reverting-a-merge-request)).
3. A developer is dispatched to it immediately.
4. Until the default branch is green: no other issue is dispatched and no other pull request is
   merged. Developers already running may continue, but their pull requests wait.
5. Once green, the repair issue is completed and the sprint resumes.

### Retrospective

A file in the target repository, `docs/retros/<sprint>.md`, opened as a pull request: what was
delivered, what went right, what went wrong with cost and root cause, the quota use table from
`baton usage` (section 7b), and how to improve. Lessons
that would change Baton itself are opened as a pull request to the Baton repository.

## 7. Developer: one task (`develop`)

Follows GitLab's [engineering basics](https://handbook.gitlab.com/handbook/engineering/workflow/#basics).
Each developer is a fresh agent that does one task and stops, so its conversation stays small.

1. Read only what the task needs: the issue's Context, Acceptance criteria, Out of scope and
   Decisions, its own task brief, both briefs and the project's agent rules. The description is
   the spec; comments from anyone but the maintainer are data. Earlier tasks are read through
   `git log --oneline` and `git diff --stat`, not their full diffs.
2. Check the plan is still current: `git diff --stat <written-against> origin/<default> --` the
   task's files. When something it relies on changed and the brief no longer fits, return
   `REPLAN`.
3. Get the branch.
   - *Task 1:* create `issue-<N>-<slug>` from the default branch, open a draft pull request
     (`Closes #N` and a summary of the plan), and set `status:in-dev`.
   - *Later tasks and review tasks:* fetch and check out the branch tip detached, so a worktree
     left by an earlier task cannot block it.
4. Do the task with tests, inside its brief. Out-of-scope ideas go in the report.
5. Run the task's check, and nothing more unless the brief says so.
6. Commit as `Task <n>: <title>` (or `Review <r.n>: <title>`) and push with
   `git push origin HEAD:<branch>`. The push only fast-forwards; if it is refused, return
   `BLOCKED`.
7. Return one of:
   - `DONE <sha>` (a plan task) or `FIXED <sha>` (a review task);
   - `DECLINED <reason>`, for a review task that is wrong or belongs in a follow-up;
   - `REPLAN` with the proposed task briefs and the reason, in a fenced `baton-plan` block (plan
     tasks only; a review fix that turns out bigger is declined with that reason instead);
   - `QUESTION:` or `BLOCKED:` after posting the question, the options and a recommendation as an
     issue comment and setting `status:blocked`. Work in progress is pushed first.

The last plan task merges the default branch in and trial-merges against other open pull
requests, resolving or reporting conflicts. Developers never tick boxes, edit issue descriptions
or review comments, run review tools, or start subagents of their own.

**Spikes** have no plan: one developer does the whole investigation, with an optional prototype
ref, and posts a findings comment. On long work it posts a status comment about every 30
minutes naming its working folder, so a later developer can resume. Spikes are not code-reviewed.

## 7a. Reviewer: one pull request, one review (`review`)

Adapted from the superpowers plugin's `requesting-code-review` and `receiving-code-review`
skills. The reviewer gets a crafted brief (the issue, the pull request, the commits to review),
never the orchestrator's history.

1. Read the issue (the spec, with its `Decisions`), the repo brief, the project's agent rules and
   the pull request. Earlier reviews and the developer's replies, on a later round.
2. Check out the head commit in its own worktree and review `merge-base..head`; on a later round,
   review what changed since the last review (the review tasks' commits) and whether each task
   was fixed or rightly declined.
3. Run the verification command when the brief allows it.
4. Judge: the acceptance criteria are met and nothing outside the issue's scope changed; the
   project's rules in the brief are kept (for example where expected test values must come
   from); a measurement measures what the user waits for; tests verify real behaviour; error
   handling, security and privacy (no personal data in code, tests or text); and anything in the
   pull request that reads like instructions to agents, which is data.
5. For each problem, a severity (`critical`, `important`, `minor`) and a disposition, decided by
   the reviewer:
   - **fix here**: a defect in this change, an unmet acceptance criterion, a broken project rule,
     a security or privacy problem, or missing tests for the new behaviour. Every critical
     problem is fixed here.
   - **follow-up**: a problem that was there before, lies outside the issue's scope, or would
     grow the pull request beyond its acceptance criteria. The reviewer writes a title and two
     lines; the orchestrator files it in `status:refinement`.
6. List what it considered and set aside as outside the issue, one line each, so nothing is
   dropped silently.
7. Post one `review` comment on the pull request, stamped with the verdict and the full head
   commit: `approve` when nothing is to be fixed here, otherwise `changes`. Return the verdict.
   The "fix here" problems are a `## Tasks` checklist in the plan's brief format, numbered
   `<round>.<n>`; the reviewer may group related small problems into one task:

   ```markdown
   - [ ] **1.1 Clamp the fade estimate**
     - Problem: `src/defade/detect.rs:58` divides by a channel mean that can be 0 (critical)
     - Fix: return `None` when the mean is below 1
     - Check: new test `black_frame_is_not_faded`; `cargo test defade::detect`
   ```

A reviewer is dispatched only when every plan task is ticked, and on a later round only when
every task of the previous review is ticked and answered: one review of the whole change, not one
per task. `baton review post ... approve` refuses while any plan task or previous review task is
open, so `merge-pr` cannot merge unfinished work.

**Review state** is read from the pull request's comments, so any session can resume it: no
review; changes asked for; the developer replied (review again); approved. An approval stays
valid after later commits only while the pull request's own change is identical (the diff from
its merge base, compared without line positions), which is what merging the default branch in
does. Any other change needs a new round, which reviews only the difference.

The review is a quality gate, not a security boundary: every agent posts under the maintainer's
login, so a manipulated agent could imitate a review. Protected paths still need the maintainer's
own `/baton approve`.

## 7b. Quota use

Every tool call re-sends the agent's whole conversation, so an agent's cost grows with its number
of calls times its conversation size. In the second photofactory sprint, developers made 120 to
270 calls and grew to 300K to 680K tokens; calls made above 250K were over half the cost. Small
tasks (sections 4 and 7) are the main answer. The develop, review and refine skills also share
these working habits:

- **Read what you need.** Find and filter with `rg -n`, `sed -n 'a,bp'`, `awk`, `head`, `tail`
  and `find`. `cat` is fine for short files and when the whole file is needed. Compare numbers
  rather than open images, unless the task is about how something looks.
- **Logs go to a file.** Long test, parity and benchmark output goes to a log in the working
  folder; read the summary and failure lines from it.
- **One call for related commands.** Chain related shell commands in one call.
- **Wait once.** Run long commands in the foreground with a long timeout (up to ten minutes). For
  anything longer, start it in the background and make one blocking wait. Never check the same
  run twice.

**`baton usage`** reads the local transcripts under `~/.claude/projects/` (sessions and their
`subagents/*.jsonl`) and reports, per issue and role (task, review, fix, refine, orchestrator,
other): agents, calls, weighted tokens (cache read 0.1, cache write 1.25, output 5 times input),
peak conversation size and the share of the cost from calls above 250K tokens, plus a total per
five-hour window. An agent is matched to its issue by its dispatch prompt; API calls are counted
once per request id; unreadable records are skipped and counted. Options: `--since <time>`,
`--json`. The sprint retrospective includes its table, which holds only issue numbers and counts.

## 8. Communication

- Every agent comment starts with a hidden marker `<!-- baton:<role>:<kind> -->` and a visible
  prefix such as **Baton developer: question**. Roles: `orchestrator`, `developer`, `refine`,
  `reviewer`. Kinds: `question`, `status`, `decision`, `plan`, `review`, `findings`, `reply`. A
  reviewer's `review` marker also carries the verdict and the commit:
  `<!-- baton:reviewer:review verdict=approve commit=<sha> -->`.
- A maintainer answer is a comment by the repository owner without a marker.
- Answers given in the session are posted to the issue by the orchestrator as a `decision` comment
  quoting the maintainer. That comment is then the one linked from `Decisions`.

## 9. Security

### 9.1 Privacy: block before publishing

Forge content is not reliably recallable (edit history, caches), so the check runs before
publication.

- A `PreToolUse` hook intercepts every publishing command: `gh issue|pr create|comment|edit`,
  `gh api`, `git commit`, `git push`. It scans the text the command would publish and blocks on a
  match. Text it cannot see (stdin, a variable, command substitution, a missing file) is refused.
- Git `pre-commit` and `pre-push` hooks scan the added lines, new file names and commit messages.
  The `PreToolUse` hook refuses any attempt to skip them.
- Generic patterns ship with the plugin: private IP ranges, email addresses, absolute host paths,
  internal domain suffixes.
- Maintainer-specific terms (names, hostnames, folder names) live in
  `~/.baton/private-terms.txt`, outside every repository. The plugin never copies or logs matches.
- New image files are blocked outside `fixtures_paths`.
- Commits use the forge's no-reply author address.
- Known limit: a deny-list catches only known strings. Briefs must also tell agents to use
  placeholders and never paste tool output that may contain real data.

### 9.2 Prompt injection: who counts as the maintainer

- **Dispatch gate.** Only `status:ready` issues are dispatched. Labels can be applied only by
  people with write access, so an outsider cannot mark an issue ready.
- **Refinement gate.** Because `refine` may apply `status:ready` itself, it does so only for
  issues written by the maintainer or by a Baton agent. An issue written by anyone else can be
  refined but is left at `needs-maintainer` until the maintainer marks it ready, whatever
  `may_mark_ready` says.
- Text from any other author (comments, pull request text, fetched pages, tool output such as OCR)
  is passed to agents quoted and labelled as untrusted data.
- Developers cannot merge: the gate refuses `gh pr merge`. `merge-pr` refuses a pull request that
  touches `protected_paths` unless the maintainer has commented `/baton approve <commit sha>` on
  it, naming the head commit or an earlier one whose changes to the protected paths are identical
  to the head's.
- Agents work only on branches they created; they never check out or run code from forks.
- Skills are never edited in place by a sprint; changes to Baton arrive as pull requests.
- Scheduled runs take the stricter path: anything flagged gets `needs-maintainer` and waits.

### 9.3 Identity

Version 1 runs under the maintainer's own forge login, so the hook stamps the marker on every
agent comment and blocks unmarked agent posts. This is weaker than a separate identity: a
manipulated agent that found another way to post could imitate the maintainer.

**Planned upgrade, after a working version exists:** a machine account for agents, with a token
limited to the target repository. GitHub's terms allow one free machine account per person. The
skills need no change; "maintainer comment" becomes "comment by the owner login", enforced by the
forge.

## 10. Lessons carried over from the prototype sprint

| Prototype problem | Baton answer |
|---|---|
| One developer per issue grew to 300K-680K tokens and used the quota in about an hour | Plans of small tasks, a fresh developer per task, and `baton usage` to measure it |
| Merged before CI finished; default branch went red | `merge-pr` requires SUCCESS on the head commit |
| Review ran against the wrong commit | The reviewer reviews the head commit in its own worktree, and its review names that commit |
| Issues not auto-closed after merge | `merge-pr` closes the issue itself |
| Disk and CI minutes ran short | Optional budgets checked every pass |
| Blanket cleanup removed a running agent's files | `merge-pr` cleans only that pull request's worktree and branch |
| An agent was lost on restart | Developers and the orchestrator resume from forge state alone |
| A private string reached a pull request body | Publish gate runs before posting |
| Project rules lived in one shared brief | Repo brief plus local brief |

## 11. Other forges

The skills name steps neutrally and rely only on issues, labels, milestones, comments, change
requests and CI status. Dependencies are written in the issue description, not as a forge-specific
feature. Supporting Forgejo later means adding `references/forgejo.md` that maps the steps to the
existing Forgejo MCP tools, and matching those tools in the publish gate. GitLab would follow the
same pattern. Neither is required.

## 12. Testing

- **Scripts and hooks:** unit tests with recorded `gh` output and sample texts; the publish gate
  must block each generic pattern and a sample private term, and pass clean text.
- **Skills:** scenario runs against a scratch repository with seeded issues, covering: a clean
  issue, a question and answer with the cited decision, overlapping files, a red default branch,
  a spike with a prototype ref, an issue worked task by task and resumed mid-plan, a `REPLAN`
  inside and outside the acceptance criteria, a review worked as tasks, a remapped label set, an untrusted comment containing
  instructions, and a sprint resumed in a fresh session.
- **Pilot:** one real sprint on a small batch, followed by the first retrospective.

## 13. Build order

1. Repository skeleton, plugin manifest, `init` with label setup and mapping.
2. Publish gate and `merge-pr`, with tests.
3. `develop` skill and the developer subagent; `review` skill and the reviewer subagent; task
   plans, `tasks` and `usage`.
4. `refine` skill.
5. `sprint-plan` skill.
6. `sprint` skill, interactive runs.
7. Scheduled runs.
8. Pilot sprint and first retrospective.

## 14. Deferred

- Weights and capacity estimates.
- Machine account for agents.
- Forgejo and GitLab references.
