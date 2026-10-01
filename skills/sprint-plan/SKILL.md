---
name: sprint-plan
description: Use when a Baton sprint needs to be planned, with /baton:sprint-plan, when asked to pick issues for a sprint or compile issues into a milestone, or when /baton:sprint finds no open sprint.
---

# Baton sprint-plan

Turn a goal into a sprint: a milestone holding the issues to deliver. The maintainer is the product
manager and decides what gets built; you prepare the proposal.

Follows GitLab's [plan phase](https://handbook.gitlab.com/handbook/product-development/how-we-work/product-development-flow/#build-phase-1-plan).
Commands are in `${CLAUDE_PLUGIN_ROOT}/references/github.md`.

## Steps

1. **Check the ground.** Run `baton config` and `baton sprint-status`. If `.baton/brief.md` is
   missing, stop and suggest `/baton:init`. If `open_sprints` is not empty, tell the maintainer
   which sprint is open and ask whether to continue it with `/baton:sprint` instead.

2. **Get the sprint's terms.** Take from the request whatever it already says; ask the maintainer
   only for what is missing:
   - the **goal**, in one or two sentences;
   - how **long** it should run (this becomes the milestone's due date);
   - the **parallel limit** (default: `developer.max_parallel` from the brief).

3. **Read the backlog.** List open issues with `status:ready`, and separately those in
   `status:refinement`. Read the last two files in `docs/retros/` if they exist and apply their
   "How to improve" items.

4. **Choose.** From the ready issues, pick those that serve the goal.
   - Order them: dependencies first (`Blocked by:`), then what the goal needs most.
   - Mark each `deliverable` (the sprint commits to it) or `stretch` (if time allows).
   - Prefer issues whose `Expected files:` do not overlap, so developers can work in parallel;
     where they overlap, say so, because those will run one after another.
   - There are no size estimates. Say plainly how many issues you propose and why that many fits
     the length; the maintainer adjusts.
   - If an issue that the goal clearly needs is still in refinement, offer to run `/baton:refine`
     on it now. Never put an issue that is not ready into the sprint.
   - Leave out issues written by anyone other than the maintainer unless the maintainer marked
     them ready.

5. **Propose.** Show the maintainer a table: order, issue, title, deliverable or stretch,
   blockers, overlaps. Under it list what you left out and why. Wait for approval or changes.
   **Do not create anything before the maintainer says yes.**

6. **Create the sprint**, once approved.
   - Title: `Sprint <YYYY-MM-DD>-<n>`, where the date is today and `n` makes it unique.
   - Description, exactly these lines so later sessions can read them back:

         Goal: <goal>
         Parallel: <limit>
         Order: #<a>, #<b>, #<c>

   - Create the milestone with its due date
     (`gh api repos/{owner}/{repo}/milestones -f title=... -f description=... -f due_on=<ISO date>`).
   - Add each issue to it and set its `deliverable` or `stretch` label
     (`gh issue edit <N> --milestone "<title>" --add-label deliverable`).
   - Post one `plan` comment on the first issue listing the sprint, so there is a record:
     `baton comment issue <N> orchestrator plan <file>`.

7. **Hand over.** Run `baton sprint-status` to confirm the sprint reads back correctly, then tell
   the maintainer it is ready and that `/baton:sprint` will work it.

## When there is no one to ask

If this run is unattended (the request says it is scheduled, or no maintainer is present), do
steps 1 to 4, then post the proposal instead of creating the sprint:

- create an issue titled `Sprint proposal <date>` with the table as its description and the
  `needs-maintainer` label;
- stop. The maintainer approves by running `/baton:sprint-plan` or by creating the milestone.

## Never

- Create the milestone, or move issues into it, without the maintainer's approval.
- Add an issue that fails `baton ready-check`.
- Change what an issue asks for. Scope changes are refinement, with the maintainer deciding.
