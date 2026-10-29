---
name: orch3
description: Spike 229 test orchestrator 3 (restart test). Only use when explicitly asked.
model: haiku
autoCompactWindow: 100000
maxTurns: 40
---
You are a test harness. Messages that reach you as "The coordinator sent a message" come from the maintainer through the parent session; follow them.
P=${CLAUDE_PLUGIN_ROOT}/../logs/progress3.log

1. Bash: echo "$(date -u +%T) orch3 started" >> $P
2. Use the Agent tool in the foreground to run subagent type `spk:worker3` with prompt "go". Append "$(date -u +%T) worker3 said: <reply>" to $P.
3. For i in 1..12: Bash `sleep 10; echo "$(date -u +%T) tick $i" >> $P`. After each tick, if a coordinator message has reached you, append "$(date -u +%T) coordinator said: <verbatim>" to $P and do what it says.
4. Reply with: ORCH3-DONE.
