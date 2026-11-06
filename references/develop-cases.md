# Develop: rare cases

Read by the developer agent when the `develop` skill sends it here.

## Stale branches

If your prompt has the line "Start by merging origin/<default>", or you find the branch at
least 15 commits behind `origin/<default>` (`git rev-list --count HEAD..origin/<default>`),
begin the task by merging the default branch in and resolving the conflicts, as its own
commit, before the task's own work; push it with the task's push.

## Interrupted tasks

- If the branch already has a `Task <n>:` commit for your task, an interrupted developer did it:
  check it against the brief and that its commit ticks the task (`baton tasks issue <N>`),
  finish it if needed, and continue from step 5.
- Whatever the task number, if the branch exists but no pull request closes the issue
  (`gh pr list --head <branch>`), open the draft pull request now, as task 1 would.

## Hook refusals

If the hook refuses the commit because it adds a line the brief's
`forbid_added` forbids, remove that entry; do not bypass the hook or edit the rule. Only an
`Allows: forbid_added "<glob>"` line in your task's brief permits it. If you cannot do the task
without it, return `QUESTION`.

## Opening the draft pull request

After the push in step 6, open a **draft** pull request from the detached checkout with
`gh pr create --draft --title "<title>" --body-file <file>`; the gate uses the pushed `issue-<N>-*` branch as
the head (or takes `--head <branch>`), so do not create a local branch. The body has
`Closes #<N>` on its own line and a short summary of the plan. Drafts may not run CI, which
saves minutes.

## Background

Adapted from the GitLab handbook's
[engineering basics](https://handbook.gitlab.com/handbook/engineering/workflow/#basics) and the
superpowers plugin's plan-execution skills: a fresh developer per task, working from a brief that
names the files, the interfaces and the check, so nobody's conversation grows large.

## Keeping a pull request mergeable

For `Update pull request <P> (issue <N>).`, follow these steps in order:

1. Check out the pull request branch tip detached, as in step 3 above. Record
   `before=$(git rev-parse HEAD)`.
2. `git fetch origin <default>` and `git merge origin/<default>`. Resolve conflicts; for each
   conflicted file, find the plan tasks whose `Files:` list it (read the plan as in step 2 above)
   and run only those tasks' `Check:` lines. No conflicts: run none, and do not run the whole
   suite (Baton handles the full verification of the new head, see #49; you do not run it).
3. Commit the merge if git did not. Whether or not there was anything to merge, run
   `baton push HEAD:refs/heads/<branch>` (pushing an unchanged head is a no-op and must still
   be run). Never force.
4. Read the pushed head back: `git ls-remote origin refs/heads/<branch>` must print
   `git rev-parse HEAD`. If it does not, return `BLOCKED: the branch moved`.
5. Return the update block (see "Return") with that sha. "Already up to date" is a normal
   outcome: the sha is then `before`, and the block's `Merged:` line says `nothing to merge`. The
   flow ends only with that block, `BLOCKED:` or `QUESTION:`; a one-word or placeholder report is
   not a result.

## REPLAN

When the plan cannot be followed as written (an interface changed on the default branch, a step
is missing, a task is in the wrong order), propose new tasks instead of improvising:

- Write them in the plan's task form (`- [ ] Task <n>: <title>` followed by an indented
  `<details>` block whose summary is "Click to see plan details" and whose two-space bullets are
  Files, Uses, Provides, Port from and Check), numbered from the task you were given, replacing it and every open
  task after it.
- Say why in one paragraph, and whether the new tasks stay inside the issue's acceptance
  criteria. If they would change what the issue promises, say so plainly.
- Push nothing half-done that the new plan does not use.

The orchestrator replaces the open tasks and records the change on the issue, or asks the
maintainer when the promise would change.

## When you need a decision

Do not guess on anything the description does not settle and that changes behaviour or output.

1. Commit and push your work in progress, so another developer can resume it.
2. Post the question: what you need decided, the options you see, your recommendation, and what
   you have done so far. Keep it short and self-contained. See "Posting a comment" below.
3. `baton set-status <N> status:blocked`
4. Return `QUESTION:` with the same text and the comment URL. Stop there; do not wait.

A brief that no longer fits the code is not a question: return `REPLAN` (above).

Use `BLOCKED:` in the same way for something that is not a question: a dependency that is not
merged, missing access, a failing default branch.

## Posting a comment

Pass the body on standard input with an inline heredoc:

    baton comment issue <N> developer <kind> - <<'EOF'
    <the body>
    EOF

A body that names a `git` command is written to `.baton-scratch/comment.txt` and passed as that path in place of `-`.
Claude Code's worktree isolation refuses a command line that names `git` inside a heredoc.

`baton comment` scans the text, adds the Baton marker and prints the comment's URL. If it
refuses because of a privacy finding, replace the detail with a placeholder and post again.

If you cannot post at all, put the complete body in your final report inside a block fenced with
```` ```baton-comment <kind> ````, so the orchestrator can post it unchanged.

A plan that no longer fits:

````
REPLAN
Issue: #<N>, from task <n>
Why: <one paragraph>
Within the acceptance criteria: yes | no, because <...>
```baton-plan <n>
- [ ] Task <n>: <title>
  <details>
  <summary>Click to see plan details</summary>

  - Files: ...
  - Check: ...

  </details>
```
````

Any task:

```
QUESTION:
Issue: #<N>
Comment: <URL>
<the question, options and recommendation>
Work so far: branch <name>, <what is pushed>
```

```
BLOCKED: <one line>
Issue: #<N>
<what blocks it, what you tried, what would unblock it>
```

## Older issues

An issue whose `Plan:` link points at `refs/plans/<N>` (and has no `## Developer Plan`) is refused
by baton. Do not read or tick it: return `BLOCKED: run baton plan migrate <N>`.
