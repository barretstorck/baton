"""Spike 161 prototype: stop a running subagent when its token budget is spent.

Wire as a PreToolUse hook (no matcher). The payload has `transcript_path` (the
parent session) and, inside a subagent, `agent_id`; the subagent's own
transcript is <parent transcript without .jsonl>/subagents/agent-<agent_id>.jsonl
and is written as the agent works. The limit comes from BATON_SPIKE_TOKENS.
"""
import json
import os
import pathlib
import sys

from . import usage


def transcript(payload):
    parent = pathlib.Path(payload["transcript_path"])
    return parent.with_suffix("") / "subagents" / f"agent-{payload['agent_id']}.jsonl"


def spent(path):
    """Weighted tokens of one transcript so far (de-duplicated by request id)."""
    _, calls, _ = usage._read(pathlib.Path(path), set())
    return sum(call["weighted"] for call in calls)


def main(stdin_text, limit=None):
    limit = limit or int(os.environ.get("BATON_SPIKE_TOKENS") or 0)
    try:
        payload = json.loads(stdin_text)
        if not limit or not payload.get("agent_id"):
            return 0
        used = spent(transcript(payload))
    except (ValueError, KeyError, OSError):
        return 0                      # fail open: a budget must not break every call
    if used < limit:
        return 0
    if payload.get("tool_name") == "Bash" and "baton comment" in json.dumps(payload.get("tool_input")):
        return 0                      # leave the agent a way to post its partial findings
    print(f"Token budget reached ({used:,.0f} of {limit:,} weighted tokens). Stop now: post your "
          "findings so far with `baton comment`, then return.", file=sys.stderr)
    return 2
