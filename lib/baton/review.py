"""Code reviews on a pull request: the reviewer's verdicts and the developer's replies.

Design spec, section 7a. All state lives in the pull request's comments, so any
session can pick a review loop up. Adapted from the superpowers plugin's
requesting-code-review and receiving-code-review skills.
"""
import hashlib
import json
import os
import re
import tempfile

from . import checklist, comment, gate, run, verify as verifylog

# The one place a verdict is defined. `tasks` is "none" (a `## Tasks` section is
# refused) or "required"; `round` says whether the verdict counts toward review.max_rounds.
VERDICTS = {
    "approve": {"title": "approved", "tasks": "none", "round": False},
    "changes": {"title": "changes requested", "tasks": "required", "round": True},
    "blocked": {"title": "blocked", "tasks": "none", "round": False},
    "replan": {"title": "replan requested", "tasks": "none", "round": True},
}
BLOCKED_CAUSES = ("default-branch", "environment")
VERIFY_STATES = ("green", "red", "timeout", "missing", "none")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_REVIEW = re.compile(
    r"^<!-- baton:reviewer:review verdict=(" + "|".join(VERDICTS) + r") commit=([0-9a-f]{40})"
    r"(?: verify=(green|red|timeout|missing|none))? -->")
_CAUSE = re.compile(r"^Cause: (default-branch|environment)[ \t]*$", re.M)
_FAILURES = re.compile(r"^## Failures[ \t]*$")
_FAILURE_LINE = re.compile(r"^- (\S+?): *\S")
_REASON = re.compile(r"^## Reason[ \t]*$", re.M)
_TASKS = re.compile(r"^## Tasks[ \t]*$", re.M)
_REPLY = f"{gate.MARKER}developer:reply -->"
_REFINE_REPLY = f"{gate.MARKER}refine:reply -->"
_HUNK = re.compile(r"^@@ [^@]* @@.*$")
_COMMENT_ID = re.compile(r"#issuecomment-(\d+)$")


class ReviewError(Exception):
    pass


def compose(verdict, commit, body, verify="missing"):
    if verify not in VERIFY_STATES:
        raise ReviewError(f"unknown verify state {verify!r}")
    if verdict not in VERDICTS:
        raise ReviewError(f"unknown verdict {verdict!r}; use {', '.join(VERDICTS)}")
    if not _SHA.match(commit or ""):
        raise ReviewError("name the reviewed commit by its full 40-character sha")
    if not body.strip():
        raise ReviewError("the review body is empty")
    title = VERDICTS[verdict]["title"]
    return (f"{gate.MARKER}reviewer:review verdict={verdict} commit={commit} verify={verify} -->\n"
            f"**Baton reviewer: review ({title}, commit {commit[:7]})**\n\n{body.strip()}\n")


def _cause(body):
    found = _CAUSE.search(body)
    return found.group(1) if found else None


def failure_ids(body):
    """The ids listed under a `## Failures` heading, one `- <id>: <outcome>` line each."""
    ids = set()
    in_section = False
    for line in (body or "").splitlines():
        if line.startswith("## "):
            in_section = bool(_FAILURES.match(line))
            continue
        if in_section:
            found = _FAILURE_LINE.match(line)
            if found:
                ids.add(found.group(1))
    return ids


def history(comments, owner):
    """Reviews and replies, oldest first. Only the owner's comments count: agents post as the owner."""
    events = []
    for item in comments or []:
        if item.get("author", {}).get("login") != owner:
            continue
        body = (item.get("body") or "").lstrip()
        match = _REVIEW.match(body)
        if match:
            events.append({"kind": "review", "verdict": match.group(1),
                           "commit": match.group(2), "verify": match.group(3) or "missing",
                           "cause": _cause(body) if match.group(1) == "blocked" else None,
                           "failures": failure_ids(body),
                           "url": item.get("url")})
        elif body.startswith(_REPLY) or body.startswith(_REFINE_REPLY):
            events.append({"kind": "reply", "url": item.get("url")})
    return events


def json_default(value):
    """json.dumps fallback: sets (the failure ids) become sorted lists."""
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    return str(value)


def verify_state(cfg, comments, owner, head):
    """The verification result recorded by the latest review of `head`.

    none when no verify command is set or reviews are not required; missing
    when the latest review is of another commit or has no result.
    """
    if not cfg.data["verify"]["command"] or not cfg.data["review"]["required"]:
        return "none"
    reviews = [event for event in history(comments, owner) if event["kind"] == "review"]
    if reviews and reviews[-1]["commit"] == head:
        return reviews[-1]["verify"]
    return "missing"


def state(comments, owner, max_rounds):
    """none, changes, replied, approved, blocked, replan, repeated or escalate; the rounds so far; the last review.

    Rounds count the requests for changes since the last approval: an approval
    that went stale starts the count again. A blocked review is no round and
    does not end the count. `repeated` is the last two requests for changes (blocked
    reviews skipped) sharing a failure id; it is checked on the failure lists alone
    and outranks `escalate`. A `replan` review is a round, and outranks `escalate` too, unless the
    review before it (blocked ones skipped) was also `replan`. A re-refinement changes the plan, so
    the count compared with `max_rounds` restarts: only round reviews after the last `replan` count.
    """
    events = history(comments, owner)
    reviews = [event for event in events if event["kind"] == "review"]
    if not reviews:
        return {"state": "none", "rounds": 0, "review": None}
    last = reviews[-1]
    rounds = 0
    since_replan = 0
    seen_replan = False
    changes = []
    for event in reversed(reviews):
        if event["verdict"] == "approve":
            break
        if VERDICTS[event["verdict"]]["round"]:
            rounds += 1
            changes.append(event)
            if event["verdict"] == "replan":
                seen_replan = True
            elif not seen_replan:
                since_replan += 1
    earlier = [event for event in reviews[:-1] if event["verdict"] != "blocked"]
    replan_twice = (last["verdict"] == "replan" and bool(earlier)
                    and earlier[-1]["verdict"] == "replan")
    repeated = (len(changes) >= 2 and last["verdict"] == "changes"
                and bool(changes[0]["failures"] & changes[1]["failures"]))
    if events[-1]["kind"] == "reply":
        name = "replied"
    elif last["verdict"] == "approve":
        name = "approved"
    elif last["verdict"] == "blocked":
        name = "blocked"
    elif last["verdict"] == "replan":
        name = "escalate" if replan_twice else "replan"
    elif repeated:
        name = "repeated"
    elif since_replan >= max_rounds:
        name = "escalate"
    else:
        name = "changes"
    return {"state": name, "rounds": rounds, "review": last}


def fingerprint(diff_text):
    """A diff's identity without line positions or blob ids.

    Context lines, mode changes and binary data stay in, so a line moved
    elsewhere in a file, a chmod or a changed binary file is a different change.
    """
    lines = []
    for line in diff_text.splitlines():
        if line.startswith("index "):
            continue
        lines.append("@@" if _HUNK.match(line) else line)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _change_id(runner, base_ref, commit, paths):
    base = runner(["git", "merge-base", base_ref, commit]).strip()
    if not base:
        raise run.RunError(f"no merge base for {commit[:7]}")
    argv = ["git", "diff", "--binary", base, commit] + (["--", *paths] if paths else [])
    return fingerprint(runner(argv))


def same_change(runner, base_ref, old, new, paths=()):
    """True when `old` and `new` make the same change against the default branch.

    Merging the default branch into a pull request keeps its change; any other
    edit does not. An unreachable commit is never the same change.
    """
    if new.startswith(old):
        return True
    try:
        return _change_id(runner, base_ref, old, paths) == _change_id(runner, base_ref, new, paths)
    except (run.RunError, OSError):
        return False


def fetch(runner, default_branch, branch):
    try:
        runner(["git", "fetch", "--quiet", "origin", default_branch, branch])
    except (run.RunError, OSError):
        pass


def _linked_plan_path(runner, pr, cfg):
    """The plan file the pull request's one closing issue links to, or None."""
    from . import planfile  # planfile imports checklist
    refs = pr.get("closingIssuesReferences") or []
    if len(refs) != 1:
        return None
    try:
        link = planfile.parse_link(checklist.issue_body(runner, refs[0]["number"]),
                                   cfg.data["docs"]["dir"])
    except (checklist.TaskError, run.RunError, OSError, ValueError, KeyError):
        return None
    if link is None or link["kind"] == "ref":
        return None  # no plan file on the branch
    return link["path"]


def _only_plan_deleted(runner, pr, cfg, default_branch, approved):
    """True when the head differs from the approved commit only by deleting the linked plan file.

    Removing the plan file before the merge is not a change to the code. Editing it,
    or changing any other file under the plans folder, still is.
    """
    path = _linked_plan_path(runner, pr, cfg)
    if path is None:
        return False
    head = pr["headRefOid"]
    try:
        status = runner(["git", "diff", "--name-status", approved, head, "--", path]).split()
    except (run.RunError, OSError):
        return False
    if status != ["D", path]:
        return False
    return same_change(runner, f"origin/{default_branch}", approved, head,
                       [".", f":(exclude){path}"])


def current(pr, owner, cfg, runner, default_branch):
    """The review state at the pull request's head commit: an older approval may be stale."""
    found = state(pr.get("comments"), owner, cfg.data["review"]["max_rounds"])
    if found["state"] == "approved" and found["review"]["commit"] != pr["headRefOid"]:
        fetch(runner, default_branch, pr["headRefName"])
        if not same_change(runner, f"origin/{default_branch}", found["review"]["commit"],
                           pr["headRefOid"], ["."]) and not _only_plan_deleted(
                runner, pr, cfg, default_branch, found["review"]["commit"]):
            found["state"] = "stale"
    return found


def _latest_review(comments, owner):
    """The owner's latest review comment and its marker match, or (None, None)."""
    found = (None, None)
    for item in comments or []:
        if item.get("author", {}).get("login") != owner:
            continue
        match = _REVIEW.match((item.get("body") or "").lstrip())
        if match:
            found = (item, match)
    return found


def in_linked_worktree(runner, cwd):
    """True when cwd is a linked git worktree: its git dir differs from the common dir."""
    base = ["git", "rev-parse", "--path-format=absolute"]
    git_dir = runner(base + ["--git-dir"], cwd=cwd).strip()
    common = runner(base + ["--git-common-dir"], cwd=cwd).strip()
    return git_dir != common


def review_tasks(pr, owner):
    """The tasks of the latest review when it asks for changes; none after an approval."""
    item, match = _latest_review(pr.get("comments"), owner)
    if item is None or VERDICTS[match.group(1)]["tasks"] == "none":
        return {"round": 0, "review": None, "tasks": [], **checklist.progress([])}
    tasks = checklist.tasks_under(item["body"], "tasks")
    rounds = state(pr.get("comments"), owner, 1 << 30)["rounds"]
    return {"round": rounds, "review": item.get("url"), "tasks": tasks,
            **checklist.progress(tasks)}


def tick_review(runner, number, owner, task_id):
    """Tick a task in the latest review. False when it was already ticked."""
    pr = json.loads(runner(["gh", "pr", "view", str(number), "--json", "comments"]))
    item, match = _latest_review(pr.get("comments"), owner)
    if item is None or VERDICTS[match.group(1)]["tasks"] == "none":
        raise ReviewError(f"the latest review on #{number} asks for no changes")
    new, changed = checklist.tick(item["body"], "tasks", task_id)
    if not changed:
        return False
    found = _COMMENT_ID.search(item.get("url") or "")
    if not found:
        raise ReviewError("cannot tell the review comment's id from its URL")
    handle, path = tempfile.mkstemp(suffix=".md")
    try:
        with os.fdopen(handle, "w", newline="") as out:
            out.write(new)
        runner(["gh", "api", "-X", "PATCH",
                f"repos/{{owner}}/{{repo}}/issues/comments/{found.group(1)}",
                "-F", f"body=@{path}", "--silent"])
    finally:
        os.unlink(path)
    return True


def _open_work(runner, pr, owner):
    problems = [f"task {task['id']} of the last review is not done"
                for task in review_tasks(pr, owner)["tasks"] if not task["done"]]
    for ref in pr.get("closingIssuesReferences") or []:
        found = checklist.plan(checklist.issue_body(runner, ref["number"]))
        for task in (found or {}).get("tasks", []):
            if not task["done"]:
                problems.append(f"task {task['id']} of issue #{ref['number']}'s plan is not done")
    return problems


def post(cfg, runner, number, verdict, commit, body, terms, owner, repo=None, home=None):
    """Post a review of `commit`.

    Refuses if the branch moved while it was reviewed, if an approval would
    leave tasks open, or if a request for changes has no task list for its round.
    """
    verify = "none"
    if cfg.data["verify"]["command"]:
        if repo is None:
            repo = json.loads(runner(["gh", "repo", "view", "--json", "name"]))["name"]
        found = verifylog.latest_result(owner, repo, number, commit, home)
        verify = found["result"] if found else "missing"
    text = compose(verdict, commit, body, verify)
    pr = json.loads(runner(["gh", "pr", "view", str(number), "--json",
                            "headRefOid,comments,closingIssuesReferences"]))
    head = pr["headRefOid"]
    if head != commit:
        raise ReviewError(f"the pull request's head is now {head[:7]}, not {commit[:7]}; "
                          "review the new commits and post again")
    if verdict == "approve":
        problems = _open_work(runner, pr, owner)
        if verify not in ("green", "none"):
            problems.append(f"verification of {commit[:7]} is {verify}; "
                            f"run baton verify {number} <worktree>")
        if problems:
            raise ReviewError("cannot approve yet: " + "; ".join(problems))
    elif verdict == "blocked":
        problems = []
        if not _CAUSE.search(body):
            problems.append("name the cause on a line 'Cause: default-branch' or "
                            "'Cause: environment'")
        if not _REASON.search(body):
            problems.append("add a '## Reason' section")
        if _TASKS.search(body):
            problems.append("a blocked review has no '## Tasks' section")
        if problems:
            raise ReviewError("cannot post a blocked review: " + "; ".join(problems))
    elif verdict == "replan":
        problems = []
        where = checklist.span(body, "feedback")
        if where is None or not body[where[0]:where[1]].strip():
            problems.append("add a non-empty '## Feedback' section saying what in the plan "
                            "is wrong and why")
        if _TASKS.search(body):
            problems.append("a replan review has no '## Tasks' section")
        if problems:
            raise ReviewError("cannot post a replan review: " + "; ".join(problems))
    else:
        tasks = checklist.tasks_under(body, "tasks")
        round_ = state(pr.get("comments"), owner, 1 << 30)["rounds"] + 1
        if not tasks:
            raise ReviewError("a request for changes needs a '## Tasks' checklist")
        wrong = [task["id"] for task in tasks
                 if task["done"] or not task["id"].startswith(f"{round_}.")]
        if wrong:
            raise ReviewError(f"this is round {round_}: tasks must be open and numbered "
                              f"{round_}.1, {round_}.2 ... (not {', '.join(wrong)})")
    try:
        return comment.publish(cfg, runner, "pr", number, text, terms)
    except comment.CommentError as error:
        raise ReviewError(str(error)) from error
