"""The only way a Baton pull request is merged.

Adapted from the acceptance rules in the design spec, section 6, and the
prototype sprint's lesson: never merge until CI has concluded SUCCESS on
the head commit.
"""
import json
import os
import re
import shutil
import tempfile

from . import checklist, forbid, gate, labels, planfile, review, scan, state as item_state, verify
from .run import RunError
from .config import logins

PR_FIELDS = ("number,state,isDraft,mergeable,headRefOid,headRefName,baseRefName,"
             "isCrossRepository,body,files,changedFiles,statusCheckRollup,"
             "closingIssuesReferences,commits,comments")
_APPROVE = re.compile(r"^/baton approve\s+([0-9a-f]{7,40})\b")


class PostMergeError(Exception):
    pass


class PlanRetired(Exception):
    """The plan file was removed from the branch; checks must run again before the merge."""
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


_RUN_URL = re.compile(r"/actions/runs/(\d+)(?:/|$)")


def _settle(item, runner, notes, head=""):
    """GitHub can keep a check run open after its workflow run finished. For an unfinished
    check run, ask the workflow run; if it is completed and ran on the pull request's head
    commit (and is the item's workflow), return a copy settled from it. Fails closed."""
    if item.get("__typename") == "StatusContext" or item.get("status") == "COMPLETED":
        return item
    found = _RUN_URL.search(item.get("detailsUrl") or "")
    if runner is None or not found or not head:
        return item
    try:
        run = json.loads(runner(["gh", "run", "view", found.group(1),
                                 "--json", "status,conclusion,workflowName,url,headSha"]))
    except (RunError, ValueError):
        return item
    if not isinstance(run, dict) or str(run.get("status", "")).lower() != "completed" \
            or not run.get("conclusion"):
        return item
    if run.get("headSha") != head:
        return item
    if item.get("workflowName") and item["workflowName"] != run.get("workflowName"):
        return item
    conclusion = str(run["conclusion"]).upper()
    if notes is not None:
        notes.append(f"check {item.get('name')!r} was still in progress; used the result "
                     f"of run {run.get('url') or found.group(1)} ({conclusion.lower()})")
    return {**item, "status": "COMPLETED", "conclusion": conclusion}


def _check_ci(pr, cfg, runner=None, notes=None):
    rollup = [_settle(item, runner, notes, pr.get("headRefOid") or "")
              for item in _latest_runs(pr.get("statusCheckRollup") or [])]
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
        if not match or gate.MARKER in body or comment.get("author", {}).get("login") not in logins(owner):
            continue
        sha = match.group(1)
        if not head.startswith(sha) and not fetched:
            review.fetch(runner, default_branch, pr["headRefName"])
            fetched = True
        if review.same_change(runner, f"origin/{default_branch}", sha, head, touched):
            return True
    return False


def check(pr, cfg, owner, default_branch, runner, notes=None):
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
    problems.extend(_check_ci(pr, cfg, runner, notes))

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
        recorded = review.verify_state(cfg, pr.get("comments"), owner, pr.get("headRefOid"))
        if recorded not in ("none", "green"):
            problems.append(f"verification of {pr.get('headRefOid', '')[:7]} is {recorded} in the "
                            "latest review; the reviewer runs baton verify")

    protected = tuple(cfg.data["protected_paths"])
    touched = [f["path"] for f in files if protected and f["path"].startswith(protected)]
    if touched and not _approved(pr, owner, runner, default_branch, touched):
        problems.append("protected paths changed without maintainer approval "
                        f"(comment `/baton approve <head commit sha>`): {', '.join(touched)}")
    rules = cfg.data["forbid_added"]
    if rules:
        patch = runner(["gh", "pr", "diff", str(pr["number"])])
        allowed = _allowances(pr, closing, cfg, runner, problems)
        problems.extend(forbid.violations(patch, rules, allowed))
    return problems


def _allowances(pr, closing, cfg, runner, problems):
    """Globs the plan allows, taken from the one issue the pull request closes.

    The branch name alone is not trusted: it must name that same issue, or the pull
    request gets no allowances.
    """
    if len(closing) != 1:
        return set()
    number = next(iter(closing))
    branch = pr.get("headRefName", "")
    if forbid.branch_issue(branch) != number:
        problems.append(f"branch {branch} does not belong to the issue the pull request "
                        f"closes (#{number}); no forbid_added allowances apply")
        return set()
    return forbid.allowed_globs(forbid.plan_text(runner, cfg, branch, number))


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


def _task_worktrees(commits, runner):
    """(path, locked) of detached worktrees sitting on one of the pull request's own commits.

    Each task's developer works on a detached checkout of the branch, so these are
    the worktrees a pull request's tasks left behind. A worktree on a default-branch
    commit is never one of them, whatever its age.
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
        elif line == "" and path:
            if detached and sha in commits:
                found.append((path, locked))
            path = None
    return found


def _after_merge(pr, cfg, runner, notes):
    for ref in pr.get("closingIssuesReferences") or []:
        issue = ref["number"]
        state = json.loads(runner(["gh", "issue", "view", str(issue), "--json", "state"]))["state"]
        if state == "OPEN":
            item_state.change(cfg, runner, "close", "issue", issue, "orchestrator",
                              f"Merged pull request #{pr['number']}.", scan.load_terms())
        labels.set_status(cfg, runner, issue, "status:verification")

    branch = pr["headRefName"]
    worktree, locked = _worktree_for(branch, runner)
    if worktree:
        force = ["--force", "--force"] if locked else ["--force"]
        runner(["git", "worktree", "remove", *force, worktree])
    own = {commit.get("oid") for commit in pr.get("commits") or []}
    for path, locked in _task_worktrees(own, runner):
        force = ["--force", "--force"] if locked else ["--force"]
        runner(["git", "worktree", "remove", *force, path])
    if runner(["git", "branch", "--list", branch]).strip():
        runner(["git", "branch", "-D", branch])
    runner(["git", "push", "origin", "--delete", branch])


def _retire_plan(pr, repo, runner, cfg, notes=None):
    """Pin the issue's plan link; for an in-flight branch plan also remove the file and push.

    A plan file on the branch is pinned to the head, removed from the branch and pushed,
    and the note to show is returned. None when there is nothing to retire.
    """
    closing = [ref["number"] for ref in pr.get("closingIssuesReferences") or []]
    if len(closing) != 1:
        return None
    issue, branch, head = closing[0], pr["headRefName"], pr["headRefOid"]
    link = planfile.parse_link(checklist.issue_body(runner, issue), cfg.data["docs"]["dir"])
    if link is None or link["pinned"]:
        return None
    if link["kind"] == "ref":
        return None
    runner(["git", "fetch", "--quiet", "--end-of-options", "origin", branch])
    try:
        runner(["git", "show", "--end-of-options", f"{head}:{link['path']}"])
    except RunError:
        return None
    slug = f"{repo['owner']['login']}/{repo['name']}"
    planfile.link_issue(runner, slug, issue, at=head,
                       docs_dir=cfg.data["docs"]["dir"])
    tree = tempfile.mkdtemp(prefix="baton-retire-")
    try:
        runner(["git", "worktree", "add", "--detach", tree, head])
        try:
            runner(["git", "rm", "-q", "--", link["path"]], cwd=tree)
            runner(["git", "commit", "-q", "-m", f"Remove the plan of #{issue}"], cwd=tree)
            runner(["git", "push", "origin", f"HEAD:{branch}"], cwd=tree)
        finally:
            runner(["git", "worktree", "remove", "--force", tree])
    except BaseException:
        # The file is still on the branch, so the link must not stay pinned: a rerun would
        # find it pinned and merge the plan file into the default branch.
        try:
            planfile.link_issue(runner, slug, issue, branch=branch, path=link["path"],
                                docs_dir=cfg.data["docs"]["dir"])
        except Exception:
            pass
        raise
    finally:
        if os.path.isdir(tree):
            shutil.rmtree(tree, ignore_errors=True)
    return (f"removed the plan {link['path']} of #{issue} from {branch} and pinned the link "
            f"to {head}; checks run on the new head, run `baton merge-pr {pr['number']}` "
            "again once they pass")


def merge(number, cfg, runner, notes=None):
    notes = [] if notes is None else notes
    repo = json.loads(runner(["gh", "repo", "view", "--json", "owner,name,defaultBranchRef"]))
    pr = json.loads(runner(["gh", "pr", "view", str(number), "--json", PR_FIELDS]))
    if pr.get("state") != "MERGED":
        problems = check(pr, cfg, cfg.maintainers(repo["owner"]["login"]), repo["defaultBranchRef"]["name"], runner,
                         notes)
        if problems:
            return problems
        note = _retire_plan(pr, repo, runner, cfg, notes)
        if note:
            raise PlanRetired(note)
        runner(["gh", "pr", "merge", str(number), "--merge",
                "--match-head-commit", pr["headRefOid"]])
    try:
        _after_merge(pr, cfg, runner, notes)
    except Exception as error:
        raise PostMergeError(
            f"#{number} is merged, but a follow-up step failed ({error}); "
            f"run `baton merge-pr {number}` again to finish") from error
    try:
        verify.remove_logs(repo["owner"]["login"], repo["name"], number)
    except OSError as error:
        notes.append(f"could not remove the verification logs of #{number} ({error})")
    return []
