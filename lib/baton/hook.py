"""The one PreToolUse hook: the publish gate and the spike token budget in one process."""
from . import budget, gate


def main(stdin_text):
    code = gate.main(stdin_text)
    try:
        other = budget.main(stdin_text)
    except Exception:
        other = 0
    return 2 if 2 in (code, other) else 0
