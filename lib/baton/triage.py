"""Triage decisions: parsed and checked before anything is written."""
import json

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
