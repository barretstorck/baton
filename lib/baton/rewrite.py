"""Spike #222 prototype: complete a raw forge command instead of refusing it.

The gate calls `plan(command, cwd, runner, agent)` after its scan found no problem. The
result is one of:

- `None`: nothing to add; the command runs as the agent wrote it.
- `("rewrite", new_command, note)`: the gate returns `updatedInput` with `new_command`
  and `note` as `additionalContext`, so the agent learns what ran.
- `("refuse", message)`: the gate cannot complete the command safely; `message` says
  exactly what to type instead.

The only rule so far: `gh pr create` from a detached HEAD gets `--head <branch>`
(issue #216). The branch is the one local or `origin` branch whose tip is HEAD.
"""
import re
import shlex

from . import run as _run

# Claude Code applies permission rules and safety checks to a hook's rewritten input
# from 2.1.290 (changelog: "Fixed some permission rules and safety checks not being
# applied to a tool call after a PreToolUse hook rewrote its input").
MIN_VERSION = (2, 1, 290)

_SEPARATORS = re.compile(r"[;&|\n`]|\$\(")
_QUOTED = re.compile(r"""'[^']*'|"[^"]*\"""")
_PR_CREATE = re.compile(r"(?:^|[;&|(\n])\s*((?:\S*/)?gh\s+pr\s+create)\b")
_HEAD_FLAG = re.compile(r"(?<![\w-])(?:--head|-H)(?:\s|=|$)")


def claude_version(agent):
    """(major, minor, patch) from AI_AGENT, for example `claude-code_2-1-296_agent`."""
    match = re.search(r"claude-code_(\d+)-(\d+)-(\d+)", agent or "")
    return tuple(int(part) for part in match.groups()) if match else None


def _simple(command):
    """True when the command is one plain command: no separators outside quotes."""
    return not _SEPARATORS.search(_QUOTED.sub('""', command))


def _branch_at_head(cwd, runner):
    """The one branch whose tip is HEAD when HEAD is detached; '' when attached; None when unclear."""
    try:
        runner(["git", "symbolic-ref", "-q", "HEAD"], cwd=cwd)
        return ""
    except _run.RunError:
        pass
    try:
        out = runner(["git", "for-each-ref", "--points-at", "HEAD", "--format=%(refname)",
                      "refs/heads", "refs/remotes/origin"], cwd=cwd)
    except _run.RunError:
        return None
    names = set()
    for ref in out.split():
        if ref.startswith("refs/heads/"):
            names.add(ref[len("refs/heads/"):])
        elif ref.startswith("refs/remotes/origin/") and not ref.endswith("/HEAD"):
            names.add(ref[len("refs/remotes/origin/"):])
    return names.pop() if len(names) == 1 else None


def plan(command, cwd, runner=_run.run, agent=None):
    match = _PR_CREATE.search(command)
    if not match or _HEAD_FLAG.search(_QUOTED.sub('""', command)):
        return None
    branch = _branch_at_head(cwd, runner)
    if branch == "":
        return None
    fix = "Push the branch, then run the same command with `--head <branch>` added after `gh pr create`."
    if branch is None:
        return ("refuse", "`gh pr create` from a detached HEAD needs `--head <branch>`, and no single "
                          "branch points at HEAD. " + fix)
    if not _simple(command):
        return ("refuse", f"`gh pr create` from a detached HEAD needs `--head {branch}`. "
                          f"Add `--head {shlex.quote(branch)}` after `gh pr create` and run it again.")
    version = claude_version(agent)
    if version is None or version < MIN_VERSION:
        return ("refuse", f"`gh pr create` from a detached HEAD needs `--head {branch}`, and this Claude "
                          "Code cannot have Baton add it. "
                          f"Add `--head {shlex.quote(branch)}` after `gh pr create` and run it again.")
    new = command[:match.end(1)] + f" --head {shlex.quote(branch)}" + command[match.end(1):]
    note = f"Baton added `--head {branch}` to `gh pr create` (HEAD is detached). The command that ran: {new}"
    return ("rewrite", new, note)
