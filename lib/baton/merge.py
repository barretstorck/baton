"""The only way a Baton pull request is merged.

Adapted from the acceptance rules in the design spec, section 6, and the
prototype sprint's lesson: never merge until CI has concluded SUCCESS on
the head commit.
"""
import json
import re

from . import gate, labels

PR_FIELDS = ("number,state,isDraft,mergeable,headRefOid,headRefName,body,files,"
             "statusCheckRollup,closingIssuesReferences,commits,comments")
_CLOSING = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b:?\s+#(\d+)", re.I)
_PASSING = {"SUCCESS", "SKIPPED", "NEUTRAL"}


def _check_ci(pr, cfg):
    rollup = pr.get("statusCheckRollup") or []
    if not rollup:
        if cfg.data["merge"]["allow_no_ci"]:
            return []
        return ["no CI checks are registered on the head commit yet"]
    problems = []
    for item in rollup:
        if item.get("__typename") == "StatusContext":
            name, done, result = item.get("context"), item.get("state") != "PENDING", item.get("state")
        else:
            name, done, result = item.get("name"), item.get("status") == "COMPLETED", item.get("conclusion")
        if not done:
            problems.append(f"check {name!r} has not finished")
        elif result not in _PASSING:
            problems.append(f"check {name!r} concluded {result}")
    return problems


def _approved(pr, owner):
    head_time = pr["commits"][-1]["committedDate"] if pr.get("commits") else ""
    for comment in pr.get("comments") or []:
        body = comment.get("body", "")
        if (comment.get("author", {}).get("login") == owner
                and gate.MARKER not in body
                and body.strip().startswith("/baton approve")
                and comment.get("createdAt", "") > head_time):
            return True
    return False


def check(pr, cfg, owner):
    problems = []
    if pr.get("state") != "OPEN":
        problems.append(f"pull request is not open (state {pr.get('state')})")
    if pr.get("isDraft"):
        problems.append("pull request is still a draft")
    if pr.get("mergeable") != "MERGEABLE":
        problems.append(f"pull request is not mergeable ({pr.get('mergeable')})")
    problems.extend(_check_ci(pr, cfg))

    closing = {ref["number"] for ref in pr.get("closingIssuesReferences") or []}
    if len(closing) != 1:
        problems.append(f"pull request must close exactly one issue, found {sorted(closing)}")
    for number in sorted({int(n) for n in _CLOSING.findall(pr.get("body") or "")} - closing):
        problems.append(f"stray closing keyword would close #{number}")

    protected = tuple(cfg.data["protected_paths"])
    touched = [f["path"] for f in pr.get("files") or [] if protected and f["path"].startswith(protected)]
    if touched and not _approved(pr, owner):
        problems.append("protected paths changed without maintainer approval "
                        f"(`/baton approve` comment after the last commit): {', '.join(touched)}")
    return problems


def _worktree_for(branch, runner):
    path = None
    for line in runner(["git", "worktree", "list", "--porcelain"]).splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line == f"branch refs/heads/{branch}":
            return path
    return None


def merge(number, cfg, runner):
    owner = json.loads(runner(["gh", "repo", "view", "--json", "owner"]))["owner"]["login"]
    pr = json.loads(runner(["gh", "pr", "view", str(number), "--json", PR_FIELDS]))
    problems = check(pr, cfg, owner)
    if problems:
        return problems

    runner(["gh", "pr", "merge", str(number), "--merge", "--match-head-commit", pr["headRefOid"]])

    issue = pr["closingIssuesReferences"][0]["number"]
    state = json.loads(runner(["gh", "issue", "view", str(issue), "--json", "state"]))["state"]
    if state == "OPEN":
        runner(["gh", "issue", "close", str(issue)])
    labels.set_status(cfg, runner, issue, "status:verification")

    branch = pr["headRefName"]
    worktree = _worktree_for(branch, runner)
    if worktree:
        runner(["git", "worktree", "remove", "--force", worktree])
    runner(["git", "branch", "-D", branch])
    runner(["git", "push", "origin", "--delete", branch])
    return []
