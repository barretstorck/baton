"""Closing and reopening issues and pull requests, always with a marker comment."""
from . import comment

ACTIONS = {"close": "closed", "reopen": "reopened"}


def change(cfg, runner, action, target, number, role, reason, terms):
    """Post a `closed` or `reopened` marker comment, then close or reopen.

    Returns the marker comment's URL. The comment goes first, so an agent's
    change is never unmarked; nothing is changed when the comment is refused.
    """
    if action not in ACTIONS:
        raise comment.CommentError(f"unknown action {action!r}; use close or reopen")
    if target not in comment.TARGETS:
        raise comment.CommentError(f"unknown target {target!r}; use issue or pr")
    if not (reason or "").strip():
        raise comment.CommentError("a reason is required")
    url = comment.post(cfg, runner, target, number, role, ACTIONS[action], reason, terms)
    runner(["gh", target, action, str(number)])
    return url
