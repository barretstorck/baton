"""Agent comments: stamped, scanned, and told apart from the maintainer's."""
import json
import os
import tempfile

from . import gate, identity, scan
from .config import has_login

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


def _scanned(cfg, text, terms):
    """Refuse text the privacy scan finds something in."""
    findings = scan.scan_text(text, terms, cfg.data["privacy_allow"])
    if findings:
        raise CommentError("comment not posted; privacy scan found: "
                           + "; ".join(scan.describe(findings)))


def publish(cfg, runner, target, number, text, terms, role="orchestrator"):
    """Scan an already stamped comment and post it. Returns the comment's URL."""
    if target not in TARGETS:
        raise CommentError(f"unknown target {target!r}; use issue or pr")
    _scanned(cfg, text, terms)
    handle, path = tempfile.mkstemp(suffix=".md")
    try:
        with os.fdopen(handle, "w") as out:
            out.write(text)
        return identity.write(runner, ["gh", target, "comment", str(number), "--body-file", path],
                              role).strip()
    finally:
        os.unlink(path)


def publish_review(cfg, runner, number, text, terms, event, login):
    """Scan a stamped verdict and post it as a formal review by the reviewer App.

    `event` is approve, request-changes or comment. `gh pr review` prints no URL, so the
    newest review by `login` is read back. Returns its URL.
    """
    _scanned(cfg, text, terms)
    handle, path = tempfile.mkstemp(suffix=".md")
    try:
        with os.fdopen(handle, "w") as out:
            out.write(text)
        identity.write(runner, ["gh", "pr", "review", str(number), f"--{event}",
                                "--body-file", path], "reviewer")
    finally:
        os.unlink(path)
    out = runner(["gh", "api", "--paginate",
                  f"repos/{{owner}}/{{repo}}/pulls/{number}/reviews?per_page=100",
                  "--jq", f'.[] | select(.user.login == "{login}") | .html_url'])
    urls = [line.strip() for line in out.splitlines() if line.strip()]
    if not urls:
        raise CommentError("the review was posted but its URL could not be read")
    return urls[-1]


def post(cfg, runner, target, number, role, kind, body, terms):
    """Scan, stamp and post a comment. Returns the comment's URL."""
    if target not in TARGETS:
        raise CommentError(f"unknown target {target!r}; use issue or pr")
    return publish(cfg, runner, target, number, compose(role, kind, body), terms, role)


def _notify_marker(cause):
    return f"<!-- baton:notify:{cause} -->"


def notified(comments, cause, trusted):
    """True when a trusted login already posted the notify status comment for this cause.

    The comment's author must be one of `trusted` (maintainers and Baton logins), its first
    line the orchestrator status header, and its body the cause's notify marker.
    """
    marker = _notify_marker(cause)
    header = f"{gate.MARKER}orchestrator:status -->"
    trusted = list(trusted)
    return any(has_login(item.get("author"), trusted)
               and _first_line(item.get("body")) == header
               and marker in (item.get("body") or "")
               for item in comments or [])


def notify(cfg, runner, number, cause, text, maintainers, terms, bots=()):
    """Label an issue needs-maintainer and mention the maintainers, once per cause.

    Returns the comment's URL, or None when the issue was already notified for it by a
    maintainer or one of `bots` (Baton logins); anyone else's marker comment is ignored.
    """
    if cause not in NOTIFY_CAUSES:
        raise CommentError(f"unknown cause {cause!r}; use one of {list(NOTIFY_CAUSES)}")
    if not maintainers:
        raise CommentError("no maintainers to mention")
    found = json.loads(runner(["gh", "issue", "view", str(number), "--json", "comments"])
                       or "{}")
    comments = identity.mark_authors(runner, number, found.get("comments"))
    if notified(comments, cause, list(maintainers) + sorted(bots)):
        return None
    mentions = " ".join(f"@{handle}" for handle in maintainers)
    body = f"{_notify_marker(cause)}\n{mentions}\n{text.strip()}"
    text_out = compose("orchestrator", "status", body)
    findings = scan.scan_text(text_out, terms, cfg.data["privacy_allow"])
    if findings:
        raise CommentError("comment not posted; privacy scan found: "
                           + "; ".join(scan.describe(findings)))
    identity.write(runner, ["gh", "issue", "edit", str(number), "--add-label",
                            cfg.label("needs-maintainer")], "orchestrator")
    return publish(cfg, runner, "issue", number, text_out, terms)


def _first_line(body):
    return (body or "").lstrip().split("\n", 1)[0]


def pending_answers(issue, owner, bots=()):
    """The latest agent question on an issue and the maintainer's replies after it.

    A question marker counts from `owner` (the maintainers) or `bots` (Baton logins).
    A reply is a comment by `owner` that carries no Baton marker, or a `decision`
    marker comment from `owner` or `bots`. A marker-less comment by a bot never counts.
    """
    comments = issue.get("comments") or []
    question_at = None
    for index, item in enumerate(comments):
        line = _first_line(item.get("body"))
        if not (line.startswith(gate.MARKER) and _QUESTION in line):
            continue
        author = item.get("author")
        if has_login(author, owner) or has_login(author, list(bots)):
            question_at = index
    if question_at is None:
        return {"question": None, "answers": []}
    answers = []
    for item in comments[question_at + 1:]:
        author = item.get("author")
        is_bot = has_login(author, list(bots))
        line = _first_line(item.get("body"))
        marked = line.startswith(gate.MARKER)
        if marked and _DECISION in line:
            if not (has_login(author, owner) or is_bot):
                continue
        elif marked:
            continue
        elif is_bot or not has_login(author, owner):
            continue
        if gate.MARKER in (item.get("body") or "") and not marked:
            continue
        answers.append({"url": item.get("url"), "body": item.get("body")})
    return {"question": comments[question_at].get("url"), "answers": answers}
