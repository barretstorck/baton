"""Forbidden diff patterns: the brief's `forbid_added` rules checked against added lines."""
import fnmatch
import re

from . import githooks
from .run import RunError

_ALLOWS = re.compile(r'^\s*(?:[-*]\s*)?Allows:\s*forbid_added\s+"([^"]+)"\s*$', re.M)
_BRANCH = re.compile(r"^issue-(\d+)(?:-|$)")


def allowed_globs(plan_text):
    """Globs a plan allows with `Allows: forbid_added "<glob>"` lines."""
    return set(_ALLOWS.findall(plan_text or ""))


def violations(patch, rules, allowed=()):
    """One message per added line that matches a rule whose glob is not allowed."""
    found = []
    for path, added in githooks.added_by_file(patch):
        for glob, pattern in rules.items():
            if glob in allowed or not fnmatch.fnmatch(path, glob):
                continue
            regex = re.compile(pattern)
            for line in added.split("\n"):
                if regex.search(line):
                    found.append(f"forbidden added line in {path}: {line.strip()}")
    return found


def plan_text(runner, cfg, branch):
    """The plan file for the issue a branch `issue-<N>-...` belongs to, or "" if none."""
    match = _BRANCH.match(branch or "")
    if not match:
        return ""
    number = match.group(1)
    folder = f"{cfg.data['docs']['dir']}/plans/"
    for ref in (f"refs/plans/{number}", f"refs/baton/plans/{number}"):
        try:
            names = runner(["git", "ls-tree", "--name-only", ref, folder]).split()
            name = next(n for n in names if n.rsplit("/", 1)[-1].startswith(f"{number}-"))
            return runner(["git", "show", f"{ref}:{name}"])
        except (RunError, StopIteration):
            continue
    return ""
