"""The only way a Baton pull request is merged.

Adapted from the acceptance rules in the design spec, section 6, and the
prototype sprint's lesson: never merge until CI has concluded SUCCESS on
the head commit.
"""
import json
import re

from . import gate, labels

PR_FIELDS = ("number,state,isDraft,mergeable,headRefOid,headRefName,baseRefName,"
             "isCrossRepository,body,files,changedFiles,statusCheckRollup,"
             "closingIssuesReferences,commits,comments")
_APPROVE = re.compile(r"^/baton approve\s+([0-9a-f]{7,40})\b")


class PostMergeError(Exception):
    pass
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
    """True when the owner approved exactly this head commit: `/baton approve <sha>`."""
    for comment in pr.get("comments") or []:
        body = comment.get("body", "")
        match = _APPROVE.match(body.strip())
        if (match and gate.MARKER not in body
                and comment.get("author", {}).get("login") == owner
                and pr.get("headRefOid", "").startswith(match.group(1))):
            return True
    return False


def check(pr, cfg, owner, default_branch):
    problems = []
    if pr.get("isCrossRepository"):
        problems.append("pull request comes from a fork; Baton only merges its own branches")
    if pr.get("baseRefName") != default_branch:
        problems.append(f"pull request does not target the default branch ({default_branch})")
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
    texts = [pr.get("body") or ""] + [
        f"{commit.get('messageHeadline', '')}\n{commit.get('messageBody', '')}"
        for commit in pr.get("commits") or []]
    mentioned = {int(n) for text in texts for n in _CLOSING.findall(text)}
    for number in sorted(mentioned - closing):
        problems.append(f"stray closing keyword in the description or a commit message "
                        f"would close #{number}")

    files = pr.get("files") or []
    if pr.get("changedFiles", len(files)) != len(files):
        problems.append("file list is incomplete (too many changed files to verify)")

    protected = tuple(cfg.data["protected_paths"])
    touched = [f["path"] for f in files if protected and f["path"].startswith(protected)]
    if touched and not _approved(pr, owner):
        problems.append("protected paths changed without maintainer approval "
                        f"(comment `/baton approve <head commit sha>`): {', '.join(touched)}")
    return problems


def _worktree_for(branch, runner):
    path = None
    for line in runner(["git", "worktree", "list", "--porcelain"]).splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line == f"branch refs/heads/{branch}":
            return path
    return None


def _after_merge(pr, cfg, runner):
    for ref in pr.get("closingIssuesReferences") or []:
        issue = ref["number"]
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


def merge(number, cfg, runner):
    repo = json.loads(runner(["gh", "repo", "view", "--json", "owner,defaultBranchRef"]))
    pr = json.loads(runner(["gh", "pr", "view", str(number), "--json", PR_FIELDS]))
    if pr.get("state") != "MERGED":
        problems = check(pr, cfg, repo["owner"]["login"], repo["defaultBranchRef"]["name"])
        if problems:
            return problems
        runner(["gh", "pr", "merge", str(number), "--merge",
                "--match-head-commit", pr["headRefOid"]])
    try:
        _after_merge(pr, cfg, runner)
    except Exception as error:
        raise PostMergeError(
            f"#{number} is merged, but a follow-up step failed ({error}); "
            f"run `baton merge-pr {number}` again to finish") from error
    return []
