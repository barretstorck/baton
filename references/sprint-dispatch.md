# Dispatch special cases

Read this when `sprint-status` reports a `stale` plan, a `held` issue, `path_check_skipped` or `merge_first`, for an issue with an older plan, when an issue in development has no plan or every task ticked, and when resuming a session.

**Issue in development with no plan (`unplanned`).**
   - `progress.<N>.unplanned` is true (work started before Baton 0.3, with no plan): dispatch
     `baton:baton-refiner` with `Refine issues <N>.` and the extra line "Work is in progress on
     branch <branch>: plan only the remaining work, and list what the branch already does as
     ticked tasks." Dispatch its next task once the plan is in.

**Every task ticked.**
   - **every task ticked** (no `review.next`, no `plan.next`): the developer finished but the
     session ended before the next step. If the latest review asks for changes and no reply
     follows it, post the reply (`skills/sprint/SKILL.md`, step 8, When a developer or reviewer returns), building each line from the branch's `Review <r.n>:`
     commits; ask on the issue about a task with no commit: post an orchestrator `question` comment and run `baton notify <N> stuck <file | ->` (it goes under `Needs you`). Then
     `baton set-status <N> status:in-review`. The pull request stays a draft until the next pass
     marks it ready (`skills/sprint/SKILL.md`, step 4, Review and merge); CI runs once, and the reviewer comes only when `ci` is `green` or
     `none`.

**Stale plans and far-behind branches.** `progress.<N>.stale` is
`{"missing_paths", "ambiguous_paths", "path_check_skipped", "plan_behind"}` for every issue with a plan: the paths its
open tasks name that no longer exist on the default branch (null when the check did not run;
it does not count a path that the same or an earlier task, or an open blocker or a blocker of
one, marks `(new)`, a bare name short for such a path, a directory a listed path starts with, or
a token that is not a repository path, such as a container image or a dotted code reference),
the bare names among them that match several tracked files, as a map from name to those paths
(`{}` when none, null when the check did not run; the hold reason lists the matches),
why the check was skipped (null when it ran), and how many default-branch commits the plan's `Written against:` commit lacks.
`progress.<N>.branch_behind` is how many default-branch commits the issue's pull request branch
lacks (null without a pull request), and `progress.<N>.merge_first` is true when that reaches
`BEHIND_LIMIT` (15).

- A ready issue `held` as "plan names <n> missing paths; re-refine" is not dispatched to a
  developer. Send it to `baton:baton-refiner` with `Refine issues <N>.` and say in the sprint
  status that you did.
- When `path_check_skipped` is set (the default branch listing failed or came back empty),
  `missing_paths` is null and cannot be trusted. Say in the sprint status that the stale-plan check
  did not run for that issue, and check by hand that the plan's Expected files exist on the default
  branch before dispatching (or send it to `baton:baton-refiner`). Do not hold the issue only
  for this.
- When `merge_first` is true, add one line to the developer prompt, after the repository line
  (an allowed exception to the exact-prompt rule, since the first line stays exact):

      Start by merging origin/<default>: the branch is <n> commits behind.

**Older issues.** An issue whose plan is on `refs/plans/<N>` (a `Plan:` first line), in a file
on its branch, or in a `## Plan` section is an older issue. Before you dispatch its first task,
while it is not yet in development, run `baton plan migrate <N>`; it converts the plan into the
`## Developer Plan` section of the issue body. An issue already in development keeps its older
plan until it merges.

Look at `held` too: if an issue is held for a reason you can clear (a blocker you can dispatch
first), order the work accordingly; otherwise leave it.

A free slot is permission to start work, not to go against a recorded order. If the only issue
that could fill it has Decisions or Pointers saying it comes after another issue, leave the slot
empty and ask on that issue: `baton comment issue <N> orchestrator question <file>`,
run `baton notify <N> stuck <file | ->`, and list the issue under `Needs you`. This holds whether the slot came from a raised parallel limit or from
a finished developer.

**Resuming.** A new session cannot tell from its own memory which developers are still running.
For each `in_dev` issue, compare `git log --oneline origin/<default>..origin/<branch>` with the
ticked boxes (`baton tasks issue <N>` reads them from the issue body). A `Task <n>:` commit whose box is not ticked was done by a developer
that was interrupted before it finished: run that task's `Check:` on that commit in a scratch
worktree, and if it passes, run `baton task done issue <N> <n>`, which ticks the box in the issue body;
otherwise dispatch the task again. (A developer ticks its own box before it pushes the code.) A `Review <r.n>:`
commit whose box is not ticked is checked the same way, then `baton task done pr <P> <r.n>`. A
task with no commit is dispatched again.
