"""One read of the forge that tells the orchestrator where the sprint stands.

The orchestrator keeps no memory between passes (design spec, section 6);
`snapshot` rebuilds everything it needs from the milestone, labels, pull
requests, comments and the default branch's checks.
"""
import json
import os
import re
import time

from . import comment, config, issue as issue_fields, merge

_FAILED = {"failure", "timed_out", "startup_failure", "action_required"}
_ORDER = re.compile(r"^\s*Order:(.*)$", re.M | re.I)
ISSUE_FIELDS = "number,title,state,labels,body,author,comments"
PR_FIELDS = "number,headRefName,isDraft,mergeable,closingIssuesReferences,statusCheckRollup"


def branch_state(runs):
    """green, red, pending or none, judged on the newest commit's workflow runs."""
    if not runs:
        return "none"
    latest = [run for run in runs if run.get("headSha") == runs[0].get("headSha")]
    if any((run.get("conclusion") or "").lower() in _FAILED for run in latest):
        return "red"
    if any((run.get("status") or "").lower() != "completed" for run in latest):
        return "pending"
    return "green"


def overlaps(first, second):
    for a in first:
        for b in second:
            if a == b or (a.endswith("/") and b.startswith(a)) or (b.endswith("/") and a.startswith(b)):
                return True
    return False


def _pr_ci(pr, cfg):
    if not pr.get("statusCheckRollup"):
        return "none"
    problems = merge._check_ci(pr, cfg)
    if any("not finished" in problem for problem in problems):
        return "pending"
    return "red" if problems else "green"


def _pick_sprint(milestones, wanted):
    sprints = sorted((m for m in milestones if m["title"].startswith("Sprint")),
                     key=lambda m: m["number"])
    if wanted:
        named = [m for m in milestones if m["title"] == wanted]
        return (named[0] if named else None), [m["title"] for m in sprints]
    return (sprints[0] if sprints else None), [m["title"] for m in sprints]


def _blocker_open(number, by_number, runner):
    if number in by_number:
        return by_number[number]["state"] == "OPEN"
    try:
        return json.loads(runner(["gh", "issue", "view", str(number), "--json", "state"]))["state"] == "OPEN"
    except (ValueError, KeyError):
        return True


def snapshot(cfg, runner, milestone=None):
    repo = json.loads(runner(["gh", "repo", "view", "--json", "owner,name,defaultBranchRef"]))
    owner, branch = repo["owner"]["login"], repo["defaultBranchRef"]["name"]
    milestones = json.loads(runner(
        ["gh", "api", f"repos/{owner}/{repo['name']}/milestones?state=open&per_page=100"]) or "[]")
    chosen, open_sprints = _pick_sprint(milestones, milestone)
    runs = json.loads(runner(["gh", "run", "list", "--branch", branch, "--limit", "20",
                              "--json", "status,conclusion,headSha,workflowName"]) or "[]")
    result = {
        "sprint": None, "open_sprints": open_sprints,
        "default_branch": {"name": branch, "state": branch_state(runs)},
        "repair_issue": None, "free_slots": 0, "in_dev": [], "in_review": [],
        "verification": [], "answered": [], "waiting_on_maintainer": [],
        "dispatchable": [], "held": [], "finished": False, "issues": [],
    }
    if chosen is None:
        return result
    result["sprint"] = {"number": chosen["number"], "title": chosen["title"],
                        "description": chosen.get("description") or "", "due": chosen.get("due_on")}

    issues = json.loads(runner(["gh", "issue", "list", "--milestone", chosen["title"], "--state",
                                "all", "--limit", "300", "--json", ISSUE_FIELDS]) or "[]")
    prs = json.loads(runner(["gh", "pr", "list", "--state", "open", "--limit", "200",
                             "--json", PR_FIELDS]) or "[]")
    pr_for = {ref["number"]: pr for pr in prs for ref in pr.get("closingIssuesReferences") or []}
    by_number = {item["number"]: item for item in issues}
    status_of = {cfg.label(name): name for name in config.STATUSES}
    repair_label = cfg.label("broken-default-branch")
    order = [int(n) for line in _ORDER.findall(result["sprint"]["description"])
             for n in re.findall(r"#(\d+)", line)]

    ready, busy_files, waiting = [], [], set()
    for item in sorted(issues, key=lambda entry: entry["number"]):
        number = item["number"]
        labels = {label["name"] for label in item.get("labels") or []}
        status = next((status_of[name] for name in labels if name in status_of), None)
        tier = ("deliverable" if cfg.label("deliverable") in labels
                else "stretch" if cfg.label("stretch") in labels else None)
        files = issue_fields.expected_files(item.get("body"))
        repair = repair_label in labels
        result["issues"].append({"number": number, "title": item["title"], "status": status,
                                 "tier": tier, "state": item["state"], "repair": repair})
        if repair and item["state"] == "OPEN":
            result["repair_issue"] = number
        if cfg.label("needs-maintainer") in labels and status != "status:complete":
            waiting.add(number)
        if status == "status:in-dev":
            result["in_dev"].append(number)
            busy_files.append(files)
        elif status == "status:in-review":
            pr = pr_for.get(number)
            result["in_review"].append({
                "number": number, "pr": pr["number"] if pr else None,
                "draft": pr["isDraft"] if pr else None,
                "ci": _pr_ci(pr, cfg) if pr else "none",
                "mergeable": pr["mergeable"] if pr else None})
            busy_files.append(files)
        elif status == "status:verification":
            result["verification"].append(number)
        elif status == "status:blocked":
            pending = comment.pending_answers(item, owner)
            if pending["answers"]:
                result["answered"].append({"number": number, **pending})
            else:
                waiting.add(number)
        elif status == "status:ready" and item["state"] == "OPEN":
            ready.append((item, tier, files, repair))

    def rank(entry):
        item, tier, _, repair = entry
        number = item["number"]
        return (0 if repair else 1, {"deliverable": 0, "stretch": 1}.get(tier, 2),
                order.index(number) if number in order else len(order), number)

    red = result["default_branch"]["state"] == "red"
    slots = max(cfg.data["developer"]["max_parallel"] - len(result["in_dev"]), 0)
    result["free_slots"] = slots
    for item, tier, files, repair in sorted(ready, key=rank):
        number = item["number"]
        open_blockers = [n for n in issue_fields.blocked_by(item.get("body"))
                         if _blocker_open(n, by_number, runner)]
        if item.get("author", {}).get("login") != owner:
            reason = "written by someone other than the maintainer"
        elif red and not repair:
            reason = "default branch is red; only the repair issue may proceed"
        elif open_blockers:
            reason = "blocked by " + ", ".join(f"#{n}" for n in open_blockers)
        elif any(overlaps(files, other) for other in busy_files):
            reason = "expected files overlap with work in flight"
        elif len(result["dispatchable"]) >= slots:
            reason = "no free developer slot"
        else:
            result["dispatchable"].append(number)
            busy_files.append(files)
            continue
        result["held"].append({"number": number, "reason": reason})

    result["waiting_on_maintainer"] = sorted(waiting)
    result["finished"] = bool(issues) and all(
        entry["status"] == "status:complete" or entry["number"] in waiting
        for entry in result["issues"])
    return result


def lock_acquire(path, holder, ttl_hours):
    """Take the run lock. False when another run holds a lock younger than the limit."""
    path = str(path)
    for _ in range(2):
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(path) < ttl_hours * 3600:
                    return False
                os.unlink(path)
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(handle, "w") as out:
            out.write(f"{holder}\n{time.strftime('%Y-%m-%dT%H:%M:%S%z')}\n")
        return True
    return False


def lock_release(path):
    try:
        os.unlink(str(path))
    except FileNotFoundError:
        pass
