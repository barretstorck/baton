# Sprint review and merge handling

The sprint orchestrator reads this before acting on any `in_review` entry (`skills/sprint/SKILL.md`, step 4, Review and merge).

For each entry in `in_review`. Its task progress is `progress.<N>` in `sprint-status`: `plan`
(the issue's tasks) and `review` (the latest review's tasks).

- **Spike** (`progress.<N>.spike` is true): read the findings comment, then follow its last
  `Outcome:` line. Call the spike `<S>` and the issue in its `Spike for:` line `<O>`:
  1. `infeasible`: run `baton notify <O> spike-infeasible -` with an inline heredoc naming the
     spike and its reason, then close the spike with the reason.
  2. `single`: remove the `Blocked by: #<S>` entry from `<O>`'s description (write the complete
     description to a file, then `gh issue edit <O> --body-file <file>`; keep other `Blocked by:`
     entries, and drop the line if it would be empty). Dispatch `baton:baton-refiner` in the
     background, with the Agent tool's `model` and `effort` from `refiner.model` and
     `refiner.effort`, and exactly:

         Re-refine issue <O> after spike #<S>.
         Repository: <owner>/<repo>.

     It takes no developer slot. Then close the spike.
  3. `multiple`: file the follow-ups the findings propose that are worth having (`skills/sprint/SKILL.md`, step 6, Follow-ups), each
     with the spike's `Spike for:` and `Origin: outside (@<login>)` lines under its Pointers and "see #<S>" in
     its Context. Post a `status` comment on `<O>` linking them, then close the spike.
  4. `partial`: file a follow-up spike for the open questions (title `[SPIKE] ...`, label
     `type:spike`, `status:refinement`, the same `Spike for:` and `Origin: outside (@<login>)` lines, its
     Context listing the open questions). Replace `Blocked by: #<S>` on `<O>` with
     `Blocked by: #<new spike>`. Judge from the findings which of 1 to 3 the answered questions
     amount to and apply it to them. Then close the spike.

  A findings comment with no `Outcome:` line: dispatch the researcher again once, for it to add
  the line; if it is still missing, `needs-maintainer` (as in `references/sprint-returns.md`, the "anything else" rule).
  When the report's `Prototype:` is not `none`, run `git ls-remote origin refs/spikes/<N>` and
  `git ls-remote --heads origin 'issue-<N>-*'`: delete the branch with
  `git push origin --delete <branch>` if it still exists; if the ref is missing, report that to
  the maintainer and do not close the issue. Otherwise set `status:complete` and close the issue with
  `gh issue close <N> --comment "<one-line findings summary>"`.
  Spikes are not code-reviewed.
- **Not a spike and no pull request, or `progress.<N>.plan.next` is set:** the plan is not
  finished (an interrupted task, or a replaced plan). `baton set-status <N> status:in-dev`; `skills/sprint/SKILL.md` step 7 (Dispatch)
  dispatches the next task, which opens the draft pull request if it is missing.
- **`review` is `changes` but `progress.<N>.review_without_tasks` is true:** a review from before
  Baton 0.3 with no task list. Dispatch a reviewer with the extra line "The last review has no
  task list: review again and write one."
- **Not a spike, `progress.<N>.plan.next` is null, `draft` is true and `review` is not `changes`,
  `replan`, `escalate` or `repeated`:** the plan is finished (or the review's fixes are replied to), so
  mark the pull request ready: `gh pr ready <PR>`. CI runs once, now, instead of on every push
  of a draft. The reviewer is dispatched in this same pass (next bullets).
- **Ready (not a draft), every plan task ticked, and `mergeable` is `CONFLICTING`:** update it
  before any review: dispatch a developer with `Update pull request <P> (issue <N>).` A red `ci`
  is not fixed by a developer: the reviewer turns it into fix tasks.
- **Ready (not a draft), every plan task ticked, and `ci` is `cancelled`:** the run was cancelled,
  so CI did not finish; it did not fail. This is not `red`: dispatch no developer fix task. No `baton` command re-runs a workflow, so do not run `gh run rerun`; ask the maintainer, or
  make an empty update commit on the pull request branch and push it. The new head has
  no checks yet (`pending`) and needs a new verification record (#49) before it can be approved.
  `merge-pr` keeps refusing cancelled checks; never suggest overriding it. The `approved`
  conditions need `ci` `green` or `none`, so a `cancelled` pull request waits to merge.
- **`review_requested` is true (tier 2):** a maintainer asked Baton to review. Dispatch a reviewer (see "Dispatching a reviewer" below) whatever the `review` state, including `approved` and `replied`, before merging; the new review clears it.
- **`review` is `none`, `replied`, `stale` or `blocked` (its cause gone):** dispatch a reviewer
  (see "Dispatching a reviewer" below) once every plan task is ticked
  (`progress.<N>.plan.next` is null) and the pull request is ready (mark it ready just before,
  in the same pass, if it is still a draft). Do not look at `ci`: the reviewer runs the full
  verification and waits for CI itself. `stale` means the pull request's change moved after it
  was approved.
- **`blocked`:** read the review's `Cause:` line. Blocked reviews are not rounds.
  - `default-branch`: follow `references/sprint-default-branch.md`; the pull request waits. When the
    default branch is green again, dispatch a reviewer.
  - `environment`: dispatch one reviewer again on the next pass. If that review is `blocked` with
    cause `environment` a second time in a row, run `baton notify <N> stuck <file | ->` with a text linking both reviews (it adds `needs-maintainer` and posts the
    orchestrator `status` comment); the issue goes under `Needs you`.
- **`repeated`:** the same tests failed in two reviews in a row. Run `baton notify <N> stuck <file | ->` with a text linking the two reviews and naming the shared failing ids (it adds `needs-maintainer` and posts the
  orchestrator `status` comment), and
  put the issue under `Needs you`. Do not re-draft the pull request or dispatch a fix round. Record the
  answer as a decision (`skills/sprint/SKILL.md`, step 3, Answers; `references/sprint-answers.md`) and then handle it like an answered `escalate`.
- **`changes`:** if `draft` is false, turn the pull request back into a draft: `gh pr ready <PR>
  --undo`, so that CI runs once per "developer done" and not once per pushed fix task. Then
  `baton set-status <N> status:in-dev`. Step 7 then works the review's tasks one
  at a time, and `references/sprint-returns.md` posts the reply when the last one is ticked. This takes a developer slot.
- **`replan`:** the reviewer says the plan, not the code, is the problem, and the pull request
  has no refiner's reply yet. Run each as a command on its own:
  1. `baton set-status <N> status:refinement` (the issue keeps its milestone).
  2. `gh pr ready <PR> --undo`, only when `draft` is false.
  3. An orchestrator `status` comment on the issue linking the review:
     `baton comment issue <N> orchestrator status <file>`.
  4. Dispatch `baton:baton-refiner` in the background, with exactly:

         Re-refine issue <N> after the review of pull request <P>.
         Repository: <owner>/<repo>.

  It takes no developer slot. When it has rewritten the open tasks it marks the issue ready and
  posts a `refine` `reply` on the pull request; `skills/sprint/SKILL.md`, step 7 (Dispatch), then dispatches the next task as for a
  resumed issue, and the reviewer comes back after the last one, as after any reply.
- **Each entry in `re_refine`** (an issue in `status:refinement` with an open pull request and no
  `needs-maintainer`; a session died after step 3 of `replan`): `gh pr ready <PR> --undo` if
  `draft` is false, and dispatch the same refiner prompt, unless a refiner for that issue is
  already running in this session. The refiner's work is safe to repeat.
- **`escalate`:** the review rounds in the brief are used up and the reviewer still asks for
  changes, or two `replan` reviews came in a row (the maintainer decides what the plan should
  be). Run `baton notify <N> stuck <file | ->` with a text linking the
  last review and the last reply, saying what the two disagree about (it adds `needs-maintainer` and posts the orchestrator `status` comment); the issue goes under
  `Needs you`. When the maintainer answers, record it as a decision (`skills/sprint/SKILL.md`, step 3, Answers; `references/sprint-answers.md`); if the answer sends the
  review's tasks to a developer, re-draft the pull request as for `changes` (`gh pr ready <PR>
  --undo` when `draft` is false) and work the tasks (`skills/sprint/SKILL.md`, step 7, Dispatch).
- **`approved`**, or `not-required`:
  0. `approved` with `verify` `missing` (the brief sets `verify.command`; the head moved after the
     approval, typically by merging the default branch in): dispatch a reviewer for a short
     re-approval (see "Dispatching a reviewer"), and merge only once the new review shows `verify`
     `green`. Never run `baton verify` or post a review yourself.
  1. Still a draft (a fallback; normally ready already): `gh pr ready <PR>`. CI starts now; look
     again next pass.
  2. `ci` is `pending`: wait.
  2a. `ci` is `cancelled`: handle it as the ready-pull-request `cancelled` case above (re-run or
     empty update commit); not the Answers step (`skills/sprint/SKILL.md`, step 3), and no merge until `ci` is `green`.
  3. `mergeable` is `CONFLICTING`: post an orchestrator `status` comment on the pull request,
     `baton set-status <N> status:in-dev`, and dispatch a developer with
     `Update pull request <P> (issue <N>).` Merging the default branch in does not void the
     review, but a new head needs a new verification record (`skills/sprint/SKILL.md`, step 5, Verification).
     `ci` is `red` on an approved head (a new head after an update): dispatch a reviewer, who
     turns the failure into fix tasks; no developer fixes CI directly.
  4. Green and mergeable: if `review` is `not-required`, check briefly that every acceptance
     criterion is addressed, nothing outside the scope changed, the body has `Closes #<N>` once
     and the verification line is present. Then run `baton merge-pr <PR>` **as a command on its
     own**. It re-checks the review (and refuses a head whose review does not record `verify` as `green` when the brief sets `verify.command`), CI on the head commit, mergeability, closing references and
     protected paths, then merges, closes the issue, sets `status:verification` and removes that
     pull request's branch and worktree. Read its output before doing anything else; never chain
     another command after it, and never set a status it did not set. If it says it kept a
     worktree, that is for a live lock or unmerged work; uncommitted files do not keep one. The plan lives in the issue body, so nothing is pinned or pushed to
     the branch and `merge-pr` merges in one pass. (An older in-flight plan that is still a file
     on the branch is the exception: `merge-pr` pushes a commit removing the file and stops
     without merging. Wait for CI on the new head, then run `baton merge-pr <PR>` again; the
     reviewer's approval still holds.)
- **`merge-pr` refuses:** read its reasons.
  - *Protected paths:* run `baton notify <N> approval <file | ->` (it adds `needs-maintainer` and mentions the maintainers), post the request for the comment
    `/baton approve <head sha>` on the issue, naming the sha, and list the issue under `Needs you`. Ask only once the review is approved and CI is
    green, so the sha you name is the one that will merge. The approval stays valid if only the
    default branch is merged in later, so other merges may go ahead meanwhile. Never post that
    comment yourself.
  - *No reviewer approval:* dispatch a reviewer.
  - *Anything else:* post what failed and dispatch a developer, as for red CI.

Merge **one pull request at a time**. After each merge, the others are behind: when one of them
conflicts and no developer is running for it, dispatch one with
`Update pull request <P> (issue <N>).` Merging the
default branch in does not void its review. Issues that touch the same files run in parallel, and
the conflicts that follow a merge are expected: the `Update pull request` developer resolves them,
never the orchestrator.

#### Dispatching a researcher

A spike is dispatched to `baton:baton-researcher` with `Do spike issue <N>.`, never to a developer.
Start it in the background, with the Agent tool's `model` set to `researcher.model` and `effort`
set to `researcher.effort` (from `baton config`, default `opus` and `medium`); say in the sprint
status which effort you used. A spike takes a developer slot.

When the spike's `## Pointers` have a `Timebox:` line, the prompt is `Do spike issue <N>.`
followed by that line copied unchanged on its own line. The `baton budget` hook reads the token
limit from it, and `baton usage` still classifies the prompt by its first line.

#### Dispatching a reviewer

Start a `baton:baton-reviewer` subagent in the background, with the Agent tool's `model` set to
`reviewer.model` and `effort` set to `reviewer.effort` (from `baton config`, default `opus` and
`medium`); say in the sprint status which effort you used. Reviewers do not take developer slots, but run no more of them at once than the
parallel limit, and never two on one pull request. The prompt is short; the issue and the pull
request are the brief. Its first line is exact, because `baton usage` reads it:

    Review pull request <PR> (issue <N>).
    Repository: <owner>/<repo>. Sprint goal: <goal>.

Do not paste the diff, the developer's report or your own opinion into the prompt.
