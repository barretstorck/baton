---
name: worker3
description: Spike 229 nested worker 3 that asks for a larger window than its parent.
model: haiku
tools: Read
autoCompactWindow: 1000000
---
Read ${CLAUDE_PLUGIN_ROOT}/../chunks/chunk1.txt with limit 20, then reply with exactly: WORKER3-OK
