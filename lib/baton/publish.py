"""Issue, pull request and milestone writes: scanned first, then sent through `gh`."""
import contextlib
import io
import json
import os
import sys
import tempfile

from . import branches
from . import checklist
from . import cliargs
from . import identity
from . import issue as issue_mod
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
        return identity.write(runner, build(path), "orchestrator").strip()
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


def _keep_plan(runner, number, body):
    """`body`, with the issue's current Developer Plan appended if `body` drops it."""
    if checklist.span(body, "developer plan") is not None:
        return body
    old = checklist.issue_body(runner, number)
    found = checklist.span(old, "developer plan")
    if found is None:
        return body
    section = old[checklist._section_start(old, found):found[1]]
    try:
        kept = checklist.set_plan(body, section)
    except checklist.TaskError as err:
        raise CommentError(f"could not keep the Developer Plan of #{number}: {err}")
    print(f"baton: kept the Developer Plan of #{number}; change it with baton plan set",
          file=sys.stderr)
    return kept


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
    if body is not None and target == "issue":
        body = _keep_plan(runner, number, body)
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
        return identity.write(runner, argv, "orchestrator").strip()
    return _with_body(runner, body, lambda path: argv + ["--body-file", path])


def create_pr(cfg, runner, title, body, draft, terms, head=None):
    _need_body(body)
    _check(cfg, terms, title, body)

    def build(path):
        argv = ["gh", "pr", "create", "--title", title, "--body-file", path]
        if head:
            argv += ["--head", head]
        return argv + ["--draft"] if draft else argv
    return _with_body(runner, body, build)


def resolve_head(runner, cwd, head):
    """The head branch for a new pull request: the given one, or the pushed branch a detached HEAD stands on."""
    if head:
        return head
    if not branches.detached(runner, cwd):
        return None
    found = branches.pushed_branches_at_head(runner, cwd)
    if len(found) != 1:
        raise CommentError("HEAD is detached and no single pushed issue-<N>-* branch points at it; "
                           "push the branch with baton push HEAD:refs/heads/<branch>, "
                           "or pass --head <branch>")
    return found[0]


def ready_pr(runner, number, undo=False):
    argv = ["gh", "pr", "ready", str(number)]
    if undo:
        argv.append("--undo")
    return identity.write(runner, argv, "orchestrator").strip()


def create_milestone(cfg, runner, title, description, due_on, terms):
    _check(cfg, terms, title, description)
    argv = ["gh", "api", _MILESTONES, "-f", f"title={title}"]
    if description:
        argv += ["-f", f"description={description}"]
    if due_on:
        argv += ["-f", f"due_on={due_on}"]
    return identity.write(runner, argv, "orchestrator").strip()


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
    return identity.write(runner, argv, "orchestrator").strip()


def mark_missed(cfg, runner, number, milestone_ref, terms):
    """Record that an issue missed its sprint: description line, label, no milestone."""
    ref = str(milestone_ref)
    listing = json.loads(runner(["gh", "api", _MILESTONES + "?state=all"]) or "[]")
    found = [m for m in listing
             if m.get("title") == ref or str(m.get("number")) == ref]
    if not found:
        raise CommentError(f"no milestone {ref!r}")
    milestone = found[0]
    data = json.loads(runner(["gh", "issue", "view", str(number), "--json", "body"]) or "{}")
    body = issue_mod.add_missed_line(data.get("body") or "", milestone["title"],
                                     milestone.get("html_url", ""))
    return edit(cfg, runner, "issue", number, terms, body=body,
                add_labels=[cfg.label("missed-sprint")], remove_milestone=True)


class UsageError(CommentError):
    """Bad command-line usage; the message is the usage line."""


cliargs.build()
_ISSUE_CREATE = cliargs.USAGE["issue create"]
_EDIT = cliargs._EDIT
_PR_CREATE = cliargs.USAGE["pr create"]
_PR_READY = cliargs.USAGE["pr ready"]
_MS_CREATE = cliargs.USAGE["milestone create"]
_MS_EDIT = cliargs.USAGE["milestone edit"]


def _parsed(command, argv):
    """Run cliargs.parse, turning a usage failure into UsageError."""
    cliargs.build()
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            return vars(cliargs.parse(command, argv))
    except cliargs.Usage:
        raise UsageError(err.getvalue().rstrip("\n") or cliargs.USAGE[command]) from None


def _edit_opts(o):
    out = {k: o[k] for k in ("title", "milestone", "add_labels", "remove_labels")
           if o.get(k) not in (None, [])}
    out.update(add_labels=o["add_labels"], remove_labels=o["remove_labels"],
               remove_milestone=o["remove_milestone"], action="edit", number=o["number"])
    if o["body_source"] is not None:
        out["body_source"] = o["body_source"]
    return out


def parse_issue(argv):
    o = _parsed("issue", argv)
    if o["action"] == "edit":
        return _edit_opts(o)
    if o["action"] != "create":
        raise UsageError(cliargs.USAGE["issue"])
    return {"title": o["title"], "labels": o["labels"], "milestone": o["milestone"],
            "action": "create", "body_source": o["body_source"]}


def parse_pr(argv):
    o = _parsed("pr", argv)
    if o["action"] == "edit":
        return _edit_opts(o)
    if o["action"] == "ready":
        return {"action": "ready", "number": o["number"], "undo": o["undo"]}
    return {"action": "create", "title": o["title"], "draft": o["draft"],
            "body_source": o["body_source"]}


def parse_milestone(argv):
    o = _parsed("milestone", argv)
    out = {k: o[k] for k in ("title", "description", "due_on", "state", "ref")
           if o.get(k) is not None}
    out["action"] = o["action"]
    return out
