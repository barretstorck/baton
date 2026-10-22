## Pattern

Extend the existing plan-file code instead of replacing it: `lib/baton/planfile.py` already parses,
reads and links the `Plan:` line, and `lib/baton/checklist.py` ticks and replaces tasks in a file
(`tick_issue`, `_replace_in_file`). A ref plan is a third source beside "legacy body" and "file on
a branch"; all three keep working for one release (see the issue's "Plans already in flight").
Tests use `FakeRunner` (`tests/fakes.py`, replies keyed by command-line prefix, calls in
`runner.calls` / `runner.lines()`) as `tests/test_planfile.py`, `tests/test_checklist.py`
(`LinkedTickReplaceTest`, line 297) and `tests/test_merge.py` (`plan_replies`, line 195) do.

## Verification

`PYTHONPATH=lib python3 -m unittest discover -s tests -v` (the README's "Tests" section).

## Design notes

- **Link kinds.** Every link keeps the shape `Plan: [<path>](<url>)`. `parse_link` returns
  `{"ref", "path", "pinned", "kind"}` with `kind` one of:
  - `"ref"`: url `https://github.com/<repo>/commits/refs/plans/<N>`; `ref` is `refs/plans/<N>`, `pinned` False;
  - `"pinned"`: url `.../blob/<40-hex sha>/<path>` (the existing format; used by both the old and the new flow);
  - `"branch"`: url `.../blob/<branch>/<path>` (in-flight old format).
  Existing callers read `ref`, `path`, `pinned`; keep those keys and values for `"branch"` and `"pinned"`.
- **Path inside the ref.** The ref's tree holds only the plan file, at the same repo-relative path
  as before, `<docs.dir>/plans/<N>-<slug>.md`, so `check_link`, the link text and `/blob/<sha>/<path>`
  permalinks need no new rules. `refs/plans/<N>` must match the `<N>` that starts the file name.
- **Local ref.** Never use `FETCH_HEAD` (concurrent agents share one `.git`). Fetch with
  `git fetch --quiet origin +refs/plans/<N>:refs/baton/plans/<N>` and read
  `git show refs/baton/plans/<N>:<path>`. A pinned sha that is not in the local object store is
  fetched the same way (N taken from the path's file name) and shown again.
- **Commit without a checkout.** Build nested trees bottom-up with `git hash-object -w --stdin`
  and one `git mktree` per directory level (the runner takes no environment, so no temporary
  index), then `git commit-tree <tree> [-p <parent>] -m <message>`. The first commit has no
  parent (the ref has its own history). Parent = `refs/baton/plans/<N>` after a fetch when
  `git ls-remote origin refs/plans/<N>` is not empty. A commit is never pushed by these helpers
  except where a task says so.
- **Atomic push** printed for developers: `git push --atomic origin HEAD:refs/heads/<branch> <sha>:refs/plans/<N>`.
- **Necessary additions beyond the issue text** (consequences of having no plan file in the
  checkout; say so in the PR): `baton task done issue <N> <n> --as-built "<text>"` adds the
  `Provides (as built): <text>` line under that task's `Provides:` line in the same commit (develop
  step 4 asks for this today by editing the file); `baton plan pin <N>` pins the link to the ref's
  tip (used by `merge-pr` and by the orchestrator when it closes an issue without a merge);
  `baton plan commit <N> <path> <file|->` writes a plan file as a new commit on the ref and
  pushes it (used by the refiner). `baton plan link <N> refs/plans/<N> <path>` writes the history-page link.
- The pre-push privacy scan (`lib/baton/githooks.py` `pre_push`) scans pushes to any ref name.

## Plan

Written against: dc7d89f8e49ade16665665d7312fe65a2c9ac435

- [ ] **1. Link formats and validation**
  - Files: `lib/baton/planfile.py`, `tests/test_planfile.py`
  - Uses: `link_line(repo, ref, path)` (`lib/baton/planfile.py:26`), `check_link(ref, path, docs_dir=None)` (`:33`), `parse_link(body, docs_dir=None)` (`:80`), `_LINK` (`:15`), `PlanfileTest` (`tests/test_planfile.py:21`) and `BadLinkTest`-style cases at `:240-280`
  - Provides: `ref_name(number) -> str` returning `"refs/plans/<number>"`; `link_line(repo, ref, path)` returns the history-page link when `ref` starts with `refs/plans/` and otherwise the unchanged `/blob/<ref>/<path>` link; `parse_link` returns the `kind` key of Design notes for all three formats; `check_link` accepts `refs/plans/<N>` only when `<N>` is digits and equals the number that starts the path's file name, and still refuses every ref it refused before
  - Check: `PYTHONPATH=lib python3 -m unittest tests.test_planfile -v` with new tests: history-page link round-trips; pinned and branch links parse as before plus `kind`; `refs/plans/7` with path `.../5-x.md` is refused; `refs/plans/x` refused

- [ ] **2. Read, write and pin at the ref**
  - Files: `lib/baton/planfile.py`, `tests/test_planfile.py`
  - Uses: `read(runner, ref, path)` (`lib/baton/planfile.py:107`), `link_issue(runner, repo, number, branch=None, path=None, at=None, docs_dir=None)` (`:118`), `checklist.TaskError`, `checklist._save_issue`
  - Provides: `read(...)` also handles `refs/plans/<N>` (fetch into `refs/baton/plans/<N>`, show) and a pinned sha missing locally (fetch that ref, retry); `write(runner, number, path, text, message) -> str` building the commit as in Design notes and returning its sha, not pushed; `tip(runner, number) -> str | None` (the ref's sha on the remote via `git ls-remote origin refs/plans/<N>`); `push(runner, number, sha)` running `git push origin <sha>:refs/plans/<N>`; `pin(runner, repo, number, docs_dir=None) -> str | None` calling `link_issue(..., at=<tip>)` and returning the sha, or None when the link is already pinned or is not a `"ref"` link; `link_issue` accepts `branch="refs/plans/<N>"` and writes the history-page link
  - Check: `PYTHONPATH=lib python3 -m unittest tests.test_planfile -v` with new tests on the `FakeRunner` call lists: `write` issues `hash-object`, `mktree` for `plans`, `baton`, `docs` levels, then `commit-tree` with `-p` only when the ref exists; `read` of a ref link fetches then shows; `pin` writes the permalink and is a no-op for a pinned link

- [ ] **3. Tick and replan on the ref**
  - Files: `lib/baton/checklist.py`, `tests/test_checklist.py`
  - Uses: `tick(text, heading, task_id)` (`lib/baton/checklist.py:86`), `replace_plan` (`:139`), `plan_changes` (`:121`), `tick_issue(runner, number, task_id, cwd=".", docs_dir=None)` (`:237`), `_replace_in_file` (`:265`), `replace_issue_plan` (`:298`), `_linked` (`:227`), `planfile.write`, `planfile.push`, `planfile.read` from task 2
  - Provides: `tick_issue(..., as_built=None) -> bool` for a `"ref"` link reads the plan at the ref, ticks (and with `as_built` inserts the `Provides (as built): ...` line under that task's `Provides:` line), commits with `planfile.write` as `Task <n>`, prints `plan commit <sha>` and `push with: git push --atomic origin HEAD:refs/heads/<branch> <sha>:refs/plans/<N>` and does not push; already ticked writes nothing and prints nothing new; `branch` and legacy-body links behave exactly as before (file under `cwd`, issue body); `_replace_in_file` for a `"ref"` link writes the replaced plan with `planfile.write` (message as today, `Replan from task <n>` plus `Dropped:` line) and pushes it with `planfile.push`; `_linked` no longer raises for a `"ref"` link
  - Check: `PYTHONPATH=lib python3 -m unittest tests.test_checklist -v` with new tests for a ref link: tick commits to the ref and does not touch `cwd`; second tick is a no-op; `as_built` line appears under `Provides:`; replace commits and pushes `<sha>:refs/plans/<N>`; a branch link still ticks the file in `cwd`

- [ ] **4. Command line for ref plans**
  - Files: `lib/baton/cli.py`, `tests/test_planfile.py`
  - Uses: `cmd_task` (the `task done` branch, `lib/baton/cli.py:~175`), `cmd_plan` (`:190-228`), usage strings, `planfile.write`/`push`/`pin`/`link_issue` from task 2, `checklist.tick_issue(..., as_built=...)` from task 3
  - Provides: `baton task done issue <N> <n> [--as-built <text>]`; `baton plan commit <N> <path> <file|->` (reads the text as `comment.read_body(<file|->, sys.stdin)` does, scans nothing new, commits with message `Plan for #<N>: <title>` from `gh issue view <N> --json title`, pushes with `planfile.push`, prints the sha); `baton plan link <N> refs/plans/<N> <path>` writes the history-page link; `baton plan pin <N>`; the usage text lists all of them
  - Check: `PYTHONPATH=lib python3 -m unittest tests.test_planfile -v` with new tests that call `cli.main([...])` (see how other tests call the cli, for example `tests/test_usage.py`; if none does, call the `cmd_*` functions with a patched `run.run`) for each new form and for a bad argument count (exit 64)

- [ ] **5. Migrate on touch**
  - Files: `lib/baton/planfile.py`, `lib/baton/cli.py`, `tests/test_planfile.py`
  - Uses: `migrate(cfg, runner, repo, number, branch, default_branch)` (`lib/baton/planfile.py:192`), `split_legacy` (`:171`), `parse_link`, `plan_path` (`:22`), `STATUSES` guard at `:197-201`, `read`, `write`, `push` from task 2, `cmd_plan` migrate branch (`lib/baton/cli.py:213`)
  - Provides: `migrate(cfg, runner, repo, number, slug, default_branch) -> str` (the new `Plan:` line): refuses an issue that is `status:in-dev` or later, as today; source is the body's `## Plan` (via `split_legacy`) or the plan file the existing `"branch"` link names (its latest version at `origin/<branch>`); it commits the text to `refs/plans/<N>` with `write`/`push`, relinks the issue to the history page (`set_link` on the trimmed body for a body plan), and for a branch plan commits `git rm` of the plan file on the branch from a temporary detached worktree and pushes it; refuses an issue that already has a `"ref"` link; `baton plan migrate <N> [<slug-or-branch>]` accepts the old `issue-<N>-<slug>` form (strips the prefix) and a plain slug, and when none is given takes the slug from the existing link's path
  - Check: `PYTHONPATH=lib python3 -m unittest tests.test_planfile -v`; update the existing migrate tests (`:163-205`) to the new signature and add: body plan to ref; branch plan to ref and file removed from the branch; refused when started or already a ref link

- [ ] **6. Readers: ready-check and sprint-status**
  - Files: `lib/baton/issue.py`, `lib/baton/sprint.py`, `tests/test_issue.py`, `tests/test_sprint.py`
  - Uses: `planfile.parse_link`/`planfile.read` in `ready_problems` (`lib/baton/issue.py:55-90`), `checklist.plan_source` call with `plan_cache` (`lib/baton/sprint.py:104-127`), the `LINKED_HEAD` fixtures in `tests/test_issue.py:175` area and the progress test at `tests/test_sprint.py:215`
  - Provides: no new signatures; behaviour: `ready-check` on a history-page link reads the plan through the ref and reports `the Plan: link cannot be read (...)` when the ref is missing; `sprint-status` `progress.<N>.plan` is computed from the ref plan
  - Check: `PYTHONPATH=lib python3 -m unittest tests.test_issue tests.test_sprint -v` with new tests: ref link with a plan passes; ref missing fails (the `FakeRunner` reply for `git fetch` raising `RunError`; see `RaisingRunner` in `tests/test_issue.py`); sprint progress counts ticks from a ref plan

- [ ] **7. merge-pr pins in one pass; review.current**
  - Files: `lib/baton/merge.py`, `lib/baton/review.py`, `tests/test_merge.py`, `tests/test_review.py`
  - Uses: `_retire_plan(pr, repo, runner, cfg)` (`lib/baton/merge.py:200-244`), `merge(number, cfg, runner, notes=None)` (`:246`), `PlanRetired`, `_linked_plan_path(runner, pr, cfg)` (`lib/baton/review.py:128`), `_only_plan_deleted` (`:144`), `planfile.pin` from task 2, `plan_replies` and the retire tests in `tests/test_merge.py:193-270`
  - Provides: in `merge`, for a `"ref"` link `planfile.pin(...)` runs before `gh pr merge` and the merge goes on in the same call (no `PlanRetired`, no retire commit, no waiting for CI); a missing ref adds a note and does not block; a `"branch"` link still takes the old `_retire_plan` path unchanged; `_linked_plan_path` returns None for a `"ref"` link, so `current` never excludes a plans path for a ref plan and still does for an in-flight branch plan
  - Check: `PYTHONPATH=lib python3 -m unittest tests.test_merge tests.test_review -v` with new tests: ref plan pinned and merged in one `merge()` call, with no `git rm` and no push to the branch; branch plan still raises `PlanRetired`; `review.current` with a ref link has no `:(exclude)` argument in the `same_change` call

- [ ] **8. Refine and develop skills, templates**
  - Files: `skills/refine/SKILL.md`, `skills/develop/SKILL.md`, `agents/baton-developer.md`, `templates/plan.md`, `templates/issue.md`
  - Uses: the who-ticks-what text #22 adds under develop step 4's tick line and in `agents/baton-developer.md` (plan task: "ticks its own box in the plan file ... and commits it with the code"); refine definition-of-ready `Plan:` row (`skills/refine/SKILL.md:36`), step 4 and step 5 (`:71-111`), report step (`:123`); develop "A plan task" step 1 (`skills/develop/SKILL.md:40-52`), step 3 (`:64-83`), step 4 tick bullets (`:88-94`), steps 5-6 (`:96-105`); `templates/issue.md:1`; the plan template's closing note
  - Provides: refine: the `Plan:` row says the link opens the plan ref's commit history; step 5 commits the plan with `baton plan commit <N> <docs.dir>/plans/<N>-<slug>.md <file>`, then `baton plan link <N> refs/plans/<N> <path>`; no branch is created or pushed; re-refining adds a commit; legacy issues and in-flight branch plans are moved with `baton plan migrate <N>` when not in development; the report says "ref" instead of "branch". Develop: step 1 reads the plan with `git fetch --quiet origin +refs/plans/<N>:refs/baton/plans/<N> && git show refs/baton/plans/<N>:<path>` (path from `baton tasks issue <N>`); missing ref returns `BLOCKED: the plan ref is missing`; step 3 task 1 creates `issue-<N>-<short-slug>` from `origin/<default>` and the plan-only rebase and its force-push exception are deleted; step 4 ticks with `baton task done issue <N> <n> [--as-built <text>]`, which prints the commit; step 6 pushes `git push --atomic origin HEAD:refs/heads/<branch> <sha>:refs/plans/<N>` for every task, task 1 included (`-u` is dropped: the checkout is detached); a refused push is `BLOCKED: the branch moved`; #22's who-ticks-what contrast in develop step 4 and its sentence in `agents/baton-developer.md` say a plan task is ticked with `baton task done issue <N> <n>` on the plan ref and pushed with the code in the atomic push, not in the plan file in the checkout (the review-task half and the "never run `baton task done pr`" rule stay as they are). The in-flight form (plan file on the branch) stays described as the older way. Templates: `Plan:` line described as the history-page link
  - Check: `rg -n "plan-only|Plan for" skills/develop/SKILL.md` prints nothing; `rg -n "in the plan file|with the code" skills/develop/SKILL.md agents/baton-developer.md` shows no sentence telling a developer to tick or commit the plan file in its checkout, except text marked as the in-flight branch case; `rg -n "refs/plans" skills/refine/SKILL.md skills/develop/SKILL.md templates` shows the new text; `PYTHONPATH=lib python3 -m unittest tests.test_planfile -v` still passes (its `test_templates`, `:208`, reads the templates)

- [ ] **9. Sprint and review skills, design spec, README**
  - Files: `skills/sprint/SKILL.md`, `skills/review/SKILL.md`, `docs/superpowers/specs/2026-10-14-plans-in-repo-design.md`, `README.md`
  - Uses: sprint `merge-pr` paragraph about the pinned link and removal commit (`skills/sprint/SKILL.md:130-134`), legacy issues paragraph (`:214-218`), resuming paragraph (`:222-230`); review step 1 and step 3 (`skills/review/SKILL.md:34-40`, `:58-61`); spec sections "Lifecycle of a plan file" (line 46), "Retiring a plan" (line 80), "Permanence depends on `--merge`" (search with `rg -n "Permanence" docs/superpowers/specs/2026-10-14-plans-in-repo-design.md`); README "Plans of small tasks" (`README.md:23-45`) and the `## Spike prototypes` section added by #24
  - Provides: sprint: `merge-pr` pins and merges in one pass for a ref plan, the retire-and-wait text is described as the in-flight branch case only; an issue closed without a merge gets `baton plan pin <N>` first; migrate on touch uses `baton plan migrate <N>`; resuming uses `baton task done issue <N> <n>` then the atomic push, and reads ticks with `baton tasks issue <N>`. Review: the plan is read from the plan ref (`git fetch --quiet origin +refs/plans/<N>:refs/baton/plans/<N>`), nothing about plan ticks or removal on the branch for ref plans. Spec: a note at the top of those three sections that issue #25 supersedes them for new plans. README: plans bullet says the plan lives at `refs/plans/<N>` not on the branch; the spike-prototype section is generalised to custom refs, its public-repository checklist also lists `git ls-remote origin 'refs/plans/*'` and the same deletion caveats
  - Check: `rg -n "retire|Remove the plan" skills/sprint/SKILL.md` leaves only text marked as the in-flight case; `rg -n "refs/plans" README.md` shows the note and the checklist

- [ ] **10. Full verification**
  - Files: none
  - Check: `PYTHONPATH=lib python3 -m unittest discover -s tests -v` passes
