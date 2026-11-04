"""PreToolUse hook: block a spike researcher's tools once its token limit is spent.

The limit is the `Timebox:` line in the researcher's dispatch prompt, the first user
record of its own transcript. Tokens are the weighted figure `baton usage` reports.
Exit code 2 blocks one tool call; the hook fails open on any error.
"""
import json
import os
import pathlib
import re
import sys
import tempfile

from . import issue, usage


def _transcript(payload):
    path = str(payload["transcript_path"])
    if path.endswith(".jsonl"):
        path = path[:-len(".jsonl")]
    return pathlib.Path(path) / "subagents" / f"agent-{payload['agent_id']}.jsonl"


def _state_path(agent_id):
    agent_id = str(agent_id)
    if not re.fullmatch(r"[\w.-]+", agent_id):
        return None
    return os.path.join(tempfile.gettempdir(), f"baton-budget-{agent_id}.json")


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) else None


def _save(path, state):
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=os.path.basename(path) + ".")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, path)
    except (OSError, ValueError):
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


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
        state_file = _state_path(payload["agent_id"])
        state = _load(state_file) if state_file else None
        if state and state.get("path") == str(path) and isinstance(state.get("prompt"), str):
            box = issue.timebox(state["prompt"])
            if not (box and box["tokens"]):
                return 0
        prompt, used, state = usage.tally(path, state)
        if state_file:
            _save(state_file, state)
        box = issue.timebox(prompt or "")
        limit = box and box["tokens"]
        if not limit:
            return 0
        if used < limit or _is_comment(payload):
            return 0
        message = (f"Token budget reached ({used:.0f} of {limit} weighted tokens). Post your findings "
                   'so far with baton comment, headed "Timebox reached", then return.')
    except Exception:
        return 0
    print(message, file=sys.stderr)
    return 2
