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
   - the **parallel limit** (default: `developer.max_parallel` from the brief). It goes into the
     milestone as `Parallel: <n>`, which `baton sprint-status` uses for the free slots.
     More developers at once finish the sprint sooner, but they burn the quota at a multiple of
     the rate and can hit a rate limit mid-work. The default is 1; ask the maintainer which
     to choose.

3. **Read the backlog.** List open issues with `status:ready`, and separately those in
   `status:refinement`. The issue list (`--json number,title,labels`) carries each issue's
   `priority:` and `severity:` label (names in `labels.map` of `baton config`); read them for
   every ready issue. Also list the open issues carrying `priority:1` or `priority:2`, whatever
   their status. Read the last two files in `<docs.dir>/retros/` (`docs.dir` from `baton config`, default `docs/baton`) if they exist and apply their
   "How to improve" items.

4. **Choose.** From the ready issues, pick those that serve the goal.
   - Order them: goals first. Within each goal, order by dependencies first (`Blocked by:`),
     then priority (1 first, unranked last), then severity (1 first, unranked last). Priority
     and severity never override dependencies or the goal. An issue without a priority label
     ranks after every ranked one; one without a severity label ranks after every one with a
     severity (a non-bug never has one).
   - Severity is ranking information only: it carries no due date and never pulls a bug into
     the sprint or interrupts it.
   - Mark each `deliverable` (the sprint commits to it) or `stretch` (if time allows).
   - Show where the issues' `Expected files:` overlap, because those pull requests are likely to
     conflict and the merge order matters.
   - There are no size estimates. The number of plan tasks is the nearest thing: read it with
     `baton tasks issue <N>` (each task is one developer and one test cycle). Say plainly how
     many issues and tasks you propose and why that many fits the length; the maintainer adjusts.
   - Check each with `baton ready-check <N>`; an issue without a plan (other than a spike) is not
     ready.
   - If an issue that the goal clearly needs is still in refinement, offer to refine it now
     (`/baton:refine`, or `baton:baton-refiner` agents with at most about three issues each). Never put an issue that is not ready into the sprint.
   - Leave out outside issues (written by anyone other than a maintainer, or with an
     `Origin: outside (@<login>)` line) that `baton ready-check` reports as lacking a maintainer's
     `/baton ready` comment.

5. **Propose.** Show the maintainer a table: order, issue, title, priority, severity,
   deliverable or stretch, plan tasks, blockers, overlaps (show `-` where a priority or
   severity is unset). Under it list what you left out and why. After that, add a separate list
   titled "Urgent or high priority, not in this sprint": every open `priority:1` and
   `priority:2` issue that matches no goal of this sprint, each with the reason it is left out
   (not serving the goal, not ready, blocked by an open issue, written by someone else, and so on).
   Also give one line with the mix: the count and percentage of the proposed issues by type
   (feature, maintenance, bug), counting by the `type:` labels named in `labels.map` of
   `baton config`; spikes are counted separately and are not part of the percentages. When
   `work_mix` in `baton config` is not empty, add the target next to each type and the difference
   in percentage points. The mix is information only: it never blocks, holds back or changes a plan.
   Wait for approval or changes.
   **Do not create anything before the maintainer says yes.**

6. **Create the sprint**, once approved.
   - Title: `Sprint <YYYY-MM-DD>-<n>`, where the date is today and `n` makes it unique.
   - Description, exactly these lines so later sessions can read them back:

         Goal: <goal>
         Parallel: <limit>
         Order: #<a>, #<b>, #<c>

   - Create the milestone with its due date
     (`baton milestone create --title "<title>" --description "<text>" --due-on <ISO date>`).
   - Add each issue to it and set its `deliverable` or `stretch` label
     (`baton issue edit <N> --milestone "<title>" --add-label deliverable`).
   - Post one `plan` comment on the first issue listing the sprint, so there is a record:
     `baton comment issue <N> orchestrator plan <file>`.

7. **Hand over.** Run `baton sprint-status` to confirm the sprint reads back correctly, then tell
   the maintainer it is ready and that `/baton:sprint` will work it.

## When there is no one to ask

If this run is unattended (the request says it is scheduled, or no maintainer is present), do
steps 1 to 4, then post the proposal instead of creating the sprint:

- create an issue titled `Sprint proposal <date>` with the table as its description and the
  `needs-maintainer` label, with the same mix line and the same "Urgent or high priority, not in
  this sprint" list under the table;
- stop. The maintainer approves by running `/baton:sprint-plan` or by creating the milestone.

## Never

- Create the milestone, or move issues into it, without the maintainer's approval.
- Add an issue that fails `baton ready-check`.
- Use severity to set a due date, or to add a bug to the sprint on its own.
- Change what an issue asks for. Scope changes are refinement, with the maintainer deciding.
