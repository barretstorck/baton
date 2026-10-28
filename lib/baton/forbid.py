"""Forbidden diff patterns: the brief's `forbid_added` rules checked against added lines."""
import fnmatch
import json
import re

from . import checklist, githooks
from .run import RunError

_ALLOWS = re.compile(r'^\s*(?:[-*]\s*)?Allows:\s*forbid_added\s+"([^"]+)"\s*$', re.M)
_HIDDEN = re.compile(r"<!--.*?(?:-->|\Z)|^[ \t]*(`{3,}|~{3,}).*?(?:^[ \t]*\1[`~]*[ \t]*$|\Z)",
                     re.S | re.M)
_BRANCH = re.compile(r"^issue-(\d+)(?:-|$)")


def allowed_globs(plan_text):
    """Globs a plan allows with `Allows: forbid_added "<glob>"` lines."""
    return set(_ALLOWS.findall(plan_text or ""))


def violations(patch, rules, allowed=()):
    """One message per added line that matches a rule whose glob is not allowed."""
    found = [f"cannot parse the diff header, so its added lines are not checked: {header}"
             for header in githooks.unparsable_headers(patch)]
    for path, added in githooks.added_by_file(patch):
        for glob, pattern in rules.items():
            if glob in allowed or not fnmatch.fnmatch(path, glob):
                continue
            regex = re.compile(pattern)
            for line in added.split("\n"):
                if regex.search(line):
                    found.append(f"forbidden added line in {path}: {line.strip()}")
    return found


def branch_issue(branch):
    """The issue number of a branch named `issue-<N>-...`, or None."""
    match = _BRANCH.match(branch or "")
    return int(match.group(1)) if match else None


def plan_text(runner, cfg, branch, number=None):
    """The `## Developer Plan` section of issue `number`'s body (default: the issue the branch
    `issue-<N>-...` names), without HTML comments and code fences, or "" when there is none,
    the issue cannot be read or its author is not the repository owner."""
    if number is None:
        number = branch_issue(branch)
    if number is None:
        return ""
    try:
        issue = json.loads(runner(["gh", "issue", "view", str(number), "--json", "author,body"]))
        owner = json.loads(runner(["gh", "repo", "view", "--json", "owner"]))["owner"]["login"]
        if issue["author"]["login"] != owner:
            return ""
        body = issue["body"] or ""
    except (RunError, ValueError, KeyError, TypeError):
        return ""
    found = checklist.span(body, "developer plan")
    return _HIDDEN.sub("", body[found[0]:found[1]]) if found else ""
