"""Triage decisions: parsed and checked before anything is written."""
import json
import re

from . import run as runmod
from . import scan
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
