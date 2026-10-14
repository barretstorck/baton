"""A plan kept as a file in the repository, linked from the issue's first line.

Design spec, "plans in the repo": the issue body starts with a `Plan:` link to the file on
the issue branch, or at a commit once the branch is gone.
"""
import re

from . import checklist
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
