# Follow-up checking and placement

Read this before filing a follow-up issue (step 6): it says how to check its claim and where to place it by severity.

Before filing, check the follow-up's central claim ("X currently does Y", "Z was not measured")
against the code on the default branch (`rg`, or `git show origin/<default>:<path>`) or against
the findings comment it came from (`baton issue read <N>`). If you cannot check it in a
few commands, write in the issue's Context that the claim is **unverified** and name its source
("per the developer of #N"). Do not repeat an agent's claim as fact.

Place each by its severity (`critical`, `important`, `minor`; the review's own words). Read
`follow_ups` from `baton config` (`follow_ups.scheduled` in a scheduled run) and use the
placement it names for the severity:

- `deliverable` or `stretch`: joins the open sprint with that label
  (`baton issue edit <N> --milestone "<title>"`), is refined with `baton:refine` as a subagent
  at once, and is dispatched once ready.
- `goal`: joins as `stretch` only when it serves the sprint goal; otherwise it is filed in the
  backlog.
- `backlog`: filed in `status:refinement`, outside the sprint, for the maintainer to prioritise.
- `skip`: not filed.

A developer's suggestion has no severity. Treat it as `important` if it names a security,
privacy, data-loss or performance problem; otherwise as `minor`. A spike's proposed follow-up is
placed as `important`. With no open sprint, every placement except `skip` files to the backlog.
A rule in the milestone description overrides the brief for that one sprint.
