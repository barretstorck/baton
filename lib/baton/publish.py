"""Issue, pull request and milestone writes: scanned first, then sent through `gh`."""
import json
import os
import tempfile

from . import scan
from .comment import CommentError

_MILESTONES = "repos/{owner}/{repo}/milestones"


def _check(cfg, terms, *texts):
    text = "\n".join(t for t in texts if t)
    findings = scan.scan_text(text, terms, cfg.data["privacy_allow"])
    if findings:
        raise CommentError("nothing published; privacy scan found: "
                           + "; ".join(scan.describe(findings)))


def _need_body(body):
    if body is None or not body.strip():
        raise CommentError("the body is empty; write the full text first")


def _with_body(runner, body, build):
    handle, path = tempfile.mkstemp(suffix=".md")
    try:
        with os.fdopen(handle, "w") as out:
            out.write(body)
        return runner(build(path)).strip()
    finally:
        os.unlink(path)


def create_issue(cfg, runner, title, body, labels, milestone, terms):
    _need_body(body)
    labels = list(labels or ())
    _check(cfg, terms, title, body, *labels, milestone)

    def build(path):
        argv = ["gh", "issue", "create", "--title", title, "--body-file", path]
        for label in labels:
            argv += ["--label", label]
        if milestone:
            argv += ["--milestone", milestone]
        return argv
    return _with_body(runner, body, build)


def edit(cfg, runner, target, number, terms, title=None, body=None, add_labels=(),
         remove_labels=(), milestone=None, remove_milestone=False):
    if target not in ("issue", "pr"):
        raise CommentError(f"unknown target {target!r}; use issue or pr")
    add_labels, remove_labels = list(add_labels or ()), list(remove_labels or ())
    if body is not None:
        _need_body(body)
    if milestone and remove_milestone:
        raise CommentError("use --milestone or --remove-milestone, not both")
    if not (title or body or add_labels or remove_labels or milestone or remove_milestone):
        raise CommentError("nothing to change")
    _check(cfg, terms, title, body, *add_labels, *remove_labels, milestone)
    argv = ["gh", target, "edit", str(number)]
    if title:
        argv += ["--title", title]
    for label in add_labels:
        argv += ["--add-label", label]
    for label in remove_labels:
        argv += ["--remove-label", label]
    if milestone:
        argv += ["--milestone", milestone]
    if remove_milestone:
        argv += ["--remove-milestone"]
    if body is None:
        return runner(argv).strip()
    return _with_body(runner, body, lambda path: argv + ["--body-file", path])


def create_pr(cfg, runner, title, body, draft, terms):
    _need_body(body)
    _check(cfg, terms, title, body)

    def build(path):
        argv = ["gh", "pr", "create", "--title", title, "--body-file", path]
        return argv + ["--draft"] if draft else argv
    return _with_body(runner, body, build)


def ready_pr(runner, number, undo=False):
    argv = ["gh", "pr", "ready", str(number)]
    if undo:
        argv.append("--undo")
    return runner(argv).strip()


def create_milestone(cfg, runner, title, description, due_on, terms):
    _check(cfg, terms, title, description)
    argv = ["gh", "api", _MILESTONES, "-f", f"title={title}"]
    if description:
        argv += ["-f", f"description={description}"]
    if due_on:
        argv += ["-f", f"due_on={due_on}"]
    return runner(argv).strip()


def edit_milestone(cfg, runner, ref, terms, title=None, description=None, due_on=None,
                   state=None):
    if state is not None and state not in ("open", "closed"):
        raise CommentError("state must be open or closed")
    if not (title or description is not None or due_on or state):
        raise CommentError("nothing to change")
    _check(cfg, terms, title, description)
    ref = str(ref)
    if ref.isdigit():
        number = ref
    else:
        listing = json.loads(runner(["gh", "api", _MILESTONES + "?state=all"]) or "[]")
        found = [m for m in listing if m.get("title") == ref]
        if not found:
            raise CommentError(f"no milestone titled {ref!r}")
        number = str(found[0]["number"])
    argv = ["gh", "api", "--method", "PATCH", f"{_MILESTONES}/{number}"]
    for key, value in (("title", title), ("description", description),
                       ("due_on", due_on), ("state", state)):
        if value is not None and value != "":
            argv += ["-f", f"{key}={value}"]
    return runner(argv).strip()
