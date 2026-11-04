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
5. Get `<owner>/<repo>` with `gh repo view --json nameWithOwner` and dispatch
   `baton:baton-orchestrator` in the background (the Agent tool's `run_in_background`) with this
   prompt, the `Request:` line carrying the maintainer's arguments verbatim, `--scheduled`
   included, or `none`. Note its agent ID from the launch result:

       Run the sprint.
       Repository: <owner>/<repo>.
       Request: <arguments>

6. While it runs, show each message it sends you unchanged and nothing else. Pass every message
   the maintainer types in the session to it verbatim with `SendMessage` to its agent ID,
   prefixed `MAINTAINER: `, and do not act on it yourself; `/baton:sprint` typed again while it
   runs is passed on the same way, not dispatched a second time. When it completes, show its
   final message unchanged. If it ended early,
   failed or hit a limit, say so in one line; running `/baton:sprint` again dispatches a fresh
   orchestrator, which carries on from the forge.
