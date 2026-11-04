import json

REMINDER = ("A compaction just happened. If you are the Baton sprint orchestrator "
            "(the baton-orchestrator agent), run `baton sprint-status` before acting. "
            "Otherwise ignore this.")


def main(stdin_text):
    try:
        data = json.loads(stdin_text)
        if isinstance(data, dict) and data.get("source") == "compact":
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                     "additionalContext": REMINDER}}))
    except Exception:
        return 0
    return 0
