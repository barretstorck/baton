# Answers

Read this when `answered` has entries, and when the maintainer answers a question in the session.

For each entry in `answered`, the maintainer has replied to a blocked issue.

1. Read the question and the answers. If the answers do not actually settle the question, post a
   `question` comment saying what is still open, and leave the issue blocked.
2. Otherwise add one line per decision to the description's `## Decisions` section, each ending
   with a link to the answering comment:

       - <the decision, in one sentence>. ([answer](<comment URL>))

   If the decision sets the order of two issues, also add `Blocked by: #N` (the earlier issue) to
   the later issue's Pointers in the same description edit. `sprint-status` holds an issue on that
   line and does not read Decisions, so use it instead of prose alone; it releases when the blocker
   closes, which its merged pull request does.

   Write the full new description to a file and update the issue. Keep all earlier decisions.
3. `baton set-status <N> status:ready`. A developer will resume it from its branch.

**Answers given in this session.** If the maintainer answers a question here in the conversation,
the issue must still be the record. Post it first:
`baton comment issue <N> orchestrator decision <file>`, quoting the maintainer's words, then do
steps 2 and 3 using that comment's URL.
