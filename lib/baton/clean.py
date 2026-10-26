"""`baton clean`: remove leftover agent worktrees and stale verification logs."""
import json
import re

from . import run

WORKTREES_DIR = ".claude/worktrees"


def _entries(runner):
    """Every entry of `git worktree list --porcelain` as a dict."""
    entries, cur = [], None
    for line in runner(["git", "worktree", "list", "--porcelain"]).splitlines() + [""]:
        if line.startswith("worktree "):
            cur = {"path": line[len("worktree "):], "sha": None, "branch": None,
                   "detached": False, "locked": False}
        elif cur is None:
            continue
        elif line.startswith("HEAD "):
            cur["sha"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            ref = line[len("branch "):]
            cur["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        elif line == "detached":
            cur["detached"] = True
        elif line.startswith("locked"):
            cur["locked"] = True
        elif line == "":
            entries.append(cur)
            cur = None
    return entries


def agent_worktrees(runner):
    """Worktrees under `<repo root>/.claude/worktrees/`; the repo root is the first entry."""
    entries = _entries(runner)
    if not entries:
        return []
    prefix = entries[0]["path"].rstrip("/") + "/" + WORKTREES_DIR + "/"
    return [e for e in entries[1:] if e["path"].startswith(prefix)]


def _keep_reason(wt, runner, default_branch):
    """Why the worktree must stay, or None when it can go."""
    match = re.match(r"issue-(\d+)-", wt["branch"] or "")
    if match:
        state = json.loads(runner(["gh", "issue", "view", match.group(1),
                                   "--json", "state"]))["state"]
        if state == "OPEN":
            return f"issue #{match.group(1)} is open"
    if runner(["git", "-C", wt["path"], "status", "--porcelain"]).strip():
        return "it has uncommitted changes"
    if wt["detached"]:
        try:
            runner(["git", "merge-base", "--is-ancestor", wt["sha"], f"origin/{default_branch}"])
        except run.RunError:
            return f"its commit is not in {default_branch}"
    return None


def clean_worktrees(runner, default_branch, report):
    """Remove leftover agent worktrees; returns (removed, kept, failed) lists of paths."""
    removed, kept, failed = [], [], []
    for wt in agent_worktrees(runner):
        reason = _keep_reason(wt, runner, default_branch)
        if reason:
            kept.append(wt["path"])
            report(f"kept {wt['path']} : {reason}")
            continue
        force = ["--force", "--force"] if wt["locked"] else ["--force"]
        runner(["git", "worktree", "remove", *force, wt["path"]])
        removed.append(wt["path"])
        report(f"removed {wt['path']}")
    return removed, kept, failed
