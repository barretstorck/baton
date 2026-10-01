# Baton: design

**Status:** draft for maintainer review
**Date:** 2026-10-01

Baton is a Claude Code plugin that runs short software sprints on a forge repository. A manager
agent (the orchestrator) plans a sprint from ready issues, dispatches developer agents in parallel,
checks and merges their pull requests, and writes a retrospective. The forge is the only place
state and conversation live.

## 1. Goals and non-goals

**Goals**

- One command starts or continues a sprint:
  `/baton:sprint Pick issues in <repo> that can be completed within 1 day. Use up to 4 parallel subagents.`
- The same skill runs interactively or from a schedule. A scheduled run is one *tick* of an ongoing
  sprint, not a whole sprint.
- All questions and answers are issue comments. The maintainer may answer on the issue or in the
  Claude Code session; session answers are mirrored to the issue.
- Sprints are short (one to three days) so the orchestrator's context stays small.
- Nothing personal is ever published to the forge, and text from untrusted authors is never
  treated as instruction.
- Each retrospective can improve the next sprint.

**Non-goals**

- Deciding *what* to build. Product validation and design happen before an issue is refined.
- Deployment and release management.
- GitLab and Forgejo support in the first version (see section 10).

## 2. Components

| Component | Kind | Purpose |
|---|---|---|
| `sprint` | skill | Orchestrator. Plans, dispatches, checks, merges, files follow-ups, runs the retrospective. Never writes product code. |
| `develop` | skill | Developer. Takes one issue to one pull request. Never merges. |
| `refine` | skill | Brings an issue up to the definition of ready, or says what is missing. |
| `baton-developer` | subagent definition | Preloads `develop`, runs in an isolated git worktree, with a restricted tool list. |
| publish gate | hooks | Blocks personal data before anything is published; stamps agent comments. |
| `merge-pr` | script | The only way a pull request is merged. |
| `references/github.md` | reference | Maps each neutral step ("post a question") to `gh` commands. |

The skills are generic. Everything specific to a project comes from two briefs:

- **Repo brief** `.baton/brief.md`, committed in the target repository: test command, definition of
  done, conventions, parallel limit default, weight budget, paths that need maintainer approval.
- **Local brief** `~/.baton/<owner>-<repo>/brief.local.md`, never committed: hosts, paths,
  credentials locations and anything else private that agents need to do the work.

## 3. Issue lifecycle

Status is one label at a time, prefixed `status:`. Setting a status removes the previous one.

| Status | Set by | Meaning |
|---|---|---|
| `status:refinement` | maintainer or orchestrator | Needs work before it is ready |
| `status:ready` | **maintainer only** | Meets the definition of ready; may be dispatched |
| `status:in-dev` | developer | Claimed; branch and draft pull request exist |
| `status:blocked` | developer | Waiting on an answer or a dependency |
| `status:in-review` | developer | Pull request ready, tests pass, self-review done |
| `status:verification` | orchestrator | Merged; waiting for the check on the default branch |
| `status:complete` | orchestrator | Verified; issue closed |

Other labels: `type:feature|bug|maintenance|spike`, `weight:1|2|3|5`, `deliverable`, `stretch`,
`missed:<sprint>`, `needs-maintainer`.

**Definition of ready** (checked by `refine`):

- A specific title, and context that says why the work matters.
- Acceptance criteria as checkboxes that can be verified objectively.
- An out-of-scope section.
- Pointers to the likely files and the existing pattern to follow.
- The exact verification command, or "repo default".
- One outcome only; anything larger is split.
- A `Blocked by: #N` line for each dependency, and an `Expected files:` line used for overlap checks.
- A type and a weight.

`refine` may edit the description and propose `status:ready`, but only the maintainer applies it.
That label is the trust gate for dispatch (section 8).

**The issue description is the single source of truth.** When a question is answered, the answer
is folded into the description under a `Decisions` heading. Nobody should need the comment thread
to know the current spec.

**Spikes** are issues of `type:spike`. The deliverable is a findings comment and proposed follow-up
issues, not a pull request.

## 4. Sprint tracking

- **A milestone is the sprint.** Title `Sprint YYYY-MM-DD-n`. Its description holds the sprint goal,
  the parallel limit and the weight budget, so a fresh tick can read them back.
- Issues in the milestone carry `deliverable` or `stretch`.
- At close, unfinished issues move to no milestone with `missed:<sprint>`.
- The retrospective is a file in the target repository, `docs/retros/<sprint>.md`, opened as a pull
  request.
- No project board is required.

## 5. Orchestrator: one tick

Every run, interactive or scheduled, performs the same tick. It holds no memory between ticks.

1. **Reconcile.** Read the open sprint milestone, its issues, their status labels, open pull
   requests and new comments. If the default branch's CI is red, stop dispatching and merging;
   the only allowed work is fixing or reverting.
2. **Check budgets** named in the repo brief (CI minutes, disk). Over budget means no dispatch.
3. **Collect answers.** For each `status:blocked` issue with a maintainer comment newer than the
   agent's question, fold the answer into the description and set `status:ready`.
4. **Check finished work.** For each `status:in-review` pull request, run the acceptance check:
   - acceptance criteria all addressed;
   - CI concluded SUCCESS on the head commit;
   - mergeable against the default branch;
   - closing reference correct, and no stray closing keywords;
   - no path that needs maintainer approval (section 8).

   Pass: `merge-pr`, then `status:verification`. Fail: back to `status:in-dev` with a comment.
   Merges are one at a time; remaining pull requests are updated and re-tested afterwards.
5. **Verify.** When the default branch is green after a merge, set `status:complete` and close.
6. **File follow-ups.** Developer suggestions become new issues in `status:refinement`. They join
   the sprint only if they serve the sprint goal and the budget allows.
7. **Dispatch.** Fill free slots, up to the parallel limit, with `status:ready` deliverables in
   priority order, skipping any whose `Blocked by` is open or whose `Expected files` overlap a
   running issue.
8. **Close the sprint** when every issue is complete or waiting on the maintainer: write the
   retrospective, label the rest `missed`, close the milestone.

**Planning.** With no open sprint:

- *Interactive:* propose an issue list from the `status:ready` queue that fits the weight budget,
  and wait for the maintainer's yes before creating the milestone.
- *Scheduled:* never start a sprint. Post the proposed plan as an issue labelled `needs-maintainer`
  and exit. The sprint begins when the maintainer approves it.

**Interactive runs** repeat the tick while developers are working, instead of exiting.
**Scheduled runs** do one tick and exit; developers dispatched in that tick must finish or return
within it.

## 6. Developer: one issue to one pull request

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
as a follow-up, instead of running long.

## 7. Communication

- Every agent comment starts with a hidden marker `<!-- baton:<role>:<kind> -->` and a visible
  prefix such as **Baton developer: question**. Kinds: `question`, `status`, `decision`, `plan`,
  `review`.
- A maintainer answer is a comment by the repository owner without a marker.
- Answers given in the session are posted to the issue by the orchestrator as a `decision` comment
  quoting the maintainer, then folded into the description.

## 8. Security

### 8.1 Privacy: block before publishing

Forge content is not reliably recallable (edit history, caches), so the check runs before
publication.

- A `PreToolUse` hook intercepts every publishing command: `gh issue|pr create|comment|edit`,
  `gh api` writes, `git commit`, `git push`. It scans the text or diff and blocks on a match.
- Generic patterns ship with the plugin: private IP ranges, email addresses, absolute host paths,
  internal domain suffixes.
- Maintainer-specific terms (names, hostnames, folder names) live in
  `~/.baton/private-terms.txt`, outside every repository. The plugin never copies or logs matches.
- A git `pre-push` hook repeats the scan on commit messages and diffs.
- New image files are blocked outside an allowlisted synthetic-fixtures path set in the repo brief.
- Commits use the forge's no-reply author address.
- Known limit: a deny-list catches only known strings. Briefs must also tell agents to use
  placeholders and never paste tool output that may contain real data.

### 8.2 Prompt injection: who counts as the maintainer

- Only issues carrying `status:ready` are dispatched, and only the maintainer applies that label.
- Text from any other author (comments, pull request text, fetched pages, tool output such as OCR)
  is passed to agents quoted and labelled as untrusted data.
- Developers cannot merge. `merge-pr` refuses a pull request that touches CI workflows, hooks,
  `.baton/`, or agent and skill definitions unless the maintainer has approved it on the pull
  request.
- Agents work only on branches they created; they never check out or run code from forks.
- Retrospective lessons that would change Baton itself are opened as a pull request to the Baton
  repository for the maintainer to approve. Skills are never edited in place by a sprint.
- Scheduled runs take the stricter path: anything flagged gets `needs-maintainer` and waits.

### 8.3 Identity

Version 1 runs under the maintainer's own forge login, so the hook stamps the marker on every
agent comment and blocks unmarked agent posts. This is weaker than a separate identity: a
manipulated agent that found another way to post could imitate the maintainer.

**Planned upgrade:** a machine account for agents, with a token limited to the target repository.
GitHub's terms allow one free machine account per person. The skills need no change; "maintainer
comment" becomes "comment by the owner login", enforced by the forge.

## 9. Lessons carried over from the prototype sprint

| Prototype problem | Baton answer |
|---|---|
| Merged before CI finished; default branch went red | `merge-pr` requires SUCCESS on the head commit |
| Review ran against the wrong commit | Self-review runs from the worktree against the branch diff |
| Issues not auto-closed after merge | `merge-pr` closes the issue itself |
| Disk and CI minutes ran short | Budgets checked every tick |
| Blanket cleanup removed a running agent's files | `merge-pr` cleans only that pull request's worktree and branch |
| An agent was lost on restart | Developers resume from forge state alone |
| A private string reached a pull request body | Publish gate runs before posting |
| Project rules lived in one shared brief | Repo brief plus local brief |

## 10. Other forges

The skills name steps neutrally and rely only on issues, labels, milestones, comments, change
requests and CI status. Dependencies and weights are written in the issue description and labels,
not forge-specific features. Supporting Forgejo later means adding `references/forgejo.md` that
maps the steps to the existing Forgejo MCP tools, and matching those tools in the publish gate.
GitLab would follow the same pattern. Neither is required.

## 11. Testing

- **Scripts and hooks:** unit tests with recorded `gh` output and sample texts; the publish gate
  must block each generic pattern and a sample private term, and pass clean text.
- **Skills:** scenario runs against a scratch repository with seeded issues, covering: a clean
  issue, a question and answer, overlapping files, a red default branch, an untrusted comment
  containing instructions, and a tick resumed with no memory.
- **Pilot:** one real one-day sprint on a small batch, followed by the first retrospective.

## 12. Build order

1. Repository skeleton, plugin manifest, label and milestone setup.
2. Publish gate and `merge-pr`, with tests.
3. `develop` skill and the developer subagent.
4. `refine` skill.
5. `sprint` skill, interactive mode.
6. Scheduled mode.
7. Pilot sprint and first retrospective.

## 13. Open items

- Whether scheduled ticks may dispatch developers at all, or only reconcile, merge and collect
  answers, given that a developer must finish within the tick.
- How weights map to a "one day" budget; start from the prototype's throughput and tune in
  retrospectives.
- When to move to a machine account.
