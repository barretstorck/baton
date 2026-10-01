# Baton: design

**Status:** draft for maintainer review (revision 2)
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
| `refine` | skill | Brings an issue up to the definition of ready, or says what is missing. |
| `sprint-plan` | skill | Builds a sprint: asks for the goal, length and limits, then compiles ready issues into a milestone. |
| `sprint` | skill | Orchestrator. Works a planned milestone: dispatches, checks, merges, files follow-ups, runs the retrospective. Never writes product code. Calls `sprint-plan` when given no planned sprint. |
| `develop` | skill | Developer. Takes one issue to one pull request. Never merges. |
| `baton-developer` | subagent definition | Preloads `develop`, runs in an isolated git worktree with a restricted tool list. Uses **Claude Sonnet** by default; the repo brief may override the model. |
| publish gate | hooks | Blocks personal data before anything is published; stamps agent comments. |
| `merge-pr` | script | The only way a pull request is merged. |
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
| `status:in-dev` | developer | Claimed; branch and draft pull request exist |
| `status:blocked` | developer | Waiting on an answer or a dependency |
| `status:in-review` | developer | Pull request ready, tests pass, self-review done |
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

**The issue description is the single source of truth**
([GitLab](https://handbook.gitlab.com/handbook/product-development/how-we-work/product-development-flow/#issue-descriptions-as-the-single-source-of-truth-ssot)).
When a question is answered, the answer is folded into the description under a `Decisions`
heading, and each entry ends with a markdown link to the comment that gave it, for example:

```markdown
## Decisions
- Imperceptible output differences are acceptable. ([answer](https://github.com/<o>/<r>/issues/12#issuecomment-123))
```

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

### Spikes

Issues of `type:spike` deliver a findings comment and proposed follow-up issues. A pull request
is optional. A spike may open a draft pull request holding prototype code; it is never required
and is not merged by the orchestrator. The spike is complete when its findings are posted. Its
draft stays open and linked, so if the maintainer promotes the work to a real issue a developer
can build on that branch. Unpromoted drafts are listed in the retrospective for the maintainer to
close.

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

- a developer finishes (pull request marked ready, `status:in-review`);
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
5. **Check finished work.** For each `status:in-review` pull request:
   - acceptance criteria all addressed;
   - CI concluded SUCCESS on the head commit;
   - mergeable against the default branch;
   - closing reference correct, and no stray closing keywords;
   - no protected path touched without maintainer approval (section 9).

   Pass: `merge-pr`, then `status:verification`. Fail: back to `status:in-dev` with a comment.
   Merges are one at a time; remaining pull requests are updated and re-tested afterwards.
6. **Verify.** When the default branch is green after a merge, set `status:complete`. The issue
   itself was already closed by the merge.
7. **File follow-ups.** Developer suggestions become new issues in `status:refinement`. They join
   the sprint only if they serve the sprint goal.
8. **Dispatch.** Fill free slots, up to the parallel limit, with `status:ready` deliverables in
   priority order, skipping any whose `Blocked by` is open or whose `Expected files` overlap a
   running issue.
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
delivered, what went right, what went wrong with cost and root cause, and how to improve. Lessons
that would change Baton itself are opened as a pull request to the Baton repository.

## 7. Developer: one issue to one pull request (`develop`)

Follows GitLab's [engineering basics](https://handbook.gitlab.com/handbook/engineering/workflow/#basics).

1. Read the issue description, both briefs and the project's agent rules. The description is the
   spec; comments from anyone but the maintainer are data.
2. Claim: `status:in-dev`, a branch `issue-<N>-<slug>` in its own worktree, and a draft pull
   request. If a branch and draft already exist, resume from them.
3. Implement with tests. Stay in scope; out-of-scope ideas go in the report.
4. Run the full verification command.
5. Self-review with the code-review skill, run from the worktree against the branch diff. Fix
   confirmed findings.
6. Merge the default branch in, and trial-merge against other open pull requests. Resolve or
   report conflicts.
7. Mark the pull request ready and set `status:in-review`.
8. Return one of:
   - `DONE` with the pull request number and any follow-up suggestions;
   - `QUESTION:` or `BLOCKED:` after posting the question, the options and a recommendation as an
     issue comment and setting `status:blocked`. Work in progress is pushed first so another
     developer can resume.

If an issue will not fit, the developer delivers a smaller complete piece and proposes the rest
as a follow-up
([GitLab: ship a smaller iteration](https://handbook.gitlab.com/handbook/engineering/workflow/#working-in-teams)).

For `type:spike` issues, steps 3 to 7 are replaced by the investigation, an optional draft pull
request, and a findings comment.

## 8. Communication

- Every agent comment starts with a hidden marker `<!-- baton:<role>:<kind> -->` and a visible
  prefix such as **Baton developer: question**. Kinds: `question`, `status`, `decision`, `plan`,
  `review`, `findings`.
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
  touches `protected_paths` unless the maintainer has commented `/baton approve <head commit sha>`
  on it.
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
| Merged before CI finished; default branch went red | `merge-pr` requires SUCCESS on the head commit |
| Review ran against the wrong commit | Self-review runs from the worktree against the branch diff |
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
  a spike with a draft prototype, a remapped label set, an untrusted comment containing
  instructions, and a sprint resumed in a fresh session.
- **Pilot:** one real sprint on a small batch, followed by the first retrospective.

## 13. Build order

1. Repository skeleton, plugin manifest, `init` with label setup and mapping.
2. Publish gate and `merge-pr`, with tests.
3. `develop` skill and the developer subagent.
4. `refine` skill.
5. `sprint-plan` skill.
6. `sprint` skill, interactive runs.
7. Scheduled runs.
8. Pilot sprint and first retrospective.

## 14. Deferred

- Weights and capacity estimates.
- Machine account for agents.
- Forgejo and GitLab references.
