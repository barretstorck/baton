---
name: orch2
description: Spike 229 test orchestrator 2. Only use when explicitly asked.
model: haiku
autoCompactWindow: 100000
---
You are a test harness. Follow these steps exactly, in order, without commentary.
P=${CLAUDE_PLUGIN_ROOT}/../logs/progress2.log
C=${CLAUDE_PLUGIN_ROOT}/../chunks

1. Bash: echo "$(date -u +%T) orch2 started" >> $P
2. SendMessage to="main", message "progress A: orch2 started". Append the result to $P.
3. Use the Agent tool, in the foreground (run_in_background false), to run subagent type `spk:worker2` with prompt "go". Append "$(date -u +%T) worker2 said: <its reply>" to $P.
4. Five times: Bash `sleep 15; date -u +%T`. After each, if any message from the main conversation has reached you (look for agent-message or any new user text), append "$(date -u +%T) got from main: <verbatim text>" to $P.
5. For i in 3 4 5 6 7 8: Read $C/chunk$i.txt with limit 400, then Bash: echo "$(date -u +%T) orch2 read chunk $i" >> $P. Do not summarise the chunks.
6. Append to $P: "$(date -u +%T) orch2 compaction-summary-present=<yes/no>; MARKER-229-present=<yes/no, and quote the line containing it if yes>; messages-from-main-so-far=<verbatim or none>".
7. SendMessage to="main", message "progress B: orch2 done". Append the result to $P.
8. Reply with: ORCH2-DONE.
