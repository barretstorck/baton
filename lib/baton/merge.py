"""The only way a Baton pull request is merged.

Adapted from the acceptance rules in the design spec, section 6, and the
prototype sprint's lesson: never merge until CI has concluded SUCCESS on
the head commit.
"""
import json
import re

from . import gate, labels, review, run

PR_FIELDS = ("number,state,isDraft,mergeable,headRefOid,headRefName,baseRefName,"
             "isCrossRepository,body,files,changedFiles,statusCheckRollup,"
             "closingIssuesReferences,commits,comments")
_APPROVE = re.compile(r"^/baton approve\s+([0-9a-f]{7,40})\b")


class PostMergeError(Exception):
    pass
_CLOSING = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b:?\s+#(\d+)", re.I)
_PASSING = {"SUCCESS", "SKIPPED", "NEUTRAL"}


def _latest_runs(rollup):
    """Keep only the newest run of each check; a re-run supersedes a cancelled one."""
    latest = {}
    for item in rollup:
        if item.get("__typename") == "StatusContext":
            key = ("status", item.get("context"))
        else:
            key = ("check", item.get("workflowName"), item.get("name"))
        stamp = item.get("startedAt") or item.get("completedAt") or ""
        if key not in latest or stamp >= latest[key][0]:
            latest[key] = (stamp, item)
    return [item for _, item in latest.values()]


def _check_ci(pr, cfg):
    rollup = _latest_runs(pr.get("statusCheckRollup") or [])
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
    if not problems and all(
            (item.get("conclusion") or item.get("state")) == "SKIPPED" for item in rollup):
        problems.append("every check was skipped on the head commit (a draft run?); "
                        "wait for the run after the pull request was marked ready")
    return problems


def _approved(pr, owner, runner, default_branch, touched):
    """The owner approved the head commit, or an earlier one with the same protected changes:
    `/baton approve <sha>`."""
    head, fetched = pr.get("headRefOid", ""), False
    for comment in pr.get("comments") or []:
        body = comment.get("body", "")
        match = _APPROVE.match(body.strip())
        if not match or gate.MARKER in body or comment.get("author", {}).get("login") != owner:
            continue
        sha = match.group(1)
        if not head.startswith(sha) and not fetched:
            review.fetch(runner, default_branch, pr["headRefName"])
            fetched = True
        if review.same_change(runner, f"origin/{default_branch}", sha, head, touched):
            return True
    return False


def check(pr, cfg, owner, default_branch, runner):
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

    if cfg.data["review"]["required"]:
        found = review.current(pr, owner, cfg, runner, default_branch)
        if found["state"] != "approved":
            problems.append("no reviewer approval of the change at the head commit "
                            f"(review state: {found['state']})")

    protected = tuple(cfg.data["protected_paths"])
    touched = [f["path"] for f in files if protected and f["path"].startswith(protected)]
    if touched and not _approved(pr, owner, runner, default_branch, touched):
        problems.append("protected paths changed without maintainer approval "
                        f"(comment `/baton approve <head commit sha>`): {', '.join(touched)}")
    return problems


def _worktree_for(branch, runner):
    """(path, locked) of the worktree that has the branch checked out, or (None, False)."""
    path, locked, found = None, False, None
    for line in runner(["git", "worktree", "list", "--porcelain"]).splitlines() + [""]:
        if line.startswith("worktree "):
            path, locked = line[len("worktree "):], False
        elif line.startswith("locked"):
            locked = True
        elif line == f"branch refs/heads/{branch}":
            found = path
        elif line == "" and found:
            return found, locked
    return None, False


def _task_worktrees(head, runner):
    """(path, locked) of detached worktrees whose commit is part of `head`'s history.

    Each task's developer works on a detached checkout of the branch, so these are
    the worktrees a pull request's tasks left behind.
    """
    found, path, sha, locked, detached = [], None, None, False, False
    for line in runner(["git", "worktree", "list", "--porcelain"]).splitlines() + [""]:
        if line.startswith("worktree "):
            path, sha, locked, detached = line[len("worktree "):], None, False, False
        elif line.startswith("HEAD "):
            sha = line[len("HEAD "):]
        elif line == "detached":
            detached = True
        elif line.startswith("locked"):
            locked = True
        elif line == "" and path and detached and sha:
            try:
                runner(["git", "merge-base", "--is-ancestor", sha, head])
                found.append((path, locked))
            except run.RunError:
                pass
            path = None
    return found


def _after_merge(pr, cfg, runner, notes):
    for ref in pr.get("closingIssuesReferences") or []:
        issue = ref["number"]
        state = json.loads(runner(["gh", "issue", "view", str(issue), "--json", "state"]))["state"]
        if state == "OPEN":
            runner(["gh", "issue", "close", str(issue)])
        labels.set_status(cfg, runner, issue, "status:verification")

    branch = pr["headRefName"]
    worktree, locked = _worktree_for(branch, runner)
    keep = False
    if worktree:
        if runner(["git", "-C", worktree, "status", "--porcelain"]).strip():
            keep = True
            notes.append(f"kept worktree {worktree} and branch {branch}: "
                         "it has uncommitted changes")
        else:
            force = ["--force", "--force"] if locked else ["--force"]
            runner(["git", "worktree", "remove", *force, worktree])
    for path, locked in _task_worktrees(pr["headRefOid"], runner):
        if runner(["git", "-C", path, "status", "--porcelain"]).strip():
            notes.append(f"kept task worktree {path}: it has uncommitted changes")
            continue
        force = ["--force", "--force"] if locked else ["--force"]
        runner(["git", "worktree", "remove", *force, path])
    if not keep:
        runner(["git", "branch", "-D", branch])
    runner(["git", "push", "origin", "--delete", branch])


def merge(number, cfg, runner, notes=None):
    notes = [] if notes is None else notes
    repo = json.loads(runner(["gh", "repo", "view", "--json", "owner,defaultBranchRef"]))
    pr = json.loads(runner(["gh", "pr", "view", str(number), "--json", PR_FIELDS]))
    if pr.get("state") != "MERGED":
        problems = check(pr, cfg, repo["owner"]["login"], repo["defaultBranchRef"]["name"], runner)
        if problems:
            return problems
        runner(["gh", "pr", "merge", str(number), "--merge",
                "--match-head-commit", pr["headRefOid"]])
    try:
        _after_merge(pr, cfg, runner, notes)
    except Exception as error:
        raise PostMergeError(
            f"#{number} is merged, but a follow-up step failed ({error}); "
            f"run `baton merge-pr {number}` again to finish") from error
    return []
