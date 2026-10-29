"""Summarise the spike hook log: event, agent id/type and source fields per line."""
import json
import sys

KEYS = ("hook_event_name", "agent_id", "agent_type", "source", "trigger", "agent_transcript_path")
for line in open(sys.argv[1]):
    t, label, rest = line.split(" ", 2)
    try:
        d = json.loads(rest)
    except ValueError:
        print(t, label, "unparsed")
        continue
    keep = {k: v for k, v in d.items() if k in KEYS}
    if "agent_transcript_path" in keep:
        keep["agent_transcript_path"] = keep["agent_transcript_path"][-30:]
    other = sorted(set(d) - set(KEYS) - {"session_id", "transcript_path", "cwd"})
    print(t, label, json.dumps(keep), other)
