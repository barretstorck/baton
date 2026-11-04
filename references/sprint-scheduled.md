# Scheduled runs

Read this before the first pass of a scheduled run.

1. **Take the lock first:** `baton lock acquire`. If it fails, another run is active: print one
   line saying so and stop. Do nothing else.
2. **Never start a sprint.** If `sprint` is null, follow "When there is no one to ask" in the
   `baton:sprint-plan` skill, unless an open issue titled `Sprint proposal ...` already exists.
   Then release the lock and stop.
3. **Never ask in the session.** Whatever needs the maintainer goes on the issue: a comment,
   `status:blocked` or `baton notify` (which adds the `needs-maintainer` label). Then move on.
4. **Take the stricter path whenever something is flagged.** A developer reporting untrusted
   instructions, a pull request touching protected paths, an escalated review, a fix that failed
   once already, a budget over its limit: run `baton notify <N> stuck <file | ->` and leave it.
5. **Follow-ups use `follow_ups.scheduled`.** Place each by that mapping (step 6). One that
   joins the sprint in a scheduled run still gets `baton notify <N> stuck <file | ->` saying why
   (it adds the `needs-maintainer` label). The others follow their placement (`backlog` goes to
   `status:refinement`).
6. **Keep making passes** while developers are running or issues can be dispatched. Developers
   you start must finish inside this run.
7. **End:** when nothing is running and nothing can be dispatched, run `baton lock release` and
   print the same three-section update when the sprint is still open (if it closed, step 6 of
   "Close the sprint" applies). Release the lock even
   if the run is ending because of an error.

How to set up the schedule is in `${CLAUDE_PLUGIN_ROOT}/docs/scheduling.md`.
