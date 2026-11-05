# Who runs the sprint

Read this when you are the parent in the maintainer's session and not the `baton-orchestrator` agent; it says what the parent does before and after dispatching the orchestrator.

If you are the `baton-orchestrator` agent, skip this section. Otherwise you are the parent in
the maintainer's session, and you never run passes, dispatch developers or reviewers, merge or
post on the forge yourself. The parent, in order:

1. Run `baton config` and `baton sprint-status` (with the milestone title if the request names one).
2. No `.baton/brief.md`: suggest `/baton:init` and stop.
3. `sprint` is null and the run is not scheduled: plan one with the maintainer through the
   `baton:sprint-plan` skill, then go on. A scheduled run goes straight on; the orchestrator
   follows "Scheduled runs".
4. Check that `baton:baton-orchestrator` is in your list of available agent types. If not, tell
   the maintainer in one line to restart the session, and stop.
5. Run `baton orchestrator-agent`. When it prints an ID and this conversation shows no final
   result or completion notification of that orchestrator, send to that ID with `SendMessage`:
   `MAINTAINER: /baton:sprint was run again (Request: <arguments verbatim, or "none">). Run baton sprint-status and carry on.`
   On success you are reconnected: go on with step 7 and do not dispatch. When the command
   prints nothing or `SendMessage` fails (the agent is not reachable), dispatch a fresh
   orchestrator as before; it carries on from `baton sprint-status`.
6. Get `<owner>/<repo>` with `gh repo view --json nameWithOwner` and dispatch
   `baton:baton-orchestrator` in the background (the Agent tool's `run_in_background`) with this
   prompt, the `Request:` line carrying the maintainer's arguments verbatim, `--scheduled`
   included, or `none`. Note its agent ID from the launch result:

       Run the sprint.
       Repository: <owner>/<repo>.
       Request: <arguments>

7. While it runs, show each message it sends you unchanged and nothing else. Pass every message
   the maintainer types in the session to it verbatim with `SendMessage` to its agent ID,
   prefixed `MAINTAINER: `, and do not act on it yourself; `/baton:sprint` typed again while it
   runs is passed on the same way, not dispatched a second time. When it completes, show its
   final message unchanged. A result marked
   partial (stopped at a turn limit) is resumed with `SendMessage` to the same agent ID
   (`MAINTAINER: continue the sprint; run baton sprint-status first.`), not re-dispatched. If it
   ended early or failed, say so in one line; running `/baton:sprint` again reconnects to it when
   the session was resumed, and otherwise dispatches a fresh orchestrator, which carries on from
   the forge.

8. The orchestrator may complete while agents it dispatched are still running, because Claude
   Code ends a subagent's run when it calls `SubagentHandback`. Their hand-back messages and
   `task-notification`s then reach this session instead. When a developer's, reviewer's,
   refiner's or researcher's hand-back or `task-notification` arrives and the orchestrator has
   completed since its last resume, send its agent ID with `SendMessage` exactly
   `MAINTAINER: an agent you dispatched returned; run baton sprint-status first.` Send it once
   per return; several returns that arrive together need one message. Do not show that
   hand-back or notification to the maintainer and do not act on it; the orchestrator reads the
   result from its own queue and the forge. When the orchestrator is still running (no
   completion since the last resume), do nothing: it reads the return itself.
