"""PreToolUse hook: block a spike researcher's tools once its token limit is spent.

The limit is the `Timebox:` line in the researcher's dispatch prompt, the first user
record of its own transcript. Tokens are the weighted figure `baton usage` reports.
Exit code 2 blocks one tool call; the hook fails open on any error.
"""
import json
import pathlib
import sys

from . import issue, usage


def _transcript(payload):
    path = str(payload["transcript_path"])
    if path.endswith(".jsonl"):
        path = path[:-len(".jsonl")]
    return pathlib.Path(path) / "subagents" / f"agent-{payload['agent_id']}.jsonl"


def _is_comment(payload):
    if payload.get("tool_name") != "Bash":
        return False
    command = (payload.get("tool_input") or {}).get("command")
    return isinstance(command, str) and command.lstrip(" ").startswith("baton comment")


def main(stdin_text):
    try:
        payload = json.loads(stdin_text)
        if not isinstance(payload, dict) or not payload.get("agent_id") or not payload.get("transcript_path"):
            return 0
        path = _transcript(payload)
        if not path.is_file():
            return 0
        prompt, calls, _ = usage._read(path, set())
        box = issue.timebox(prompt or "")
        limit = box and box["tokens"]
        if not limit:
            return 0
        used = sum(call["weighted"] for call in calls)
        if used < limit or _is_comment(payload):
            return 0
        message = (f"Token budget reached ({used:.0f} of {limit} weighted tokens). Post your findings "
                   'so far with baton comment, headed "Timebox reached", then return.')
    except Exception:
        return 0
    print(message, file=sys.stderr)
    return 2
