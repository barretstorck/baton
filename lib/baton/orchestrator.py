"""Find the current Claude Code session's orchestrator subagent (issue #248).

Claude Code writes `<project>/<session id>/subagents/agent-<id>.meta.json` with the
agent's type, so a resumed session finds the orchestrator's ID without a file of ours.
"""
import json
import pathlib

AGENT_TYPE = "baton:baton-orchestrator"


def find_agent(root, session_id):
    """The ID of the session's most recently active orchestrator, or None."""
    if not session_id:
        return None
    best = None
    for meta in pathlib.Path(root).expanduser().glob(f"*/{session_id}/subagents/agent-*.meta.json"):
        try:
            if json.loads(meta.read_text()).get("agentType") != AGENT_TYPE:
                continue
            transcript = meta.with_name(meta.name[:-len(".meta.json")] + ".jsonl")
            when = (transcript if transcript.exists() else meta).stat().st_mtime
        except (OSError, ValueError, AttributeError):
            continue
        if best is None or when > best[0]:
            best = (when, meta.name[len("agent-"):-len(".meta.json")])
    return best[1] if best else None
