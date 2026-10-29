"""Triage decisions: parsed and checked before anything is written."""
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
import pathlib

from . import comment as commentmod
from . import labels
from . import run as runmod
from . import scan
from . import state
from . import verify
from .comment import CommentError

CATEGORIES = ("spam", "duplicate", "support", "unclear", "feature", "bug",
              "maintenance", "unsure")
REAL = ("feature", "bug", "maintenance")
SUMMARY_KEYS = ("symptom", "expected", "actual", "area", "version", "type")
KEYS = ("category", "duplicate_of", "priority", "severity", "comment", "summary")
COMMENT_MAX = 2000
FIELD_MAX = {"symptom": 300, "expected": 300, "actual": 300, "area": 80,
             "version": 40, "type": 40}
SHELL_WORDS = {"curl", "wget", "sudo", "rm", "sh", "bash", "git", "gh", "baton",
               "pip", "npm", "python", "python3"}


class TriageError(CommentError):
    pass


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _check_field(name, value):
    if not isinstance(value, str) or not value.strip():
        raise TriageError(f"summary.{name} must be a non-empty string")
    if "\n" in value or "\r" in value:
        raise TriageError(f"summary.{name} must be a single line")
    if len(value) > FIELD_MAX[name]:
        raise TriageError(f"summary.{name} is over {FIELD_MAX[name]} characters")
    if "`" in value:
        raise TriageError(f"summary.{name} holds a backtick or code fence")
    for bad in ("://", "www.", "]("):
        if bad in value:
            raise TriageError(f"summary.{name} holds a link ({bad!r})")
    stripped = value.strip()
    if stripped.startswith("$") or stripped.startswith("#!"):
        raise TriageError(f"summary.{name} starts like a command line")
    if stripped.split()[0].lower() in SHELL_WORDS:
        raise TriageError(f"summary.{name} starts with a shell command")


def _check_summary(summary, category):
    if not isinstance(summary, dict) or set(summary) != set(SUMMARY_KEYS):
        raise TriageError(f"summary must have exactly the keys {list(SUMMARY_KEYS)}")
    for name in SUMMARY_KEYS:
        _check_field(name, summary[name])
    if summary["type"] != category:
        raise TriageError("summary.type must equal category")


def parse_decision(text):
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise TriageError(f"the decision is not valid JSON: {exc}")
    if not isinstance(data, dict):
        raise TriageError("the decision must be a JSON object")
    for key in data:
        if key not in KEYS:
            raise TriageError(f"unknown key {key!r}")
    decision = {key: data.get(key) for key in KEYS}
    category = decision["category"]
    if category not in CATEGORIES:
        raise TriageError(f"category must be one of {list(CATEGORIES)}")
    for key in ("duplicate_of", "priority", "severity"):
        value = decision[key]
        if value is not None and not _is_int(value):
            raise TriageError(f"{key} must be an integer")
    for key in ("priority", "severity"):
        value = decision[key]
        if value is not None and not 1 <= value <= 4:
            raise TriageError(f"{key} must be 1 to 4")
    if category == "duplicate" and decision["duplicate_of"] is None:
        raise TriageError("duplicate_of is required for a duplicate")
    if category != "duplicate" and decision["duplicate_of"] is not None:
        raise TriageError("duplicate_of applies to a duplicate only")
    if decision["severity"] is not None and category != "bug":
        raise TriageError("severity applies to bugs only")
    if decision["priority"] is not None and category not in REAL:
        raise TriageError("priority applies to feature, bug and maintenance only")
    comment = decision["comment"]
    if category in ("support", "unclear"):
        if not isinstance(comment, str) or not comment.strip():
            raise TriageError(f"comment is required for {category}")
    elif comment is not None and category != "unsure":
        raise TriageError(f"comment is not used for {category}")
    if comment is not None:
        if not isinstance(comment, str):
            raise TriageError("comment must be a string")
        if len(comment) > COMMENT_MAX:
            raise TriageError(f"comment is over {COMMENT_MAX} characters")
    if category in REAL:
        if decision["summary"] is None:
            raise TriageError(f"summary is required for {category}")
        _check_summary(decision["summary"], category)
    elif decision["summary"] is not None:
        raise TriageError(f"summary is not used for {category}")
    return decision


_URL = re.compile(r"https?://[^\s<>()\[\]\"'`]+", re.I)
_SUMMARY_HEADING = re.compile(r"^## Triage summary[ \t]*$", re.M)
_NEXT_HEADING = re.compile(r"^## ", re.M)


def _allowed_url(url, owner, repo):
    lowered = url.lower().rstrip(".,;:!")
    for base in (f"https://github.com/{owner}/{repo}",
                 f"https://{owner}.github.io/{repo}"):
        base = base.lower()
        if lowered.startswith(base) and (
                len(lowered) == len(base) or lowered[len(base)] in "/?#"):
            return True
    return False


def check_comment(cfg, text, terms, owner, repo):
    """Refuse a comment the privacy scan flags or that links outside the repository."""
    findings = scan.scan_text(text, terms, cfg.data.get("privacy_allow", []))
    if findings:
        raise TriageError("privacy scan found: " + ", ".join(scan.describe(findings)))
    for match in _URL.finditer(text):
        if not _allowed_url(match.group(0), owner, repo):
            raise TriageError(f"comment links outside the repository: {match.group(0)}")


def render_summary(summary, author, maintainers):
    """The `## Triage summary` section text for a decision's summary."""
    lines = ["## Triage summary", ""]
    for key in SUMMARY_KEYS:
        lines.append(f"{key.capitalize()}: {summary[key]}")
    if author.lower() not in {name.lower() for name in maintainers}:
        lines.append(f"Origin: outside (@{author})")
    return "\n".join(lines)


def with_summary(body, section):
    """Replace the `## Triage summary` section of body, or append it."""
    body = body or ""
    found = _SUMMARY_HEADING.search(body)
    if not found:
        if not body.strip():
            return section + "\n"
        return body.rstrip("\n") + "\n\n" + section + "\n"
    after = _NEXT_HEADING.search(body, found.end())
    if not after:
        return body[:found.start()] + section + "\n"
    return body[:found.start()] + section + "\n\n" + body[after.start():]


CLOSING = ("spam", "duplicate", "support")


def load_issue(runner, number):
    """The issue as the forge reports it, or TriageError when it does not exist."""
    try:
        reply = runner(["gh", "issue", "view", str(number), "--json",
                        "number,author,body,labels,state"])
    except runmod.RunError as err:
        raise TriageError(f"issue #{number} could not be read: {err}")
    if not reply or not reply.strip():
        raise TriageError(f"issue #{number} does not exist")
    try:
        issue = json.loads(reply)
    except ValueError:
        raise TriageError(f"issue #{number} could not be read")
    if not isinstance(issue, dict):
        raise TriageError(f"issue #{number} could not be read")
    return issue


def _exists(runner, number):
    try:
        reply = runner(["gh", "issue", "view", str(number), "--json", "number"])
    except runmod.RunError:
        return False
    return bool(reply and reply.strip())


def check(cfg, runner, number, decision, issue, maintainers, terms, owner, repo):
    """Refuse a decision that does not fit the issue. Runs no write command."""
    if str(issue.get("state", "")).upper() == "CLOSED":
        raise TriageError(f"issue #{number} is closed")
    category = decision["category"]
    author = ((issue.get("author") or {}).get("login") or "").lower()
    if category in CLOSING and author in {name.lower() for name in maintainers}:
        raise TriageError("never closes an issue written by a maintainer")
    original = decision.get("duplicate_of")
    if category == "duplicate":
        if original == number:
            raise TriageError("duplicate_of is the issue itself")
        if not _exists(runner, original):
            raise TriageError(f"duplicate_of #{original} does not exist")
    if decision.get("comment"):
        check_comment(cfg, decision["comment"], terms, owner, repo)


UNSURE_TEXT = "Triage could not decide; please look at this issue."


def apply(cfg, runner, number, decision, maintainers, terms, owner, repo):
    """Check a decision against the issue, then act on it. Returns what was done."""
    issue = load_issue(runner, number)
    check(cfg, runner, number, decision, issue, maintainers, terms, owner, repo)
    category = decision["category"]
    if category == "spam":
        state.change(cfg, runner, "close", "issue", number, "orchestrator",
                     "Closed as spam.", terms, close_reason="spam", lock=True)
        return f"closed #{number} as spam and locked it"
    if category == "duplicate":
        original = decision["duplicate_of"]
        state.change(cfg, runner, "close", "issue", number, "orchestrator",
                     f"Closed as a duplicate of #{original}.", terms,
                     close_reason="duplicate", duplicate_of=original)
        return f"closed #{number} as a duplicate of #{original}"
    if category == "support":
        commentmod.post(cfg, runner, "issue", number, "orchestrator", "status",
                        decision["comment"], terms)
        state.change(cfg, runner, "close", "issue", number, "orchestrator",
                     "Closed as a support question; see the comment above.", terms,
                     close_reason="support")
        return f"commented on #{number} and closed it as support"
    if category == "unclear":
        commentmod.post(cfg, runner, "issue", number, "orchestrator", "status",
                        decision["comment"], terms)
        runner(["gh", "issue", "edit", str(number), "--add-label",
                cfg.label("awaiting-feedback")])
        return f"commented on #{number} and marked it awaiting-feedback"
    if category == "unsure":
        url = commentmod.notify(cfg, runner, number, "triage-unsure",
                                decision.get("comment") or UNSURE_TEXT,
                                maintainers, terms)
        if url is None:
            return f"#{number}: already notified"
        return f"notified the maintainers about #{number}"
    return _apply_real(cfg, runner, number, decision, issue, maintainers)


def _apply_real(cfg, runner, number, decision, issue, maintainers):
    category = decision["category"]
    author = (issue.get("author") or {}).get("login", "")
    section = render_summary(decision["summary"], author, maintainers)
    body = with_summary(issue.get("body") or "", section)
    handle, path = tempfile.mkstemp(suffix=".md")
    try:
        with os.fdopen(handle, "w") as out:
            out.write(body)
        runner(["gh", "issue", "edit", str(number), "--body-file", path])
    finally:
        os.unlink(path)
    present = {item["name"] for item in issue.get("labels") or []}
    target = cfg.label(f"type:{category}")
    argv = ["gh", "issue", "edit", str(number), "--add-label", target]
    for other in REAL:
        name = cfg.label(f"type:{other}")
        if name != target and name in present:
            argv += ["--remove-label", name]
    runner(argv)
    if decision.get("priority") is not None:
        labels.set_group(cfg, runner, number, "priority", decision["priority"])
    if decision.get("severity") is not None:
        labels.set_group(cfg, runner, number, "severity", decision["severity"])
    labels.set_status(cfg, runner, number, "status:refinement")
    return f"triaged #{number} as {category} and moved it to refinement"


# Scheduled runs: which issues a run triages, and which it closes as stale.

HANDLED = ("closed:spam", "closed:duplicate", "closed:support", "closed:wont-do",
           "awaiting-feedback", "needs-maintainer")


def _parse(stamp):
    when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _names(item):
    return {label["name"] for label in item.get("labels") or []}


def since_path(owner, repo, home=None):
    return verify._home(home) / f"{owner}-{repo}" / "triage-since"


def resolve_since(cfg, owner, repo, now, home=None):
    """(start time, wrote): the brief's triage.since, else the recorded time, else `now`."""
    brief = cfg.data["triage"]["since"]
    if brief:
        return _parse(brief), False
    path = since_path(owner, repo, home)
    if path.exists():
        return _parse(path.read_text().strip()), False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(now.astimezone(timezone.utc).isoformat() + "\n")
    return now, True


def pending(issues, cfg, since):
    """Open issues created after `since` that triage has not acted on, oldest first."""
    skip = set(cfg.status_labels()) | {cfg.label(name) for name in HANDLED}
    found = [item for item in issues
             if item.get("state") == "OPEN" and _parse(item["createdAt"]) > since
             and not _names(item) & skip]
    return sorted(found, key=lambda item: (_parse(item["createdAt"]), item["number"]))


def split_batch(pending_issues, max_per_run):
    return list(pending_issues[:max_per_run]), list(pending_issues[max_per_run:])


def overdue(waiting, now):
    """The first waiting issue created at least 24 hours before `now`, else None."""
    for item in waiting:
        if now - _parse(item["createdAt"]) >= timedelta(hours=24):
            return item
    return None


def stale(issues, cfg, now, stale_days, maintainers):
    """Numbers of open awaiting-feedback issues quiet for `stale_days`, ascending."""
    label = cfg.label("awaiting-feedback")
    found = []
    for item in issues:
        if item.get("state") != "OPEN" or label not in _names(item):
            continue
        if (item.get("author") or {}).get("login") in maintainers:
            continue
        stamps = [c["createdAt"] for c in item.get("comments") or [] if c.get("createdAt")]
        last = max((_parse(s) for s in stamps), default=_parse(item["createdAt"]))
        if now - last >= timedelta(days=stale_days):
            found.append(item["number"])
    return sorted(found)
