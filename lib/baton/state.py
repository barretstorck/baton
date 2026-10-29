"""Closing and reopening issues and pull requests, always with a marker comment."""
from . import comment, run

ACTIONS = {"close": "closed", "reopen": "reopened"}
REASONS = ("spam", "duplicate", "support", "wont-do", "completed")
_NATIVE = {"spam": "not planned", "support": "not planned", "wont-do": "not planned",
           "duplicate": "duplicate", "completed": "completed"}


def change(cfg, runner, action, target, number, role, reason, terms,
           close_reason=None, duplicate_of=None, lock=False):
    """Post a `closed` or `reopened` marker comment, then close or reopen.

    Returns the marker comment's URL. The comment goes first, so an agent's
    change is never unmarked; nothing is changed when the comment is refused.
    A close reason adds its label and the forge's native close reason.
    """
    if action not in ACTIONS:
        raise comment.CommentError(f"unknown action {action!r}; use close or reopen")
    if target not in comment.TARGETS:
        raise comment.CommentError(f"unknown target {target!r}; use issue or pr")
    if not (reason or "").strip():
        raise comment.CommentError("a reason is required")
    _check_options(runner, action, target, number, close_reason, duplicate_of, lock)
    url = comment.post(cfg, runner, target, number, role, ACTIONS[action], reason, terms)
    if close_reason is None:
        runner(["gh", target, action, str(number)])
    else:
        if close_reason != "completed":
            runner(["gh", "issue", "edit", str(number), "--add-label",
                    cfg.label(f"closed:{close_reason}")])
        argv = ["gh", "issue", "close", str(number), "--reason", _NATIVE[close_reason]]
        if close_reason == "duplicate":
            argv += ["--duplicate-of", str(duplicate_of)]
        runner(argv)
    if lock:
        argv = ["gh", "issue", "lock", str(number)]
        if close_reason == "spam":
            argv += ["--reason", "spam"]
        runner(argv)
    if close_reason == "duplicate":
        comment.post(cfg, runner, "issue", duplicate_of, role, "status",
                     f"Closed #{number} as a duplicate of this issue.", terms)
    return url


def _check_options(runner, action, target, number, close_reason, duplicate_of, lock):
    if close_reason is not None and close_reason not in REASONS:
        raise comment.CommentError(f"unknown reason {close_reason!r}; use one of {', '.join(REASONS)}")
    if (close_reason is not None or lock or duplicate_of is not None) and (
            action != "close" or target != "issue"):
        raise comment.CommentError("--reason, --duplicate-of and --lock apply only to closing an issue")
    if duplicate_of is not None and close_reason != "duplicate":
        raise comment.CommentError("--duplicate-of needs --reason duplicate")
    if close_reason == "duplicate":
        if duplicate_of is None:
            raise comment.CommentError("--reason duplicate needs --duplicate-of <N>")
        if duplicate_of == number:
            raise comment.CommentError("an issue cannot be a duplicate of itself")
        try:
            found = runner(["gh", "issue", "view", str(duplicate_of), "--json", "number"])
        except run.RunError:
            found = ""
        if not (found or "").strip():
            raise comment.CommentError(f"issue #{duplicate_of} was not found")
