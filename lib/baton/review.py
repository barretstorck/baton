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

from . import checklist, comment, gate, run

VERDICTS = {"approve", "changes"}
_SHA = re.compile(r"^[0-9a-f]{40}$")
_REVIEW = re.compile(
    r"^<!-- baton:reviewer:review verdict=(approve|changes) commit=([0-9a-f]{40}) -->")
_REPLY = f"{gate.MARKER}developer:reply -->"
_HUNK = re.compile(r"^@@ [^@]* @@.*$")
_COMMENT_ID = re.compile(r"#issuecomment-(\d+)$")


class ReviewError(Exception):
    pass


def compose(verdict, commit, body):
    if verdict not in VERDICTS:
        raise ReviewError(f"unknown verdict {verdict!r}; use approve or changes")
    if not _SHA.match(commit or ""):
        raise ReviewError("name the reviewed commit by its full 40-character sha")
    if not body.strip():
        raise ReviewError("the review body is empty")
    title = "approved" if verdict == "approve" else "changes requested"
    return (f"{gate.MARKER}reviewer:review verdict={verdict} commit={commit} -->\n"
            f"**Baton reviewer: review ({title}, commit {commit[:7]})**\n\n{body.strip()}\n")


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
                           "commit": match.group(2), "url": item.get("url")})
        elif body.startswith(_REPLY):
            events.append({"kind": "reply", "url": item.get("url")})
    return events


def state(comments, owner, max_rounds):
    """none, changes, replied, approved or escalate; the rounds so far; the last review.

    Rounds count the requests for changes since the last approval: an approval
    that went stale starts the count again.
    """
    events = history(comments, owner)
    reviews = [event for event in events if event["kind"] == "review"]
    if not reviews:
        return {"state": "none", "rounds": 0, "review": None}
    last = reviews[-1]
    rounds = 0
    for event in reversed(reviews):
        if event["verdict"] == "approve":
            break
        rounds += 1
    if events[-1]["kind"] == "reply":
        name = "replied"
    elif last["verdict"] == "approve":
        name = "approved"
    elif rounds >= max_rounds:
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


def current(pr, owner, cfg, runner, default_branch):
    """The review state at the pull request's head commit: an older approval may be stale."""
    found = state(pr.get("comments"), owner, cfg.data["review"]["max_rounds"])
    if found["state"] == "approved" and found["review"]["commit"] != pr["headRefOid"]:
        fetch(runner, default_branch, pr["headRefName"])
        if not same_change(runner, f"origin/{default_branch}", found["review"]["commit"],
                           pr["headRefOid"]):
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


def review_tasks(pr, owner):
    """The tasks of the latest review when it asks for changes; none after an approval."""
    item, match = _latest_review(pr.get("comments"), owner)
    if item is None or match.group(1) == "approve":
        return {"round": 0, "review": None, "tasks": [], **checklist.progress([])}
    tasks = checklist.tasks_under(item["body"], "tasks")
    rounds = state(pr.get("comments"), owner, 1 << 30)["rounds"]
    return {"round": rounds, "review": item.get("url"), "tasks": tasks,
            **checklist.progress(tasks)}


def tick_review(runner, number, owner, task_id):
    """Tick a task in the latest review. False when it was already ticked."""
    pr = json.loads(runner(["gh", "pr", "view", str(number), "--json", "comments"]))
    item, match = _latest_review(pr.get("comments"), owner)
    if item is None or match.group(1) == "approve":
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


def post(cfg, runner, number, verdict, commit, body, terms, owner):
    """Post a review of `commit`.

    Refuses if the branch moved while it was reviewed, if an approval would
    leave tasks open, or if a request for changes has no task list for its round.
    """
    text = compose(verdict, commit, body)
    pr = json.loads(runner(["gh", "pr", "view", str(number), "--json",
                            "headRefOid,comments,closingIssuesReferences"]))
    head = pr["headRefOid"]
    if head != commit:
        raise ReviewError(f"the pull request's head is now {head[:7]}, not {commit[:7]}; "
                          "review the new commits and post again")
    if verdict == "approve":
        problems = _open_work(runner, pr, owner)
        if problems:
            raise ReviewError("cannot approve yet: " + "; ".join(problems))
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
