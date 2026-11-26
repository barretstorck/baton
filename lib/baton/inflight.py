"""In-flight task records: a developer marks a task as started and ends the record on release."""

import re
import secrets
from datetime import datetime, timedelta, timezone

from . import config, gate

_START = re.compile(r"In flight: task (\S+) \(run ([0-9a-f]+)\)\s*$")
_RELEASE = re.compile(r"Released: task (\S+) \(run ([0-9a-f]+)\)\s*$")
_STATUS = gate.MARKER + "developer:status"


def new_run():
    """A fresh run token: 8 lowercase hex digits."""
    return secrets.token_hex(4)


def start_body(task, run):
    return (f"In flight: task {task} (run {run})\n\n"
            "A developer started this task; the record ends when the task is ticked or released.")


def release_body(task, run):
    return f"Released: task {task} (run {run})"


def _text(rows):
    """The first non-empty line after the marker line and the bold heading line."""
    for row in rows[1:]:
        if row.strip() and not row.startswith("**Baton "):
            return row.strip()
    return ""


def current(comments, tasks, logins):
    """The latest open in-flight record, or None.

    Only developer status comments by `logins` count. A record is open while its task is
    unticked and no later `Released:` comment has the same task and run."""
    open_tasks = {str(t.get("id")) for t in tasks or [] if not t.get("done")}
    events = []
    for item in comments or []:
        rows = (item.get("body") or "").splitlines()
        if not rows or not rows[0].startswith(_STATUS):
            continue
        if not config.has_login(item.get("author"), list(logins)):
            continue
        text = _text(rows)
        for kind, pattern in (("start", _START), ("release", _RELEASE)):
            match = pattern.match(text)
            if match:
                events.append((kind, match.group(1), match.group(2), item))
    released = set()
    for kind, task, run, item in reversed(events):
        if kind == "release":
            released.add((task, run))
        elif task in open_tasks and (task, run) not in released:
            return {"task": task, "run": run, "since": item.get("createdAt"), "url": item.get("url")}
    return None


def _parse(stamp):
    when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def is_stale(record, last_push, now, minutes):
    """Whether the newer of the record time and `last_push` is over `minutes` before `now`."""
    newest = _parse(record["since"])
    if last_push:
        newest = max(newest, _parse(last_push))
    return now - newest > timedelta(minutes=minutes)
