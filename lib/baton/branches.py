"""Find the pushed issue-<N>-* branch that a detached checkout stands on.

Used by `baton pr create` and by the gate's `gh pr create` rewrite, which both
need a `--head <branch>` because a detached HEAD has no current branch.
"""
import re

from baton import run

BRANCH_NAME = re.compile(r"[A-Za-z0-9._/-]+")
ISSUE_BRANCH = re.compile(r"issue-\d+-[A-Za-z0-9._/-]+")
_PREFIX = "refs/remotes/origin/"


def detached(runner, cwd):
    try:
        out = runner(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    except run.RunError:
        return False
    return out.strip() == "HEAD"


def pushed_branches_at_head(runner, cwd):
    try:
        out = runner(["git", "for-each-ref", "--points-at", "HEAD",
                      "--format=%(refname)", "refs/remotes/origin"], cwd=cwd)
    except run.RunError:
        return []
    names = set()
    for line in out.splitlines():
        line = line.strip()
        if line.startswith(_PREFIX):
            name = line[len(_PREFIX):]
            if ISSUE_BRANCH.fullmatch(name):
                names.add(name)
    return sorted(names)


def record_push(runner, cwd, refspec, sha):
    """Move refs/remotes/origin/<branch> after a push to a URL."""
    if not sha:
        return
    _, sep, dst = refspec.partition(":")
    if not sep or not dst.startswith("refs/heads/") or len(dst) == len("refs/heads/"):
        return
    try:
        runner(["git", "update-ref", _PREFIX + dst[len("refs/heads/"):], sha], cwd=cwd)
    except run.RunError:
        pass
