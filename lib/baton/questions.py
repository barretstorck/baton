"""Open questions for the maintainer: which issues need an answer, most important first."""
from datetime import datetime

from . import comment, issue as issue_fields


def _parse(stamp):
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _labels(item):
    return {label["name"] for label in item.get("labels") or []}


def open_questions(issues, cfg, owner, now):
    """The open issues waiting on the maintainer, sorted by importance.

    An issue is listed once, with `kind` the first that applies: an unanswered agent
    question, the blocked status, or the needs-maintainer label. `now` is timezone-aware.
    """
    open_issues = [item for item in issues if item.get("state") == "OPEN"]
    blocks = {}
    for item in open_issues:
        for number in issue_fields.blocked_by(item.get("body")):
            blocks.setdefault(number, set()).add(item["number"])
    blocked_label = cfg.label("status:blocked")
    maintainer_label = cfg.label("needs-maintainer")
    entries = []
    for item in open_issues:
        pending = comment.pending_answers(item, owner)
        asked = pending["question"] if not pending["answers"] else None
        names = _labels(item)
        if asked:
            kind = "question"
        elif blocked_label in names:
            kind = "blocked"
        elif maintainer_label in names:
            kind = "needs-maintainer"
        else:
            continue
        age = None
        if asked:
            created = next((c.get("createdAt") for c in item.get("comments") or []
                            if c.get("url") == asked), None)
            if created:
                age = max(0, (now - _parse(created)).days)
        tier = next((name for name in ("deliverable", "stretch")
                     if cfg.label(name) in names), None)
        entries.append({
            "number": item["number"], "title": item.get("title"), "kind": kind,
            "question_url": pending["question"], "tier": tier,
            "repair": cfg.label("broken-default-branch") in names,
            "blocks": sorted(n for n in blocks.get(item["number"], ()) if n != item["number"]),
            "age_days": age,
        })
    tier_rank = {"deliverable": 0, "stretch": 1, None: 2}
    entries.sort(key=lambda e: (
        not e["repair"], -len(e["blocks"]), tier_rank[e["tier"]],
        e["age_days"] is None, -(e["age_days"] or 0), e["number"]))
    return entries
