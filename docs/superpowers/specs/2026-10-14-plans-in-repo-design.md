# Plans in the repository, not in issue bodies (issue #5)

Approved in conversation 2026-10-14. Supersedes the "`## Plan` in the issue body" parts of
`2026-10-01-baton-design.md` and the 0.3.0 plan work.

## Problem

`/baton:refine` writes the whole implementation plan into the issue description. Issues become long
and hard for a person to read, and every tick and replan is an untracked edit outside git history.

## Goals

- Issues stay short and human-facing: what, why, the contract, the discussion.
- The plan (tasks, verification, design notes) is a versioned file, changed by commits.
- The issue always links to the latest version of its plan, including after the plan is gone from
  the working tree.
- The default branch does not accumulate one file per issue.

## Non-goals

- Spikes: unchanged (no plan; Verification stays in the issue).
- Review tasks (`r.n`): unchanged (they stay in the reviewer's PR comment).
- Forgejo/GitLab: GitHub first, as for the rest of Baton.

## Design

### Where things live

| Content | Where |
|---|---|
| `Plan:` link (first line of the body) | issue |
| Context, Acceptance criteria, Out of scope, Decisions | issue |
| `Expected files:` and `Blocked by:` lines | issue (read in bulk by `sprint.py`) |
| Pattern to follow (rest of Pointers), Verification | plan file |
| `Written against:`, tasks | plan file |
| Design notes (optional: approach, rejected options, risks) | plan file |

New config key in `.baton/brief.md` front matter: `docs.dir`, default `docs/baton`. It must be a
relative path inside the repository and must not be under a `protected_paths` entry.

- Plans: `<docs.dir>/plans/<N>-<slug>.md`, where `<slug>` matches the issue branch.
- Retrospectives: `<docs.dir>/retros/<sprint title>.md` (was `docs/retros/`).
- `templates/plan.md` holds the moved sections; `templates/issue.md` loses them and gains the
  `Plan:` line.

### Lifecycle of a plan file

The plan lives only on the issue branch `issue-<N>-<slug>`, never on the default branch's tree.

1. **Refine.** The refiner creates `issue-<N>-<slug>` from the default branch (developers no
   longer create it at task 1), commits the plan file, pushes, runs `baton plan link <N>`, then
   writes the trimmed issue body. Re-refining adds a commit on the same branch.
2. **Before task 1.** If the branch holds only plan commits, the developer rebases it onto the
   current default branch. `Written against:` still records what the plan was checked against.
3. **Tasks.** `baton task done <id>` ticks the box in the local plan file; the developer commits
   the tick with the task's code. The orchestrator confirms the tick is in the pushed head instead
   of editing the issue.
4. **Replan.** `baton plan replace <N> <from-task> <file>` commits the new plan to the branch and
   pushes.
5. **Merge.** See "Retiring a plan".
6. **Closed without merge.** The plan stays on the unmerged branch; the link is pinned first (see
   below).

Readers (`sprint-status`, `ready-check`, `baton tasks`, the reviewer) read the plan with
`git show origin/<branch>:<path>` after a fetch.

### The `Plan:` link

`baton plan link <N> [--at <sha>]` owns one line at the top of the issue body:

    Plan: [docs/baton/plans/5-plans-in-repo.md](https://github.com/<owner>/<repo>/blob/issue-5-plans-in-repo/docs/baton/plans/5-plans-in-repo.md)

- Without `--at`, it links to the branch, which always shows the latest version, so ticks and
  replans need no issue edit.
- With `--at <sha>`, it links to that commit: a permalink.
- Rule: any Baton command about to remove a plan file or delete an issue branch pins the link to
  the last commit that has the file, first.
- `ready-check` fails when the line is missing or its path does not exist on the linked ref.

### Retiring a plan (merge)

`merge-pr` today checks that the owner's approval covers the head commit. Retiring adds a commit
after approval, so:

1. Check approval and the other merge conditions on the current head, as now.
2. Pin: `baton plan link <N> --at <head sha>` (the head still has the file).
3. Commit `git rm <plan path>` on the branch (in a temporary detached worktree) and push.
4. Stop and report that checks are running on the new head. `merge-pr` does not wait today, so
   the orchestrator runs it again once they pass; that run finds the link pinned and the file
   gone, skips steps 2 and 3, and merges with `--merge`.

`review.same_change` already takes paths; `review.current` passes
`[".", ":(exclude)<docs.dir>/plans/"]`, so the retiring commit does not make the reviewer's
approval stale. Any other change still does. Plan paths are not protected, so the maintainer's
`/baton approve` check is unaffected.

If a step after 2 fails, the pinned link is still valid, and re-running `merge-pr` resumes.

**Permanence depends on `--merge`.** The pinned commit is reachable from the default branch only
because Baton merges with a merge commit. A squash or rebase merge would orphan permalinks
(GitHub keeps them through the PR ref only). The spec forbids changing the merge method without
revisiting this.

### Legacy issues (plan in the body)

Migrate on touch. Baton 0.4 reads both formats: an issue with a `Plan:` link uses the file; one with
a `## Plan` section uses the body as in 0.3. Whenever the refiner or orchestrator touches a legacy
issue that is not in development, it runs `baton plan migrate <N> <branch>`, which moves the
plan, Verification and pattern text to a file (branch, commit, link, trimmed body).
An issue already in development keeps its body plan until it merges. The legacy reader is removed
in a later release once no open issue uses it.

### Errors

- Plan file missing on the branch: `develop` returns BLOCKED; `ready-check` reports it.
- Branch missing for a linked plan: `ready-check` reports it; the refiner re-creates it from the
  pinned commit if there is one.
- Tick not in the pushed head after DONE: the orchestrator treats the task as not done and asks
  the developer to push it.
- Plan files pass through the existing privacy scan in the git hooks like any other commit.

### Testing

Unit tests with the existing fake runner:

- `checklist` reads, ticks and replaces a plan in file text (the parser is unchanged; only the
  source changes).
- `plan link`: branch link, `--at` permalink, replaces an existing line, keeps the rest of the body.
- `ready-check`: new format passes; missing link, missing file, missing `Written against:` fail;
  legacy format still passes.
- `merge-pr`: order is approve-check, pin, remove, push, merge; approval survives the removal
  commit; a change outside the plan path still invalidates it; a re-run after a failure resumes.
- `sprint-status` / `baton tasks`: progress from the file on the branch, and from a legacy body.
- `config`: `docs.dir` default, validation (absolute path, `..`, protected path refused).
