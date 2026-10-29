"""Generate the spike 229 harness: hooks.json, agent files and filler chunks."""
import json
import os
import random
import string

W = os.path.dirname(os.path.abspath(__file__))
EVENTS = ["PreCompact", "PostCompact", "SessionStart", "SubagentStart", "SubagentStop"]
R = "${CLAUDE_PLUGIN_ROOT}/.."  # resolved by Claude Code in agent bodies and hook commands
LOG = f"{R}/plugin/hooks/log.sh"

hooks = {"hooks": {e: [{"hooks": [{"type": "command",
                                   "command": f"\"{LOG}\" plugin-{e}"}]}] for e in EVENTS}}
with open(f"{W}/plugin/hooks/hooks.json", "w") as f:
    json.dump(hooks, f, indent=1)

fm_hooks = "".join(
    f"  {e}:\n    - hooks:\n        - type: command\n          command: \"{LOG} {{label}}-{e}\"\n"
    for e in ["PreCompact", "PostCompact", "SessionStart", "Stop"])

ORCH_BODY = f"""You are a test harness. Follow these steps exactly, in order, without commentary.
P={R}/logs/progress.log

1. Run in Bash: echo "$(date -u +%T) orch started; tools: <every tool name you have, comma separated>" >> $P  (fill in the list yourself).
2. Use the Agent tool to run subagent type `spk:worker` with prompt "go". Append "worker said: <its reply>" to $P the same way.
3. If you have a SendMessage tool, use it to send "progress: step 3 reached" to the parent conversation: try to="main", then to="parent", then to="team-lead". Append the result of each try to $P. If you do not have SendMessage, append "no SendMessage".
4. For i in 1..9: use the Read tool on {R}/chunks/chunk$i.txt (whole file), then append "$(date -u +%T) read chunk $i" to $P with Bash. Do not summarise the chunks.
5. Append to $P: "after reads: summary-of-earlier-conversation-present=<yes/no>; first 20 words of it".
6. Reply with: ORCH-DONE.
"""


def agent(name, window, label):
    head = f"---\nname: {name}\ndescription: Spike 229 test orchestrator {name}. Only use when explicitly asked.\nmodel: haiku\n"
    if window:
        head += f"autoCompactWindow: {window}\n"
    head += "hooks:\n" + fm_hooks.replace("{label}", label) + "---\n"
    return head + ORCH_BODY


with open(f"{W}/plugin/agents/orch.md", "w") as f:
    f.write(agent("orch", 100000, "plugin-frontmatter"))
with open(f"{W}/plugin/agents/worker.md", "w") as f:
    f.write("---\nname: worker\ndescription: Spike 229 nested worker.\nmodel: haiku\ntools: Bash\n---\nReply with exactly: WORKER-OK\n")
os.makedirs(f"{W}/proj/.claude/agents", exist_ok=True)
with open(f"{W}/proj/.claude/agents/orch-local.md", "w") as f:
    f.write(agent("orch-local", 100000, "project-frontmatter"))

random.seed(229)
words = ["".join(random.choice(string.ascii_lowercase) for _ in range(random.randint(3, 9)))
         for _ in range(5000)]
for c in range(1, 10):
    with open(f"{W}/chunks/chunk{c}.txt", "w") as f:
        for _ in range(700):
            f.write(" ".join(random.choice(words) for _ in range(10)) + "\n")
