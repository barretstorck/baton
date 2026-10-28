"""`baton clean`: remove leftover agent worktrees and stale verification logs."""
import json
import os
import pwd
import re
import shutil

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
            cur["locked"] = line[len("locked"):].strip() or True
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


def _lock_owner_gone(lock):
    """True when a lock reason ends in `(pid N)` and no process N is alive."""
    match = re.search(r"\(pid (\d+)\)\s*$", lock) if isinstance(lock, str) else None
    if not match:
        return False
    try:
        os.kill(int(match.group(1)), 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def _keep_reason(wt, runner, default_branch):
    """Why the worktree must stay, or None when it can go."""
    if wt["locked"] and not _lock_owner_gone(wt["locked"]):
        return "it is locked"
    match = re.match(r"issue-(\d+)-", wt["branch"] or "")
    if match:
        state = json.loads(runner(["gh", "issue", "view", match.group(1),
                                   "--json", "state"]))["state"]
        if state == "OPEN":
            return f"issue #{match.group(1)} is open"
    if wt["detached"]:
        try:
            runner(["git", "merge-base", "--is-ancestor", wt["sha"], f"origin/{default_branch}"])
        except run.RunError:
            return f"its commit is not in {default_branch}"
    return None


def foreign_owner(path):
    """First file under `path` owned by another user, as (file, user), or None."""
    me = os.getuid()
    for top, dirs, files in os.walk(path):
        for name in dirs + files:
            full = os.path.join(top, name)
            try:
                uid = os.lstat(full).st_uid
            except OSError:
                continue
            if uid != me:
                try:
                    user = pwd.getpwuid(uid).pw_name
                except KeyError:
                    user = str(uid)
                return full, user
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
        try:
            runner(["git", "worktree", "remove", *force, wt["path"]])
        except run.RunError:
            failed.append(wt["path"])
            owner = foreign_owner(wt["path"])
            if owner:
                report(f"cannot remove {wt['path']}: {owner[0]} is owned by {owner[1]}")
            else:
                report(f"cannot remove {wt['path']}: its directory could not be removed")
            continue
        removed.append(wt["path"])
        report(f"removed {wt['path']}")
    return removed, kept, failed


def clean_logs(runner, logs_root, report):
    """Remove `pr-<PR>` log folders whose pull request is closed or merged; returns (removed names, failed paths)."""
    removed, failed = [], []
    if not os.path.isdir(logs_root):
        return removed, failed
    for name in sorted(os.listdir(logs_root)):
        match = re.fullmatch(r"pr-(\d+)", name)
        path = os.path.join(logs_root, name)
        if not match or not os.path.isdir(path):
            continue
        try:
            state = json.loads(runner(["gh", "pr", "view", match.group(1),
                                       "--json", "state"]))["state"]
        except (run.RunError, ValueError, KeyError):
            report(f"kept {path} : pull request state unknown")
            continue
        if state not in ("CLOSED", "MERGED"):
            report(f"kept {path} : pull request #{match.group(1)} is {state.lower()}")
            continue
        try:
            shutil.rmtree(path)
        except OSError:
            owner = foreign_owner(path)
            detail = f"{owner[0]} is owned by {owner[1]}" if owner else "it could not be removed"
            report(f"cannot remove {path}: {detail}")
            failed.append(path)
            continue
        removed.append(name)
        report(f"removed {path}")
    return removed, failed
