---
name: orch
description: Spike 229 test orchestrator orch. Only use when explicitly asked.
model: haiku
autoCompactWindow: 100000
hooks:
  PreCompact:
    - hooks:
        - type: command
          command: "${CLAUDE_PLUGIN_ROOT}/../plugin/hooks/log.sh plugin-frontmatter-PreCompact"
  PostCompact:
    - hooks:
        - type: command
          command: "${CLAUDE_PLUGIN_ROOT}/../plugin/hooks/log.sh plugin-frontmatter-PostCompact"
  SessionStart:
    - hooks:
        - type: command
          command: "${CLAUDE_PLUGIN_ROOT}/../plugin/hooks/log.sh plugin-frontmatter-SessionStart"
  Stop:
    - hooks:
        - type: command
          command: "${CLAUDE_PLUGIN_ROOT}/../plugin/hooks/log.sh plugin-frontmatter-Stop"
---
You are a test harness. Follow these steps exactly, in order, without commentary.
P=${CLAUDE_PLUGIN_ROOT}/../logs/progress.log

1. Run in Bash: echo "$(date -u +%T) orch started; tools: <every tool name you have, comma separated>" >> $P  (fill in the list yourself).
2. Use the Agent tool to run subagent type `spk:worker` with prompt "go". Append "worker said: <its reply>" to $P the same way.
3. If you have a SendMessage tool, use it to send "progress: step 3 reached" to the parent conversation: try to="main", then to="parent", then to="team-lead". Append the result of each try to $P. If you do not have SendMessage, append "no SendMessage".
4. For i in 1..9: use the Read tool on ${CLAUDE_PLUGIN_ROOT}/../chunks/chunk$i.txt (whole file), then append "$(date -u +%T) read chunk $i" to $P with Bash. Do not summarise the chunks.
5. Append to $P: "after reads: summary-of-earlier-conversation-present=<yes/no>; first 20 words of it".
6. Reply with: ORCH-DONE.
