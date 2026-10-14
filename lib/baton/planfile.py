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
from .config import DEFAULTS, STATUSES
from .run import RunError

_LINK = re.compile(r"^Plan:\s*\[(?P<path>[^\]]+)\]\((?P<url>[^)\s]+)\)\s*$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_DOCS_DIR = DEFAULTS["docs"]["dir"]
_PLAN_LINE = re.compile(r"^Plan:.*$", re.M)


def plan_path(cfg, number, slug):
    return f"{cfg['docs']['dir']}/plans/{number}-{slug}.md"


def link_line(repo, ref, path):
    return f"Plan: [{path}](https://github.com/{repo}/blob/{ref}/{path})"


_BRANCH = re.compile(r"^[A-Za-z0-9._/-]+$")


def check_link(ref, path, docs_dir=None):
    """Raise TaskError unless `ref` and `path` are safe to hand to git and the filesystem.

    The link comes from the issue body, which anyone with edit rights controls. The path must
    be a relative `.md` file under `<docs.dir>/plans/`; the ref a full sha or a plain branch
    name that cannot be read as an option.
    """
    prefix = [part for part in (docs_dir or DEFAULT_DOCS_DIR).split("/") if part not in ("", ".")]
    parts = path.split("/")
    if (path.startswith(("/", "-")) or "\\" in path or "\0" in path
            or any(part in ("", ".", "..") for part in parts)
            or parts[:len(prefix) + 1] != prefix + ["plans"] or len(parts) <= len(prefix) + 1
            or not path.endswith(".md")):
        raise checklist.TaskError(
            f"invalid Plan: link, the path {path!r} must be a relative .md file under "
            f"{'/'.join(prefix)}/plans/")
    if not (_SHA.match(ref) or (_BRANCH.match(ref) and not ref.startswith("-")
                                and ".." not in ref and not ref.endswith(("/", ".lock")))):
        raise checklist.TaskError(f"invalid Plan: link, the ref {ref!r} is not a commit sha or a "
                                  "plain branch name")


def check_branch(number, branch):
    """Raise TaskError unless `branch` is the issue's own branch, `issue-<number>-<slug>`."""
    prefix = f"issue-{number}-"
    if not branch.startswith(prefix) or len(branch) == len(prefix):
        raise checklist.TaskError(
            f"the Plan: link names branch {branch!r}; plans are only written to {prefix}<slug>")


def check_write(root, path):
    """Raise TaskError unless writing `path` under `root` stays inside it.

    The path and every folder below `root` on the way to it must not be a symlink, and the
    resolved target must be inside the resolved `root`.
    """
    top = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root, path))
    if os.path.commonpath([top, target]) != top or target == top:
        raise checklist.TaskError(f"refusing to write {path!r}: it resolves outside {root!r}")
    current = root
    for part in path.split("/"):
        current = os.path.join(current, part)
        if os.path.islink(current):
            raise checklist.TaskError(f"refusing to write {path!r}: {part!r} is a symlink")


def parse_link(body, docs_dir=None):
    """{'ref', 'path', 'pinned'} for the body's `Plan:` line, or None.

    Raises TaskError when the line is a link whose ref or path is not safe (see check_link).
    """
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
            check_link(ref, path, docs_dir)
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
            return runner(["git", "show", "--end-of-options", f"{ref}:{path}"])
        runner(["git", "fetch", "--quiet", "--end-of-options", "origin", ref])
        return runner(["git", "show", "--end-of-options", f"origin/{ref}:{path}"])
    except RunError as error:
        raise checklist.TaskError(f"plan file {path} not found at {ref}: {error}")


def link_issue(runner, repo, number, branch=None, path=None, at=None, docs_dir=None):
    """Write or replace the issue's `Plan:` line; returns the new line.

    With `at`, the link is pinned to that commit and keeps the path of the existing link.
    """
    body = checklist.issue_body(runner, number)
    if at is not None:
        if not _SHA.match(at):
            raise checklist.TaskError(f"--at needs a full 40-character commit sha, got {at!r}")
        existing = parse_link(body, docs_dir)
        if existing is None:
            raise checklist.TaskError(f"issue #{number} has no Plan: link to pin")
        ref, path = at, existing["path"]
    elif branch and path:
        ref = branch
    else:
        raise checklist.TaskError("plan link needs a branch and a path, or --at <sha>")
    check_link(ref, path, docs_dir)
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
    if parse_link(body, cfg.data["docs"]["dir"]):
        raise checklist.TaskError(f"issue #{number} already has a Plan: link")
    file_text, trimmed = split_legacy(body)
    path = plan_path(cfg.data, number, branch[len(prefix):])
    runner(["git", "fetch", "--quiet", "origin", default_branch])
    tree = tempfile.mkdtemp(prefix="baton-migrate-")
    try:
        runner(["git", "worktree", "add", "--detach", tree, f"origin/{default_branch}"])
        try:
            target = os.path.join(tree, path)
            check_write(tree, path)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            check_write(tree, path)
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
