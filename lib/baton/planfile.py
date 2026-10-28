"""A plan kept as a file in the repository, linked from the issue's first line.

Design spec, "plans in the repo": the issue body starts with a `Plan:` link to the file on
the issue branch, or at a commit once the branch is gone.
"""
import json
import os
import re

from . import checklist
from .config import DEFAULTS, STATUSES
from .run import RunError

_LINK = re.compile(r"^Plan:\s*\[(?P<path>[^\]]+)\]\((?P<url>[^)\s]+)\)\s*$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_DOCS_DIR = DEFAULTS["docs"]["dir"]
_PLAN_LINE = re.compile(r"^Plan:.*$", re.M)


def plan_path(cfg, number, slug):
    return f"{cfg['docs']['dir']}/plans/{number}-{slug}.md"


PLAN_REF = "refs/plans/"
LOCAL_REF = "refs/baton/plans/"
_PLAN_NUMBER = re.compile(r"^(\d+)-")


def ref_name(number):
    return f"{PLAN_REF}{number}"


def link_line(repo, ref, path):
    if ref.startswith(PLAN_REF):
        return f"Plan: [{path}](https://github.com/{repo}/commits/{ref})"
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
    if ref.startswith(PLAN_REF):
        number = ref[len(PLAN_REF):]
        match = _PLAN_NUMBER.match(parts[-1])
        if not (number.isascii() and number.isdigit() and match and match.group(1) == number):
            raise checklist.TaskError(
                f"invalid Plan: link, the ref {ref!r} must be {PLAN_REF}<N> with <N> the issue "
                f"number that starts the plan file name")
        return
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
    """{'ref', 'path', 'pinned', 'kind'} for the body's `Plan:` line, or None.

    Raises TaskError when the line is a link whose ref or path is not safe (see check_link).
    """
    for line in (body or "").splitlines():
        match = _LINK.match(line.rstrip())
        if not match:
            continue
        path, url = match.group("path"), match.group("url")
        history = url.find("/commits/" + PLAN_REF)
        if history >= 0:
            ref = url[history + len("/commits/"):]
            check_link(ref, path, docs_dir)
            return {"ref": ref, "path": path, "pinned": False, "kind": "ref"}
        marker = url.find("/blob/")
        if marker < 0 or not url.endswith("/" + path):
            continue
        ref = url[marker + len("/blob/"):-len(path) - 1]
        if ref:
            check_link(ref, path, docs_dir)
            pinned = bool(_SHA.match(ref))
            return {"ref": ref, "path": path, "pinned": pinned,
                    "kind": "pinned" if pinned else "branch"}
    return None


def set_link(body, line):
    body = body or ""
    if _PLAN_LINE.search(body):
        return _PLAN_LINE.sub(lambda _: line, body, count=1)
    return f"{line}\n\n{body}"


def _plan_number(ref, path):
    """The issue number of plan ref `ref`, or of the plan file `path` for a pinned sha."""
    if ref.startswith(PLAN_REF):
        return ref[len(PLAN_REF):]
    match = _PLAN_NUMBER.match(path.rsplit("/", 1)[-1])
    return match.group(1) if match else None


def _fetch_plan(runner, number):
    runner(["git", "fetch", "--quiet", "origin", f"+{PLAN_REF}{number}:{LOCAL_REF}{number}"])


def read(runner, ref, path):
    """Text of `path` on branch `ref` (fetched first), on plan ref `refs/plans/<N>` (fetched
    into `refs/baton/plans/<N>` first) or at pinned commit `ref` (fetched through its plan ref
    when the commit is not in the local object store)."""
    try:
        if ref.startswith(PLAN_REF):
            number = _plan_number(ref, path)
            _fetch_plan(runner, number)
            return runner(["git", "show", "--end-of-options", f"{LOCAL_REF}{number}:{path}"])
        if _SHA.match(ref):
            try:
                return runner(["git", "show", "--end-of-options", f"{ref}:{path}"])
            except RunError:
                number = _plan_number(ref, path)
                if number is None:
                    raise
                _fetch_plan(runner, number)
                return runner(["git", "show", "--end-of-options", f"{ref}:{path}"])
        runner(["git", "fetch", "--quiet", "--end-of-options", "origin", ref])
        return runner(["git", "show", "--end-of-options", f"origin/{ref}:{path}"])
    except RunError as error:
        raise checklist.TaskError(f"plan file {path} not found at {ref}: {error}")


def tick_commits(runner, ref, path):
    """{task id: "<short sha> <subject>"} of the commit that first ticked each ticked task.

    Only for a `refs/plans/<N>` ref (else empty). Walks the plan ref's history oldest first and
    parses the file at each commit; the first commit whose version has a task ticked wins.
    """
    if not ref.startswith(PLAN_REF):
        return {}
    number = _plan_number(ref, path)
    _fetch_plan(runner, number)
    log = runner(["git", "log", "--reverse", "--format=%h%x09%H%x09%s", "--end-of-options",
                  f"{LOCAL_REF}{number}", "--", path])
    found = {}
    for line in log.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue
        short, full, subject = parts
        try:
            text = runner(["git", "show", "--end-of-options", f"{full}:{path}"])
        except RunError:
            continue
        for task in (checklist.plan(text) or {}).get("tasks", []):
            if task["done"] and task["id"] not in found:
                found[task["id"]] = f"{short} {subject}"
    return found


def tip(runner, number):
    """The sha of `refs/plans/<number>` on the remote, or None when it does not exist."""
    out = runner(["git", "ls-remote", "origin", ref_name(int(number))]).split()
    return out[0] if out and _SHA.match(out[0]) else None


def write(runner, number, path, text, message):
    """Commit `text` as `path` on a new commit for `refs/plans/<number>`; returns its sha.

    Built with plumbing (no checkout, no index): a blob, one tree per directory level from the
    file up, then the commit. Its parent is the remote ref's tip when the ref exists (fetched to
    `refs/baton/plans/<number>`), else it has none. Nothing is pushed; see `push`.
    """
    number = int(number)
    check_link(ref_name(number), path)
    parts = path.split("/")
    sha = runner(["git", "hash-object", "-w", "--stdin"], input=text).strip()
    entry = f"100644 blob {sha}\t{parts[-1]}\n"
    for name in reversed(parts[:-1]):
        sha = runner(["git", "mktree"], input=entry).strip()
        entry = f"040000 tree {sha}\t{name}\n"
    tree = runner(["git", "mktree"], input=entry).strip()
    command = ["git", "commit-tree", tree]
    if tip(runner, number):
        _fetch_plan(runner, number)
        command += ["-p", f"{LOCAL_REF}{number}"]
    return runner(command + ["-m", message]).strip()


def push(runner, number, sha):
    """Fast-forward `refs/plans/<number>` on the remote to commit `sha`."""
    if not _SHA.match(sha):
        raise checklist.TaskError(f"push needs a full 40-character commit sha, got {sha!r}")
    runner(["git", "push", "origin", f"{sha}:{ref_name(int(number))}"])


def pin(runner, repo, number, docs_dir=None):
    """Pin the issue's `Plan:` link to the plan ref's tip; returns the sha.

    None when the link is already pinned or is not a plan-ref link. TaskError when the ref
    no longer exists on the remote.
    """
    existing = parse_link(checklist.issue_body(runner, number), docs_dir)
    if existing is None or existing["kind"] != "ref":
        return None
    sha = tip(runner, number)
    if sha is None:
        raise checklist.TaskError(f"{existing['ref']} does not exist on the remote")
    link_issue(runner, repo, number, at=sha, docs_dir=docs_dir)
    return sha


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


def _brief_block(block):
    """The lines of one task block in the new layout (copied as is when it already is)."""
    lines = block["lines"]
    if checklist._TASK_NEW.match(lines[0].rstrip("\r\n")):
        return "".join(lines).rstrip("\n").split("\n")
    match = checklist._TASK.match(lines[0].rstrip("\r\n"))
    mark = "x" if block["done"] else " "
    brief = [line.rstrip() for line in lines[1:]]
    while brief and not brief[-1]:
        brief.pop()
    while brief and not brief[0]:
        brief.pop(0)
    indents = [len(line) - len(line.lstrip()) for line in brief if line]
    shift = 2 - min(indents) if indents else 0
    moved = []
    for line in brief:
        if not line:
            moved.append("")
        elif shift >= 0:
            moved.append(" " * shift + line)
        else:
            moved.append(line[-shift:])
    return ([f"- [{mark}] Task {match.group(2)}: {match.group(3).strip()}",
             "  <details>", "  <summary>Click to see plan details</summary>", ""]
            + moved + ["", "  </details>"])


def developer_plan_from_file(text):
    """The `## Developer Plan` section built from a plan file's text (heading included)."""
    text = (text or "").replace("\r\n", "\n")
    where = checklist.span(text, "plan")
    if where is None:
        raise checklist.TaskError("the plan file has no '## Plan' section")
    head, blocks = checklist._blocks(text[where[0]:where[1]])
    against = re.search(r"^Written against:.*$", "".join(head), re.M)
    out = ["## Developer Plan", ""]
    if against:
        out += [against.group(0).strip(), ""]
    for heading, name in (("verification", "Verification"), ("pattern", "Pattern"),
                          ("design notes", "Design notes")):
        found = checklist.span(text, heading)
        content = text[found[0]:found[1]].strip() if found else ""
        if not content or (heading == "design notes"
                           and re.fullmatch(r"\(?none\)?\.?", content, re.I)):
            continue
        out += [f"### {name}", "", content, ""]
    for block in blocks:
        out += _brief_block(block)
    return "\n".join(out).rstrip("\n") + "\n"


def migrate(cfg, runner, number):
    """Convert the issue's plan into a `## Developer Plan` section of its body.

    The source is the plan on `refs/plans/<N>`, on a branch or at a pinned commit (the body's
    `Plan:` link), or the legacy `## Plan` section. The `Plan:` line goes; the ref and any
    branch are left alone. Refused for an issue that is in development or later, that has no
    plan, or that already has a `## Developer Plan`. Returns a one-line summary.
    """
    docs_dir = cfg.data["docs"]["dir"]
    info = json.loads(runner(["gh", "issue", "view", str(number), "--json", "title,body,labels"]))
    labels = {item["name"] for item in info.get("labels") or []}
    started = [name for name in STATUSES[STATUSES.index("status:in-dev"):] if cfg.label(name) in labels]
    if started:
        raise checklist.TaskError(
            f"issue #{number} is {cfg.label(started[0])}; only issues not yet started can be migrated")
    body = info.get("body") or ""
    if checklist.span(body, "developer plan") is not None:
        raise checklist.TaskError(f"issue #{number} already has a Developer Plan")
    link = parse_link(body, docs_dir)
    if link is not None:
        text = read(runner, link["ref"], link["path"])
        new_body = _PLAN_LINE.sub("", body, count=1).lstrip("\n")
    else:
        if checklist.span(body, "plan") is None:
            raise checklist.TaskError(f"issue #{number} has no plan to migrate")
        text, new_body = split_legacy(body)
    section = developer_plan_from_file(text)
    new = checklist.set_plan(new_body, section)
    checklist._save_issue(runner, number, new)
    count = len(checklist.plan(new)["tasks"])
    return f"migrated the plan of #{number} into its description: {count} task(s)"
