"""Agent comments: stamped, scanned, and told apart from the maintainer's."""
import os
import tempfile

from . import gate, scan

ROLES = {"orchestrator", "developer", "refine"}
KINDS = {"question", "status", "decision", "plan", "review", "findings"}
TARGETS = {"issue", "pr"}
_QUESTION = ":question -->"
_DECISION = ":decision -->"


class CommentError(Exception):
    pass


def compose(role, kind, body):
    if role not in ROLES:
        raise CommentError(f"unknown role {role!r}; use one of {sorted(ROLES)}")
    if kind not in KINDS:
        raise CommentError(f"unknown kind {kind!r}; use one of {sorted(KINDS)}")
    return f"{gate.MARKER}{role}:{kind} -->\n**Baton {role}: {kind}**\n\n{body.strip()}\n"


def post(cfg, runner, target, number, role, kind, body, terms):
    """Scan, stamp and post a comment. Returns the comment's URL."""
    if target not in TARGETS:
        raise CommentError(f"unknown target {target!r}; use issue or pr")
    text = compose(role, kind, body)
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
        if item.get("author", {}).get("login") != owner:
            continue
        line = _first_line(item.get("body"))
        if line.startswith(gate.MARKER) and _DECISION not in line:
            continue
        if gate.MARKER in (item.get("body") or "") and not line.startswith(gate.MARKER):
            continue
        answers.append({"url": item.get("url"), "body": item.get("body")})
    return {"question": comments[question_at].get("url"), "answers": answers}
