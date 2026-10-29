#!/bin/sh
# Append the event label and the hook input JSON (one line) to the spike's hook log.
# For SessionStart after a compaction, also return additionalContext with a marker,
# to see which context (main or subagent) receives it.
d="$(dirname "$0")/../../logs"
input="$(cat)"
{ printf '%s %s ' "$(date -u +%H:%M:%S)" "$1"; printf '%s' "$input" | tr '\n' ' '; echo; } >> "$d/hooks.log"
case "$1:$input" in
  *SessionStart:*'"source":"compact"'*)
    printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"MARKER-229: a compaction just happened; run baton sprint-status before acting."}}' ;;
esac
exit 0
