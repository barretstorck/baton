# Retrospective detail

Read this when closing the sprint (`skills/sprint/SKILL.md`, step 9, item 1) to run the metrics and write the retrospective.

1. Run `baton sprint-metrics "<sprint title>" --since <the sprint's first dispatch, as UTC ISO time>` and paste its
   output unchanged into the retrospective's `## Metrics` section.
   Write the retrospective from `${CLAUDE_PLUGIN_ROOT}/templates/retro.md` to
   `<docs.dir>/retros/<sprint title>.md` on a branch, and open a pull request for it. Fill it from the
   forge: merged pull requests, decisions with their links, what was carried over. Fill the two follow-up rows from the sprint's review comments: the follow-ups filed, counted as `critical` / `important`, and the problems the reviews fixed in the pull request instead of filing them. Be specific
   about what went wrong, what it cost, and the root cause. Add a "Quota use" section with the
   table from `baton usage --repo <owner>/<repo> --since <the sprint's first dispatch, as UTC ISO time>`: it holds only
   issue numbers and counts. Name the issues whose tasks still grew large (peak over 250K) and
   why.
