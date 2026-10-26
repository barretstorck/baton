"""One read of the forge that tells the orchestrator where the sprint stands.

The orchestrator keeps no memory between passes (design spec, section 6);
`snapshot` rebuilds everything it needs from the milestone, labels, pull
requests, comments and the default branch's checks.
"""
import json
import os
import re
import time
from datetime import datetime, timezone

from . import checklist, comment, config, issue as issue_fields, merge, review
from .run import RunError

_FAILED = {"failure", "timed_out", "startup_failure", "action_required"}
_ORDER = re.compile(r"^\s*Order:(.*)$", re.M | re.I)
_PARALLEL = re.compile(r"^\s*Parallel:\s*(\d+)\s*$", re.M | re.I)
PUSH_RUN_GRACE_SECONDS = 300
STALE_MISSING_PATHS = 2  # hold a ready issue whose plan names at least this many missing paths
BEHIND_LIMIT = 15  # default-branch commits behind that count as far behind
_ON_LINE = re.compile(r"""^["']?on["']?\s*:(.*)$""")
_PUSH_WORD = re.compile(r"(?<![\w-])push(?![\w-])")
ISSUE_FIELDS = "number,title,state,labels,body,author,comments"
PR_FIELDS = ("number,headRefName,headRefOid,isDraft,mergeable,closingIssuesReferences,"
             "statusCheckRollup,comments")


class SnapshotError(Exception):
    """Forge data could not be read; the snapshot must not look valid."""


def _load(runner, argv, what):
    """Run a forge read that must answer with a JSON list; never turn a bad reply into []."""
    text = runner(argv)
    if not (text or "").strip():
        raise SnapshotError(f"could not read {what}: empty reply")
    try:
        data = json.loads(text)
    except ValueError as error:
        raise SnapshotError(f"could not read {what}: invalid JSON ({error})") from error
    if not isinstance(data, list):
        raise SnapshotError(f"could not read {what}: expected a list")
    return data


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


def triggers_on_push(workflow_text):
    """True when a workflow file's `on:` section mentions push (inline, list or block form)."""
    lines = (workflow_text or "").splitlines()
    for index, line in enumerate(lines):
        found = _ON_LINE.match(line)
        if not found:
            continue
        inline = found.group(1).split("#", 1)[0].strip()
        if inline:
            return bool(_PUSH_WORD.search(inline))
        block = []
        for follow in lines[index + 1:]:
            content = follow.split("#", 1)[0].rstrip()
            if not content.strip():
                continue
            if not follow[0].isspace() and not content.lstrip().startswith("-"):
                break
            block.append(content)
        if not block:
            return False
        level = min(len(item) - len(item.lstrip()) for item in block)
        for item in block:
            if len(item) - len(item.lstrip()) != level:
                continue
            key = item.strip().lstrip("-").strip()
            if re.match(r"^[\"']?push[\"']?\s*(:|$)", key):
                return True
        return False
    return False


def push_workflow_exists(runner, owner, repo, sha):
    """True when a workflow file at this commit triggers on push; a missing directory means no."""
    base = f"repos/{owner}/{repo}/contents/.github/workflows"
    try:
        entries = _load(runner, ["gh", "api", f"{base}?ref={sha}"], "workflow files")
    except RunError as error:
        if "404" in str(error) or "Not Found" in str(error):
            return False
        raise
    for entry in entries:
        name = entry.get("name", "") if isinstance(entry, dict) else ""
        if not name.endswith((".yml", ".yaml")):
            continue
        text = runner(["gh", "api", f"{base}/{name}?ref={sha}",
                       "-H", "Accept: application/vnd.github.raw"])
        if triggers_on_push(text):
            return True
    return False


def _commit_age(committed, now):
    when = datetime.fromisoformat(committed.replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return ((now or datetime.now(timezone.utc)) - when).total_seconds()


def actions_enabled(runner, owner, repo):
    """False only when the forge says Actions is disabled; an unreadable reply counts as enabled."""
    try:
        text = runner(["gh", "api", f"repos/{owner}/{repo}/actions/permissions"])
    except RunError as error:
        if "404" in str(error) or "Not Found" in str(error):
            return True
        raise
    try:
        data = json.loads(text) if (text or "").strip() else {}
    except ValueError:
        return True
    return not (isinstance(data, dict) and data.get("enabled") is False)


def default_branch_status(runner, owner, repo, branch, now=None):
    """(state, grace_left_seconds) for the default branch's head commit, push runs only.

    state is green, red, pending or none; grace_left is an integer only while pending means
    "no run yet, inside the grace period", otherwise None."""
    try:
        head = json.loads(runner(["gh", "api", f"repos/{owner}/{repo}/commits/{branch}"]))
        sha = head["sha"]
        committed = head["commit"]["committer"]["date"]
    except (ValueError, KeyError, TypeError) as error:
        raise SnapshotError(f"could not read the head commit of {branch}: {error}") from error
    runs = _load(runner, ["gh", "run", "list", "--branch", branch, "--commit", sha,
                          "--event", "push", "--limit", "20",
                          "--json", "status,conclusion,headSha,workflowName"], "workflow runs")
    if runs:
        return branch_state(runs), None
    try:
        age = _commit_age(committed, now)
    except ValueError as error:
        raise SnapshotError(f"could not read the head commit date: {error}") from error
    if (age < PUSH_RUN_GRACE_SECONDS and actions_enabled(runner, owner, repo)
            and push_workflow_exists(runner, owner, repo, sha)):
        return "pending", max(1, int(PUSH_RUN_GRACE_SECONDS - age))
    return "none", None


def default_branch_state(runner, owner, repo, branch, now=None):
    """green, red, pending or none for the default branch's head commit, push runs only."""
    return default_branch_status(runner, owner, repo, branch, now)[0]


def overlaps(first, second):
    for a in first:
        for b in second:
            if a == b or (a.endswith("/") and b.startswith(a)) or (b.endswith("/") and a.startswith(b)):
                return True
    return False


def _pr_ci(pr, cfg, runner=None):
    if not pr.get("statusCheckRollup"):
        return "none" if cfg.data["merge"]["allow_no_ci"] else "pending"
    problems = merge._check_ci(pr, cfg, runner)
    if any("not finished" in problem or "every check was skipped" in problem
           for problem in problems):
        return "pending"
    if problems and all(problem.endswith(" concluded CANCELLED") for problem in problems):
        return "cancelled"
    return "red" if problems else "green"


PR_STATE_FIELDS = "isDraft,statusCheckRollup,headRefOid,comments"  # headRefOid lets _settle settle lagging runs


def _pr_verify(pr, cfg, runner, owner):
    """The verification state recorded by the latest review of the pull request's head."""
    if not cfg.data["verify"]["command"] or not cfg.data["review"]["required"]:
        return "none"
    if owner is None:
        owner = json.loads(runner(["gh", "repo", "view", "--json", "owner"]))["owner"]["login"]
    return review.verify_state(cfg, pr.get("comments"), owner, pr.get("headRefOid"))


def pr_state(pr, cfg, runner=None, owner=None):
    """The draft, CI and verify state of one pull request, as sprint-status reads them."""
    return {"draft": bool(pr["isDraft"]), "ci": _pr_ci(pr, cfg, runner),
            "problems": merge._check_ci(pr, cfg, runner),
            "verify": _pr_verify(pr, cfg, runner, owner)}


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


def _next_task_url(owner, name, link, text, found):
    """Link to the next open task's box in a plan kept on a ref, else None."""
    if not link or link.get("kind") != "ref" or not found or not found["next"]:
        return None
    line = checklist.task_line(text, found["next"])
    if line is None:
        return None
    return (f"https://github.com/{owner}/{name}/blob/{link['ref']}/{link['path']}"
            f"?plain=1#L{line}")


def snapshot(cfg, runner, milestone=None):
    repo = json.loads(runner(["gh", "repo", "view", "--json", "owner,name,defaultBranchRef"]))
    owner, branch = repo["owner"]["login"], repo["defaultBranchRef"]["name"]
    milestones = _load(
        runner, ["gh", "api", f"repos/{owner}/{repo['name']}/milestones?state=open&per_page=100"],
        "milestones")
    chosen, open_sprints = _pick_sprint(milestones, milestone)
    state, grace_left = default_branch_status(runner, owner, repo["name"], branch)
    result = {
        "sprint": None, "open_sprints": open_sprints,
        "default_branch": {"name": branch, "state": state, "grace_ends_in_seconds": grace_left},
        "repair_issue": None, "parallel": 0, "free_slots": 0, "in_dev": [], "in_review": [],
        "verification": [], "re_refine": [], "answered": [], "waiting_on_maintainer": [],
        "dispatchable": [], "held": [], "finished": False, "issues": [], "progress": {},
        "waiting_links": {},
    }
    if chosen is None:
        return result
    result["sprint"] = {"number": chosen["number"], "title": chosen["title"],
                        "description": chosen.get("description") or "", "due": chosen.get("due_on")}

    issues = _load(runner, ["gh", "issue", "list", "--milestone", chosen["title"], "--state",
                            "all", "--limit", "300", "--json", ISSUE_FIELDS], "issues")
    prs = _load(runner, ["gh", "pr", "list", "--state", "open", "--limit", "200",
                         "--json", PR_FIELDS], "pull requests")
    pr_for = {ref["number"]: pr for pr in prs for ref in pr.get("closingIssuesReferences") or []}
    by_number = {item["number"]: item for item in issues}
    status_of = {cfg.label(name): name for name in config.STATUSES}
    repair_label = cfg.label("broken-default-branch")
    order = [int(n) for line in _ORDER.findall(result["sprint"]["description"])
             for n in re.findall(r"#(\d+)", line)]
    parallel = _PARALLEL.search(result["sprint"]["description"])
    result["parallel"] = (int(parallel.group(1)) if parallel
                          else cfg.data["developer"]["max_parallel"])

    ready, busy_files, waiting = [], [], set()
    plan_cache = {}
    tracked = []  # the default branch's files, fetched once and only when a plan needs them

    def default_branch_files():
        if not tracked:
            try:
                tracked.append(tracked_paths(runner, branch))
            except RunError:
                tracked.append(set())
        return tracked[0]

    def staleness(text, found):
        if not found or not found["tasks"]:
            return None
        files = default_branch_files()
        named = checklist.named_paths(text)
        base = found.get("written_against")
        return {"missing_paths": [path for path in named if path not in files] if files else [],
                "plan_behind": commits_behind(runner, base, branch) if base else None}

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
        if status in ("status:ready", "status:in-dev", "status:blocked", "status:in-review") \
                and item["state"] == "OPEN":
            pr = pr_for.get(number)
            try:
                text, link = checklist.plan_source(runner, item.get("body") or "", plan_cache,
                                                  cfg.data["docs"]["dir"])
            except checklist.TaskError:
                text, link = "", None
            found = checklist.plan(text)
            fixes = review.review_tasks(pr, owner) if pr else None
            spike = cfg.label("type:spike") in labels
            result["progress"][str(number)] = {
                "pr": pr["number"] if pr else None,
                "spike": spike,
                "unplanned": not spike and not (found and found["tasks"]),
                "plan": checklist.progress(found["tasks"]) if found else None,
                "next_task_url": _next_task_url(owner, repo["name"], link, text, found),
                "review": ({key: fixes[key] for key in ("round", "done", "total", "next")}
                           if fixes and fixes["total"] else None),
                "review_without_tasks": bool(fixes and fixes["review"] and not fixes["total"]),
                "stale": staleness(text, found),
            }
        if status == "status:in-dev":
            result["in_dev"].append(number)
            busy_files.append(files)
        elif status == "status:in-review":
            pr = pr_for.get(number)
            if pr and cfg.data["review"]["required"]:
                found = review.current(pr, owner, cfg, runner, branch)
                review_state, rounds = found["state"], found["rounds"]
            else:
                review_state, rounds = "not-required", 0
            result["in_review"].append({
                "number": number, "pr": pr["number"] if pr else None,
                "draft": pr["isDraft"] if pr else None,
                "ci": _pr_ci(pr, cfg, runner) if pr else "none",
                "mergeable": pr["mergeable"] if pr else None,
                "review": review_state, "rounds": rounds,
                "verify": _pr_verify(pr, cfg, runner, owner) if pr else "none"})
            busy_files.append(files)
        elif status == "status:verification":
            result["verification"].append(number)
        elif status == "status:refinement" and item["state"] == "OPEN":
            pr = pr_for.get(number)
            if pr and cfg.label("needs-maintainer") not in labels:
                result["re_refine"].append({"number": number, "pr": pr["number"],
                                            "draft": pr["isDraft"]})
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

    def missing_count(number):
        stale = (result["progress"].get(str(number)) or {}).get("stale")
        return len(stale["missing_paths"]) if stale else 0

    red = result["default_branch"]["state"] == "red"
    slots = max(result["parallel"] - len(result["in_dev"]), 0)
    result["free_slots"] = slots
    for item, tier, files, repair in sorted(ready, key=rank):
        number = item["number"]
        open_blockers = [n for n in issue_fields.blocked_by(item.get("body"))
                         if _blocker_open(n, by_number, runner)]
        if item.get("author", {}).get("login") != owner:
            reason = "written by someone other than the maintainer"
        elif red and not repair:
            reason = "default branch is red; only the repair issue may proceed"
        elif missing_count(number) >= STALE_MISSING_PATHS:
            reason = f"plan names {missing_count(number)} missing paths; re-refine"
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
    for number in result["waiting_on_maintainer"]:
        pending = comment.pending_answers(by_number[number], owner)
        result["waiting_links"][str(number)] = (
            pending["question"] if pending["question"] and not pending["answers"] else None)
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


def tracked_paths(runner, branch):
    """The files tracked on origin/<branch>, after fetching it."""
    runner(["git", "fetch", "--quiet", "origin", branch])
    out = runner(["git", "ls-tree", "-r", "--name-only", f"origin/{branch}"])
    return {line for line in (out or "").splitlines() if line}


def commits_behind(runner, base, branch):
    """How many commits origin/<branch> has that <base> does not; None when git cannot say."""
    try:
        return int(runner(["git", "rev-list", "--count", f"{base}..origin/{branch}"]).strip())
    except (RunError, ValueError):
        return None
