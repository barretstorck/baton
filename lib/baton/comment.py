"""Agent comments: stamped, scanned, and told apart from the maintainer's."""
import json
import os
import tempfile

from . import gate, scan
from .config import logins

ROLES = {"orchestrator", "developer", "refine", "reviewer", "researcher"}
KINDS = {"question", "status", "decision", "plan", "review", "findings", "reply",
         "closed", "reopened"}
TARGETS = {"issue", "pr"}
_QUESTION = ":question -->"
_DECISION = ":decision -->"
NOTIFY_CAUSES = ("stuck", "approval", "triage-unsure", "spike-infeasible",
                 "repro-skipped", "rule")


class CommentError(Exception):
    pass


def read_body(source, stdin):
    """A comment body from a file path, or from stdin when the path is `-`."""
    if source == "-":
        body = stdin.read()
    else:
        with open(source) as handle:
            body = handle.read()
    if not body.strip():
        raise CommentError("the comment body is empty")
    return body


def compose(role, kind, body):
    if role not in ROLES:
        raise CommentError(f"unknown role {role!r}; use one of {sorted(ROLES)}")
    if kind not in KINDS:
        raise CommentError(f"unknown kind {kind!r}; use one of {sorted(KINDS)}")
    return f"{gate.MARKER}{role}:{kind} -->\n**Baton {role}: {kind}**\n\n{body.strip()}\n"


def publish(cfg, runner, target, number, text, terms):
    """Scan an already stamped comment and post it. Returns the comment's URL."""
    if target not in TARGETS:
        raise CommentError(f"unknown target {target!r}; use issue or pr")
    findings = scan.scan_text(text, terms, cfg.data["privacy_allow"])
    if findings:
        raise CommentError("comment not posted; privacy scan found: "
                           + "; ".join(scan.describe(findings)))
    handle, path = tempfile.mkstemp(suffix=".md")
    try:
        with os.fdopen(handle, "w") as out:
            out.write(text)
        return runner(["gh", target, "comment", str(number), "--body-file", path]).strip()
    finally:
        os.unlink(path)


def post(cfg, runner, target, number, role, kind, body, terms):
    """Scan, stamp and post a comment. Returns the comment's URL."""
    if target not in TARGETS:
        raise CommentError(f"unknown target {target!r}; use issue or pr")
    return publish(cfg, runner, target, number, compose(role, kind, body), terms)


def _notify_marker(cause):
    return f"<!-- baton:notify:{cause} -->"


def notified(comments, cause):
    """True when any comment already carries the notify marker for this cause."""
    marker = _notify_marker(cause)
    return any(marker in (item.get("body") or "") for item in comments or [])


def notify(cfg, runner, number, cause, text, maintainers, terms):
    """Label an issue needs-maintainer and mention the maintainers, once per cause.

    Returns the comment's URL, or None when the issue was already notified for it.
    """
    if cause not in NOTIFY_CAUSES:
        raise CommentError(f"unknown cause {cause!r}; use one of {list(NOTIFY_CAUSES)}")
    if not maintainers:
        raise CommentError("no maintainers to mention")
    found = json.loads(runner(["gh", "issue", "view", str(number), "--json", "comments"])
                       or "{}")
    if notified(found.get("comments"), cause):
        return None
    mentions = " ".join(f"@{handle}" for handle in maintainers)
    body = f"{_notify_marker(cause)}\n{mentions}\n{text.strip()}"
    text_out = compose("orchestrator", "status", body)
    findings = scan.scan_text(text_out, terms, cfg.data["privacy_allow"])
    if findings:
        raise CommentError("comment not posted; privacy scan found: "
                           + "; ".join(scan.describe(findings)))
    runner(["gh", "issue", "edit", str(number), "--add-label", cfg.label("needs-maintainer")])
    return publish(cfg, runner, "issue", number, text_out, terms)


def _first_line(body):
    return (body or "").lstrip().split("\n", 1)[0]


def pending_answers(issue, owner):
    """The latest agent question on an issue and the maintainer's replies after it.

    A reply is a comment by the repository owner that either carries no Baton
    marker, or is the orchestrator's `decision` comment quoting an answer the
    maintainer gave in the session.
    """
    comments = issue.get("comments") or []
    question_at = None
    for index, item in enumerate(comments):
        line = _first_line(item.get("body"))
        if line.startswith(gate.MARKER) and _QUESTION in line:
            question_at = index
    if question_at is None:
        return {"question": None, "answers": []}
    answers = []
    for item in comments[question_at + 1:]:
        if item.get("author", {}).get("login") not in logins(owner):
            continue
        line = _first_line(item.get("body"))
        if line.startswith(gate.MARKER) and _DECISION not in line:
            continue
        if gate.MARKER in (item.get("body") or "") and not line.startswith(gate.MARKER):
            continue
        answers.append({"url": item.get("url"), "body": item.get("body")})
    return {"question": comments[question_at].get("url"), "answers": answers}
