# Follow-up checking and placement

Read this before filing a follow-up issue (`skills/sprint/SKILL.md`, step 6): it says how to search for a duplicate, check its claim and record the check, and where to place it by severity.

## Search for a duplicate

Before filing a follow-up from a review, a developer report or a spike's findings, run both
searches (see "Search for a duplicate follow-up" in `references/github-orchestrator.md`) with two
or three key words from the follow-up, and read each likely match with `baton issue read <M>`.

- An open issue that covers the same thing gets a status comment instead of a new issue
  (`baton comment issue <M> orchestrator status <file>`): what the new source adds, and "see #N"
  for where it came from. Nothing is filed.
- A closed issue that covers it is linked ("see #M") in the new issue's Context, and the issue
  is filed only when the follow-up shows the closed one did not settle it.

## Check the claim

Before filing, check the follow-up's central claim ("X currently does Y", "Z was not measured")
against the code on the default branch (`rg`, or `git show origin/<default>:<path>`) or against
the findings comment it came from (`baton issue read <N>`). If you cannot check it in a
few commands, write in the issue's Context that the claim is **unverified** and name its source
("per the developer of #N"). Do not repeat an agent's claim as fact.

End the follow-up's Context with a line `Checked: <what was compared, for example a function on
origin/<default> at <short sha>, or the findings comment URL>`, or `Checked: unverified (per
<source>)` when it could not be checked in a few commands. A follow-up whose claim the code or
the findings contradict is not filed.

## Placement

Place each by its severity (`critical`, `important`, `minor`; the review's own words). Read
`follow_ups` from `baton config` (`follow_ups.scheduled` in a scheduled run) and use the
placement it names for the severity:

- `deliverable` or `stretch`: joins the open sprint with that label
  (`gh issue edit <N> --milestone "<title>"`), is refined with `baton:refine` as a subagent
  at once, and is dispatched once ready.
- `goal`: joins as `stretch` only when it serves the sprint goal; otherwise it is filed in the
  backlog.
- `backlog`: filed in `status:refinement`, outside the sprint, for the maintainer to prioritise.
- `skip`: not filed.

A developer's suggestion has no severity. Treat it as `important` if it names a security,
privacy, data-loss or performance problem; otherwise as `minor`. A spike's proposed follow-up is
placed as `important`. With no open sprint, every placement except `skip` files to the backlog.
A rule in the milestone description overrides the brief for that one sprint.
