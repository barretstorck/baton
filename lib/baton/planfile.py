"""A plan kept as a file in the repository, linked from the issue's first line.

Design spec, "plans in the repo": the issue body starts with a `Plan:` link to the file on
the issue branch, or at a commit once the branch is gone.
"""
import json
import os
import re
import shutil
import tempfile

from . import checklist
from .config import STATUSES
from .run import RunError

_LINK = re.compile(r"^Plan:\s*\[(?P<path>[^\]]+)\]\((?P<url>[^)\s]+)\)\s*$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_PLAN_LINE = re.compile(r"^Plan:.*$", re.M)


def plan_path(cfg, number, slug):
    return f"{cfg['docs']['dir']}/plans/{number}-{slug}.md"


def link_line(repo, ref, path):
    return f"Plan: [{path}](https://github.com/{repo}/blob/{ref}/{path})"


def parse_link(body):
    """{'ref', 'path', 'pinned'} for the body's `Plan:` line, or None."""
    for line in (body or "").splitlines():
        match = _LINK.match(line.rstrip())
        if not match:
            continue
        path, url = match.group("path"), match.group("url")
        marker = url.find("/blob/")
        if marker < 0 or not url.endswith("/" + path):
            continue
        ref = url[marker + len("/blob/"):-len(path) - 1]
        if ref:
            return {"ref": ref, "path": path, "pinned": bool(_SHA.match(ref))}
    return None


def set_link(body, line):
    body = body or ""
    if _PLAN_LINE.search(body):
        return _PLAN_LINE.sub(lambda _: line, body, count=1)
    return f"{line}\n\n{body}"


def read(runner, ref, path):
    """Text of `path` on branch `ref` (fetched first) or at pinned commit `ref`."""
    try:
        if _SHA.match(ref):
            return runner(["git", "show", f"{ref}:{path}"])
        runner(["git", "fetch", "--quiet", "origin", ref])
        return runner(["git", "show", f"origin/{ref}:{path}"])
    except RunError as error:
        raise checklist.TaskError(f"plan file {path} not found at {ref}: {error}")


def link_issue(runner, repo, number, branch=None, path=None, at=None):
    """Write or replace the issue's `Plan:` line; returns the new line.

    With `at`, the link is pinned to that commit and keeps the path of the existing link.
    """
    body = checklist.issue_body(runner, number)
    if at is not None:
        if not _SHA.match(at):
            raise checklist.TaskError(f"--at needs a full 40-character commit sha, got {at!r}")
        existing = parse_link(body)
        if existing is None:
            raise checklist.TaskError(f"issue #{number} has no Plan: link to pin")
        ref, path = at, existing["path"]
    elif branch and path:
        ref = branch
    else:
        raise checklist.TaskError("plan link needs a branch and a path, or --at <sha>")
    line = link_line(repo, ref, path)
    new = set_link(body, line)
    if new != body:
        checklist._save_issue(runner, number, new)
    return line


def _remove_sections(body, names):
    """`body` without the named level-2 sections (heading included)."""
    cuts = []
    for name in names:
        found = checklist.span(body, name)
        if found is None:
            continue
        start = next(s for _, n, s, e in checklist._headings(body) if e == found[0] and n == name)
        cuts.append((start, found[1]))
    for start, end in sorted(cuts, reverse=True):
        body = body[:start] + body[end:]
    return body


def _split_pointers(body):
    """(pattern text, 'Expected files:'/'Blocked by:' lines) of the Pointers section."""
    found = checklist.span(body, "pointers")
    if found is None:
        return "", []
    keep, pattern = [], []
    for line in body[found[0]:found[1]].splitlines():
        if re.match(r"^\s*(Expected files|Blocked by):", line, re.I):
            keep.append(line.strip())
        else:
            pattern.append(line)
    return "\n".join(pattern).strip(), keep


def split_legacy(body):
    """(plan file text, trimmed body without the link) from a legacy issue body."""
    verification = checklist.span(body, "verification")
    plan = checklist.span(body, "plan")
    if plan is None:
        raise checklist.TaskError("the description has no '## Plan' section to migrate")
    pattern, kept = _split_pointers(body)
    verification_text = body[verification[0]:verification[1]].strip() if verification else ""
    plan_text = body[plan[0]:plan[1]].strip()
    file_text = ("## Pattern\n\n" + (pattern or "(none recorded)") + "\n\n"
                 "## Verification\n\n" + (verification_text or "repo default") + "\n\n"
                 "## Design notes\n\n(none)\n\n"
                 "## Plan\n\n" + plan_text + "\n")
    trimmed = _remove_sections(body, ["verification", "plan"])
    pointers = checklist.span(trimmed, "pointers")
    if pointers is not None:
        replacement = "\n" + "\n".join(kept) + "\n\n" if kept else "\n"
        trimmed = trimmed[:pointers[0]] + replacement + trimmed[pointers[1]:]
    return file_text, trimmed.rstrip() + "\n"


def migrate(cfg, runner, repo, number, branch, default_branch):
    """Move a legacy body plan to a plan file on a new `branch`, link it and trim the body."""
    prefix = f"issue-{number}-"
    if not branch.startswith(prefix) or len(branch) == len(prefix):
        raise checklist.TaskError(f"the branch must be named {prefix}<slug>, got {branch!r}")
    info = json.loads(runner(["gh", "issue", "view", str(number), "--json", "body,labels"]))
    labels = {item["name"] for item in info.get("labels") or []}
    started = [name for name in STATUSES[STATUSES.index("status:in-dev"):] if cfg.label(name) in labels]
    if started:
        raise checklist.TaskError(
            f"issue #{number} is {cfg.label(started[0])}; only issues not yet started can be migrated")
    body = info.get("body") or ""
    if parse_link(body):
        raise checklist.TaskError(f"issue #{number} already has a Plan: link")
    file_text, trimmed = split_legacy(body)
    path = plan_path(cfg.data, number, branch[len(prefix):])
    runner(["git", "fetch", "--quiet", "origin", default_branch])
    tree = tempfile.mkdtemp(prefix="baton-migrate-")
    try:
        runner(["git", "worktree", "add", "--detach", tree, f"origin/{default_branch}"])
        try:
            target = os.path.join(tree, path)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", newline="") as handle:
                handle.write(file_text)
            runner(["git", "add", "--", path], cwd=tree)
            runner(["git", "commit", "-q", "-m", f"Plan for #{number}"], cwd=tree)
            runner(["git", "push", "origin", f"HEAD:refs/heads/{branch}"], cwd=tree)
        finally:
            runner(["git", "worktree", "remove", "--force", tree])
    finally:
        if os.path.isdir(tree):
            shutil.rmtree(tree, ignore_errors=True)
    line = link_line(repo, branch, path)
    checklist._save_issue(runner, number, set_link(trimmed, line))
    return line
