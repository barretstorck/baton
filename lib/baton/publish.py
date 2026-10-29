"""Issue, pull request and milestone writes: scanned first, then sent through `gh`."""
import json
import os
import tempfile

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


_ISSUE_CREATE = ("usage: baton issue create --title <t> [--label <l>]... "
                 "[--milestone <title>] <body-file | ->")
_EDIT = ("usage: baton {t} edit <N> [--title <t>] [--body-file <file | ->] [--add-label <l>]... "
         "[--remove-label <l>]... [--milestone <title> | --remove-milestone]")
_PR_CREATE = "usage: baton pr create --title <t> [--draft] <body-file | ->"
_PR_READY = "usage: baton pr ready <N> [--undo]"
_MS_CREATE = "usage: baton milestone create --title <t> [--description <d>] [--due-on <ISO date>]"
_MS_EDIT = ("usage: baton milestone edit <number | title> [--title <t>] [--description <d>] "
            "[--due-on <date>] [--state open|closed]")


def _parse(argv, spec, usage):
    """Split argv into options (per spec: flag -> (key, 'one'|'many'|'flag')) and positionals."""
    opts = {key: [] for key, kind in spec.values() if kind == "many"}
    positional = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        i += 1
        if arg != "-" and arg.startswith("-"):
            flag, eq, value = arg.partition("=")
            if flag not in spec:
                raise UsageError(usage)
            key, kind = spec[flag]
            if kind == "flag":
                if eq:
                    raise UsageError(usage)
                opts[key] = True
                continue
            if not eq:
                if i >= len(argv):
                    raise UsageError(usage)
                value = argv[i]
                i += 1
            if kind == "many":
                opts[key].append(value)
            else:
                opts[key] = value
        else:
            positional.append(arg)
    return opts, positional


def _number(text, usage):
    if not text.isdigit():
        raise UsageError(usage)
    return int(text)


def _parse_edit(target, argv):
    usage = _EDIT.format(t=target)
    spec = {"--title": ("title", "one"), "--body-file": ("body_source", "one"),
            "--add-label": ("add_labels", "many"), "--remove-label": ("remove_labels", "many"),
            "--milestone": ("milestone", "one"), "--remove-milestone": ("remove_milestone", "flag")}
    opts, pos = _parse(argv, spec, usage)
    if len(pos) != 1:
        raise UsageError(usage)
    if opts.get("milestone") and opts.get("remove_milestone"):
        raise UsageError(usage)
    opts.setdefault("remove_milestone", False)
    opts["action"] = "edit"
    opts["number"] = _number(pos[0], usage)
    return opts


def parse_issue(argv):
    if not argv or argv[0] not in ("create", "edit"):
        raise UsageError(_ISSUE_CREATE + "\n" + _EDIT.format(t="issue"))
    if argv[0] == "edit":
        return _parse_edit("issue", argv[1:])
    spec = {"--title": ("title", "one"), "--label": ("labels", "many"),
            "--milestone": ("milestone", "one")}
    opts, pos = _parse(argv[1:], spec, _ISSUE_CREATE)
    if len(pos) != 1 or not opts.get("title"):
        raise UsageError(_ISSUE_CREATE)
    opts.update(action="create", body_source=pos[0])
    opts.setdefault("milestone", None)
    return opts


def parse_pr(argv):
    if not argv or argv[0] not in ("create", "edit", "ready"):
        raise UsageError("\n".join([_PR_CREATE, _EDIT.format(t="pr"), _PR_READY]))
    if argv[0] == "edit":
        return _parse_edit("pr", argv[1:])
    if argv[0] == "ready":
        opts, pos = _parse(argv[1:], {"--undo": ("undo", "flag")}, _PR_READY)
        if len(pos) != 1:
            raise UsageError(_PR_READY)
        return {"action": "ready", "number": _number(pos[0], _PR_READY),
                "undo": bool(opts.get("undo"))}
    opts, pos = _parse(argv[1:], {"--title": ("title", "one"), "--draft": ("draft", "flag")},
                       _PR_CREATE)
    if len(pos) != 1 or not opts.get("title"):
        raise UsageError(_PR_CREATE)
    return {"action": "create", "title": opts["title"], "draft": bool(opts.get("draft")),
            "body_source": pos[0]}


def parse_milestone(argv):
    if not argv or argv[0] not in ("create", "edit"):
        raise UsageError(_MS_CREATE + "\n" + _MS_EDIT)
    spec = {"--title": ("title", "one"), "--description": ("description", "one"),
            "--due-on": ("due_on", "one")}
    if argv[0] == "create":
        opts, pos = _parse(argv[1:], spec, _MS_CREATE)
        if pos or not opts.get("title"):
            raise UsageError(_MS_CREATE)
        opts.update(action="create")
        return opts
    spec["--state"] = ("state", "one")
    opts, pos = _parse(argv[1:], spec, _MS_EDIT)
    if len(pos) != 1 or opts.get("state", "open") not in ("open", "closed"):
        raise UsageError(_MS_EDIT)
    opts.update(action="edit", ref=pos[0])
    return opts
