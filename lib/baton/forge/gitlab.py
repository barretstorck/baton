import datetime
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import identity, run


# The start of every review note the reviewer posts (matches baton.review._REVIEW).
_REVIEW_MARKER = "<!-- baton:reviewer:review "


class GitLabError(run.RunError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class Unsupported(GitLabError):
    def __init__(self, what):
        super().__init__(f"not supported on GitLab: {what}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect, so the token never reaches another URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def urllib_transport(method, url, headers, data):
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _opener.open(request, timeout=30) as reply:
            return reply.status, dict(reply.headers), reply.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers or {}), err.read()
    except (urllib.error.URLError, OSError) as err:
        shown = urllib.parse.urlsplit(url)
        where = f"{shown.scheme}://{shown.netloc}{shown.path}"
        raise GitLabError(f"GitLab {method} {where}: {type(err).__name__}") from None


class GitLab:
    kind = "gitlab"
    _DRAFT = re.compile(r"^\s*(?:draft:|\[draft\]|\(draft\))\s*", re.I)

    def __init__(self, url, owner, repo, token=None, runner=None, transport=None, sleep=None):
        self.url = url.rstrip("/")
        self.owner = owner
        self.repo = repo
        self._token = token
        self._role_tokens = {}
        self.runner = runner or run.run
        self.transport = transport or urllib_transport
        self.sleep = sleep or time.sleep

    def _get_token(self):
        if not self._token:
            try:
                self._token = identity.forge_token("gitlab", self.owner, self.repo)
            except identity.IdentityError as err:
                raise GitLabError(f"GitLab token: {err}") from None
        return self._token

    def _token_for(self, role):
        if role is None:
            return self._get_token()
        key = identity.route(role)
        if key not in self._role_tokens:
            try:
                found = identity.forge_role_token("gitlab", self.owner, self.repo, role)
            except identity.IdentityError as err:
                raise GitLabError(f"GitLab token: {err}") from None
            self._role_tokens[key] = found
        return self._role_tokens[key] or self._get_token()

    def _project(self, owner=None, repo=None):
        full = f"{owner or self.owner}/{repo or self.repo}"
        return "/projects/" + urllib.parse.quote(full, safe="")

    def _request(self, method, path, params=None, body=None, raw=False, role=None,
                 token=None):
        url = f"{self.url}/api/v4{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"PRIVATE-TOKEN": token or self._token_for(role), "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        status, _, payload = self.transport(method, url, headers, data)
        text = (payload or b"").decode("utf-8", errors="replace")
        if not 200 <= status < 300:
            message = ""
            try:
                found = json.loads(text)
                if isinstance(found, dict):
                    detail = found.get("message") or found.get("error")
                    if detail:
                        if not isinstance(detail, str):
                            detail = json.dumps(detail)
                        message = f": {detail}"
            except ValueError:
                pass
            raise GitLabError(f"GitLab {method} {path}: HTTP {status}{message}", status=status)
        if raw:
            return text
        if not text.strip():
            return None
        return json.loads(text)

    def rotate_self_token(self, token, expires_at):
        """Rotate `token` with itself; returns (kind, secret, expires_at).

        A personal access token answers on /personal_access_tokens/self/rotate; any other
        token kind gets HTTP 405 there (nothing rotated), so the project endpoint is tried.
        """
        body = {"expires_at": expires_at.isoformat()}
        paths = [("personal", "/personal_access_tokens/self/rotate"),
                 ("project", self._project() + "/access_tokens/self/rotate")]
        for index, (kind, path) in enumerate(paths):
            try:
                reply = self._request("POST", path, body=body, token=token)
            except GitLabError as err:
                if err.status == 405 and index == 0:
                    continue
                raise
            secret = reply.get("token") if isinstance(reply, dict) else None
            if not isinstance(secret, str) or not secret:
                raise GitLabError(f"GitLab POST {path}: the reply has no token")
            return kind, secret, expires_at
        raise GitLabError(f"GitLab POST {paths[1][1]}: HTTP 405", status=405)

    def token_expiry(self, token):
        """The expiry date of `token` (a personal access token), or None when it has none."""
        found = self._request("GET", "/personal_access_tokens/self", token=token)
        value = found.get("expires_at") if isinstance(found, dict) else None
        if not value:
            return None
        try:
            return datetime.date.fromisoformat(str(value)[:10])
        except ValueError:
            raise GitLabError("GitLab GET /personal_access_tokens/self: unreadable expires_at") from None

    def _paged(self, path, params=None, limit=None):
        items = []
        page = 1
        while True:
            query = {**(params or {}), "per_page": 100, "page": page}
            batch = self._request("GET", path, params=query) or []
            items.extend(batch)
            if limit is not None and len(items) >= limit:
                return items[:limit]
            if len(batch) < 100:
                return items
            page += 1

    @staticmethod
    def _field_names(fields, known):
        names = [name for name in fields.split(",") if name]
        for name in names:
            if name not in known:
                raise Unsupported(f"the {name} field")
        return names

    @staticmethod
    def _login(user):
        return {"login": (user or {}).get("username", "")}

    @staticmethod
    def _read_file(path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    # --- labels and milestones ---

    def label_names(self):
        items = self._paged(f"{self._project()}/labels", {"include_ancestor_groups": "true"})
        return {item["name"] for item in items}

    def label_list(self):
        items = self._paged(f"{self._project()}/labels", {"include_ancestor_groups": "true"})
        return [{"name": item["name"], "description": item.get("description") or ""}
                for item in items]

    def label_create(self, name, color, description, role=None):
        self._request("POST", f"{self._project()}/labels",
                      body={"name": name, "color": "#" + color, "description": description}, role=role)
        return ""

    @staticmethod
    def _milestone_shape(item):
        due = item.get("due_date")
        state = item.get("state")
        return {"number": item["id"], "title": item.get("title"),
                "description": item.get("description") or "",
                "state": "open" if state == "active" else state,
                "due_on": f"{due}T00:00:00Z" if due else None}

    def _milestone_items(self, owner, repo, state):
        params = {"include_ancestors": "true"}
        wanted = {"open": "active", "active": "active", "closed": "closed"}.get(state)
        if wanted:
            params["state"] = wanted
        return [self._milestone_shape(item)
                for item in self._paged(f"{self._project(owner, repo)}/milestones", params)]

    def _milestone_list(self, state):
        return self._milestone_items(None, None, state)

    def milestones(self):
        return self._milestone_list("all")

    def milestones_text(self, owner, repo, state):
        return json.dumps(self._milestone_items(owner, repo, state))

    def milestone_create(self, title, description=None, due_on=None, role=None):
        body = {"title": title}
        if description:
            body["description"] = description
        if due_on:
            body["due_date"] = due_on[:10]
        self._request("POST", f"{self._project()}/milestones", body=body, role=role)
        return ""

    def milestone_edit(self, number, changes, role=None):
        body = {}
        for key in ("title", "description"):
            if key in changes:
                body[key] = changes[key]
        if "due_on" in changes:
            body["due_date"] = (changes["due_on"] or "")[:10]
        if "state" in changes:
            body["state_event"] = "close" if changes["state"] == "closed" else "activate"
        self._request("PUT", f"{self._project()}/milestones/{number}", body=body, role=role)
        return ""

    def _milestone_id(self, title):
        for item in self._milestone_list("all"):
            if item["title"] == title:
                return item["number"]
        raise GitLabError(f"GitLab milestone not found: {title}")

    # --- repository, issues and notes ---

    _REPO_FIELDS = ("owner", "name", "nameWithOwner", "defaultBranchRef", "visibility")
    _ISSUE_FIELDS = ("number", "title", "body", "state", "author", "labels", "createdAt",
                     "assignees", "comments")

    def repo_view(self, fields, empty=None):
        names = self._field_names(fields, self._REPO_FIELDS)
        item = self._request("GET", self._project())
        shapes = {"owner": lambda: {"login": (item.get("namespace") or {}).get("full_path", "")},
                  "name": lambda: item["path"],
                  "nameWithOwner": lambda: item["path_with_namespace"],
                  "defaultBranchRef": lambda: {"name": item["default_branch"]},
                  "visibility": lambda: str(item.get("visibility") or "").upper()}
        return {name: shapes[name]() for name in names}

    def _note_shape(self, item, web_url):
        return {"id": str(item["id"]), "author": self._login(item.get("author")),
                "body": item.get("body") or "", "createdAt": item.get("created_at"),
                "url": f"{web_url}#note_{item['id']}"}

    def _notes(self, kind, iid, web_url):
        items = self._paged(f"{self._project()}/{kind}/{iid}/notes",
                            {"sort": "asc", "order_by": "created_at"})
        return [self._note_shape(item, web_url) for item in items if not item.get("system")]

    def _issue_shape(self, item, names):
        state = "OPEN" if item.get("state") == "opened" else "CLOSED"
        shapes = {"number": lambda: item["iid"],
                  "title": lambda: item["title"],
                  "body": lambda: item.get("description") or "",
                  "state": lambda: state,
                  "author": lambda: self._login(item.get("author")),
                  "labels": lambda: [{"name": label} for label in item.get("labels") or []],
                  "createdAt": lambda: item.get("created_at"),
                  "assignees": lambda: [self._login(user) for user in item.get("assignees") or []],
                  "comments": lambda: self._notes("issues", item["iid"], item["web_url"])}
        return {name: shapes[name]() for name in names}

    def issue_view(self, number, fields, empty=None):
        names = self._field_names(fields, self._ISSUE_FIELDS)
        item = self._request("GET", f"{self._project()}/issues/{number}")
        return self._issue_shape(item, names)

    def issue_view_text(self, number, fields):
        return json.dumps(self.issue_view(number, fields))

    def issue_exists(self, number):
        try:
            return bool(self._request("GET", f"{self._project()}/issues/{number}"))
        except GitLabError:
            return False

    def issue_list(self, fields, state, limit, milestone=None, label=None, search=None,
                   empty=None):
        words = []
        if search is not None:
            raw = search.split()
            if any(re.match(r"^[A-Za-z][\w-]*:", word) for word in raw):
                raise Unsupported("issue search qualifiers")
            for word in raw:
                if word.lower() not in words:
                    words.append(word.lower())
        names = self._field_names(fields, self._ISSUE_FIELDS)
        params = {"state": {"open": "opened"}.get(state, state)}
        if label is not None:
            params["labels"] = label
        if milestone is not None:
            params["milestone"] = milestone
        if search is not None:
            merged = {}
            for word in words:
                found = self._paged(f"{self._project()}/issues",
                                    {**params, "search": word, "in": "title"}, 100)
                for item in found:
                    merged.setdefault(item["iid"], item)
            items = self._search_rank(list(merged.values()), words)[:limit]
        else:
            items = self._paged(f"{self._project()}/issues", params, limit)
        return [self._issue_shape(item, names) for item in items]

    @staticmethod
    def _search_rank(items, words):
        def hits(item):
            title = (item.get("title") or "").lower()
            return sum(1 for word in words if word in title)
        return sorted(items, key=lambda item: (hits(item), item["iid"]), reverse=True)

    def issue_list_text(self, fields, state, limit, milestone=None, label=None, search=None):
        return json.dumps(self.issue_list(fields, state, limit, milestone, label, search))

    def issue_edit_body(self, number, path, role=None):
        self._request("PUT", f"{self._project()}/issues/{number}",
                      body={"description": self._read_file(path)}, role=role)
        return ""

    def issue_edit_labels(self, number, add, remove=(), role=None):
        body = {}
        if add:
            body["add_labels"] = ",".join(add)
        if remove:
            body["remove_labels"] = ",".join(remove)
        if body:
            self._request("PUT", f"{self._project()}/issues/{number}", body=body, role=role)
        return ""

    def comment(self, target, number, path, role=None):
        kind = "merge_requests" if target == "pr" else "issues"
        made = self._request("POST", f"{self._project()}/{kind}/{number}/notes",
                             body={"body": self._read_file(path)}, role=role)
        note = (made or {}).get("id", "")
        return f"{self.url}/{self.owner}/{self.repo}/-/{kind}/{number}#note_{note}\n"

    def change_state(self, target, action, number, role=None):
        kind = "merge_requests" if target == "pr" else "issues"
        event = "close" if action == "close" else "reopen"
        self._request("PUT", f"{self._project()}/{kind}/{number}", body={"state_event": event}, role=role)
        return ""

    def issue_close(self, number, reason, duplicate_of=None, role=None):
        return self.change_state("issue", "close", number, role)

    def issue_lock(self, number, reason=None, role=None):
        self._request("PUT", f"{self._project()}/issues/{number}",
                      body={"discussion_locked": True}, role=role)
        return ""

    def issue_create(self, title, path, labels=(), milestone=None, role=None):
        body = {"title": title, "description": self._read_file(path)}
        if labels:
            body["labels"] = ",".join(labels)
        if milestone:
            body["milestone_id"] = self._milestone_id(milestone)
        made = self._request("POST", f"{self._project()}/issues", body=body, role=role)
        return (made or {}).get("web_url", "") + "\n"

    def item_edit(self, target, number, title=None, add_labels=(), remove_labels=(),
                  milestone=None, remove_milestone=False, path=None, role=None):
        body = {}
        if title:
            body["title"] = title
        if path is not None:
            body["description"] = self._read_file(path)
        if milestone:
            body["milestone_id"] = self._milestone_id(milestone)
        if remove_milestone:
            body["milestone_id"] = 0
        if add_labels:
            body["add_labels"] = ",".join(add_labels)
        if remove_labels:
            body["remove_labels"] = ",".join(remove_labels)
        if body:
            kind = "merge_requests" if target == "pr" else "issues"
            self._request("PUT", f"{self._project()}/{kind}/{number}", body=body, role=role)
        return ""

    def user(self, login):
        found = self._request("GET", "/users", params={"username": login}) or []
        if not found:
            raise GitLabError(f"GitLab user not found: {login}")
        return {**found[0], "login": found[0].get("username", login)}

    # --- merge requests ---

    _PR_FIELDS = ("number", "title", "body", "state", "isDraft", "mergeable", "headRefName",
                  "headRefOid", "baseRefName", "isCrossRepository", "changedFiles", "author",
                  "url", "reviewRequests", "comments", "files", "commits",
                  "closingIssuesReferences", "statusCheckRollup")

    _UNKNOWN_MERGE = ("checking", "unchecked", "preparing", "approvals_syncing")

    @staticmethod
    def _mr_state(item):
        state = item.get("state")
        if state == "merged":
            return "MERGED"
        return "OPEN" if state == "opened" else "CLOSED"

    @classmethod
    def _mr_mergeable(cls, item):
        status = item.get("detailed_merge_status")
        if item.get("has_conflicts") or status == "conflict":
            return "CONFLICTING"
        if status in cls._UNKNOWN_MERGE:
            return "UNKNOWN"
        if status is None or status == "mergeable":
            return "MERGEABLE"
        return status

    @staticmethod
    def _is_cross_repository(item):
        source, target = item.get("source_project_id"), item.get("target_project_id")
        return not (source is not None and target is not None and source == target)

    @staticmethod
    def _changed_files(item):
        count = str(item.get("changes_count") or "")
        return int(count) if count.isdigit() else None

    def _mr_diffs(self, iid):
        return self._paged(f"{self._project()}/merge_requests/{iid}/diffs")

    def _mr_files(self, iid):
        out = []
        for item in self._mr_diffs(iid):
            lines = (item.get("diff") or "").split("\n")
            entry = {"path": item["new_path"],
                     "additions": sum(1 for line in lines if line.startswith("+")),
                     "deletions": sum(1 for line in lines if line.startswith("-"))}
            old = item.get("old_path")
            if old:
                renamed = item.get("renamed_file") or old != item["new_path"]
                entry["previousPath"] = old if renamed else None
            out.append(entry)
        return out

    def _mr_commits(self, iid):
        out = []
        for item in self._paged(f"{self._project()}/merge_requests/{iid}/commits"):
            _, _, rest = (item.get("message") or "").partition("\n")
            out.append({"oid": item["id"], "messageHeadline": item.get("title") or "",
                        "messageBody": rest.lstrip("\n")})
        return out

    def _closes_issues(self, iid):
        # GitLab fills closes_issues in asynchronously: read an empty list again.
        path = f"{self._project()}/merge_requests/{iid}/closes_issues"
        issues = self._request("GET", path) or []
        for _ in range(3):
            if issues:
                break
            self.sleep(2)
            issues = self._request("GET", path) or []
        return [{"number": issue["iid"]} for issue in issues]

    _JOB_CONCLUSIONS = {"success": "SUCCESS", "failed": "FAILURE", "canceled": "CANCELLED",
                        "skipped": "SKIPPED", "manual": "ACTION_REQUIRED"}

    @classmethod
    def _job_shape(cls, job):
        conclusion = cls._JOB_CONCLUSIONS.get(job.get("status"))
        if conclusion == "FAILURE" and job.get("allow_failure"):
            conclusion = "NEUTRAL"
        return {"__typename": "CheckRun", "name": job.get("name"),
                "workflowName": job.get("stage"),
                "status": "COMPLETED" if conclusion else "IN_PROGRESS",
                "conclusion": conclusion or "",
                "detailsUrl": job.get("web_url"), "startedAt": job.get("started_at")}

    def _mr_rollup(self, item):
        pipeline = item.get("head_pipeline") or {}
        if not pipeline.get("id") or pipeline.get("sha") != item.get("sha"):
            return []
        base = f"{self._project()}/pipelines/{pipeline['id']}"
        entries = [self._job_shape(job) for job in self._paged(f"{base}/jobs")]
        for bridge in self._paged(f"{base}/bridges"):
            downstream = bridge.get("downstream_pipeline") or {}
            entry = self._job_shape({**bridge, "status": downstream.get("status")})
            entry["detailsUrl"] = downstream.get("web_url") or bridge.get("web_url")
            entries.append(entry)
        # the pipeline as a whole: running, pending, created or unknown states stay IN_PROGRESS
        entries.append(self._job_shape({
            "name": "pipeline", "stage": "pipeline", "web_url": pipeline.get("web_url"),
            "status": pipeline.get("status")}))
        return entries

    def _mr_shape(self, item, names):
        iid = item["iid"]
        shapes = {
            "number": lambda: iid,
            "title": lambda: item["title"],
            "body": lambda: item.get("description") or "",
            "state": lambda: self._mr_state(item),
            "isDraft": lambda: item.get("draft") is not False,
            "mergeable": lambda: self._mr_mergeable(item),
            "headRefName": lambda: item.get("source_branch"),
            "headRefOid": lambda: item.get("sha"),
            "baseRefName": lambda: item.get("target_branch"),
            "isCrossRepository": lambda: self._is_cross_repository(item),
            "changedFiles": lambda: self._changed_files(item),
            "author": lambda: self._login(item.get("author")),
            "url": lambda: item.get("web_url"),
            "reviewRequests": lambda: [self._login(user) for user in item.get("reviewers") or []],
            "comments": lambda: self._notes("merge_requests", iid, item["web_url"]),
            "files": lambda: self._mr_files(iid),
            "commits": lambda: self._mr_commits(iid),
            "closingIssuesReferences": lambda: self._closes_issues(iid),
            "statusCheckRollup": lambda: self._mr_rollup(item),
        }
        return {name: shapes[name]() for name in names}

    def pr_view(self, number, fields, empty=None):
        names = self._field_names(fields, self._PR_FIELDS)
        item = self._request("GET", f"{self._project()}/merge_requests/{number}")
        return self._mr_shape(item, names)

    def closing_problems(self, pr):
        # GitLab gets no closing-reference rule here; merge-pr behaves as before.
        return []

    def pr_list_text(self, fields, state, limit, head=None):
        names = self._field_names(fields, self._PR_FIELDS)
        params = {"state": {"open": "opened"}.get(state, state)}
        if head:
            params["source_branch"] = head
        items = self._paged(f"{self._project()}/merge_requests", params, limit)
        return json.dumps([self._mr_shape(item, names) for item in items])

    def run_log_failed(self, run_id):
        raise Unsupported("run logs")

    def pr_diff(self, number):
        out = []
        for item in self._mr_diffs(number):
            old, new = item.get("old_path"), item.get("new_path")
            text = item.get("diff") or ""
            for path in (old, new):
                if any(ord(c) < 32 or c in '"\\' or ord(c) == 127 for c in str(path)):
                    raise GitLabError(f"GitLab merge request {number}: refusing a path with "
                                      f"a control character, quote or backslash: {path!r}")
            if not text and (item.get("too_large") or item.get("collapsed")):
                raise GitLabError(f"GitLab merge request {number}: the diff of {new} "
                                  "is too large to read")
            out.append(f"diff --git a/{old} b/{new}\n")
            out.append("--- " + ("/dev/null" if item.get("new_file") else f"a/{old}") + "\n")
            out.append("+++ " + ("/dev/null" if item.get("deleted_file") else f"b/{new}") + "\n")
            out.append(text if not text or text.endswith("\n") else text + "\n")
        return "".join(out)

    # --- not supported yet ---

    # --- pipelines and branches ---

    _RUN_FIELDS = ("databaseId", "status", "conclusion", "headSha", "headBranch",
                   "workflowName", "event", "url")
    _DONE = ("success", "failed", "canceled", "skipped", "manual")
    # lower case, as `sprint` compares; any other completed state fails closed
    _RUN_CONCLUSIONS = {"success": "success", "failed": "failure", "canceled": "cancelled",
                        "skipped": "skipped", "manual": "action_required"}

    def _run_shape(self, item, names):
        state = item.get("status") or ""
        if state in self._DONE:
            status = "completed"
        elif state == "running":
            status = "in_progress"
        else:
            status = "queued"
        shapes = {"databaseId": lambda: item["id"],
                  "status": lambda: status,
                  "conclusion": lambda: (self._RUN_CONCLUSIONS.get(state, "failure")
                                       if status == "completed" else ""),
                  "headSha": lambda: item.get("sha"),
                  "headBranch": lambda: item.get("ref"),
                  "workflowName": lambda: "pipeline",
                  "event": lambda: item.get("source"),
                  "url": lambda: item.get("web_url")}
        return {name: shapes[name]() for name in names}

    def run_view(self, run_id, fields):
        names = self._field_names(fields, self._RUN_FIELDS)
        item = self._request("GET", f"{self._project()}/pipelines/{run_id}")
        return self._run_shape(item, names)

    def run_list_text(self, branch, sha, event, limit, fields):
        names = self._field_names(fields, self._RUN_FIELDS)
        items = self._request("GET", f"{self._project()}/pipelines",
                              params={"ref": branch, "sha": sha, "per_page": limit}) or []
        if event:  # GitLab's pipeline `source` plays the part of the workflow event
            items = [item for item in items if item.get("source") == event]
        return json.dumps([self._run_shape(item, names) for item in items])

    def branch_head(self, owner, repo, branch):
        found = self._request(
            "GET", f"{self._project(owner, repo)}/repository/branches/"
                   f"{urllib.parse.quote(branch, safe='')}")
        commit = found.get("commit") or {}
        return {"sha": commit.get("id"),
                "commit": {"committer": {"date": commit.get("committed_date")}}}

    def last_push(self, owner, repo, branch):
        found = self._request("GET", f"{self._project(owner, repo)}/events",
                              params={"action": "pushed"}) or []
        newest = None
        for entry in found:
            if (entry.get("push_data") or {}).get("ref") != branch:
                continue
            created = entry.get("created_at")
            if created and (newest is None or created > newest):
                newest = created
        return newest

    # --- merge request writes ---

    def pr_create(self, title, path, head=None, draft=False, role=None):
        if draft:
            title = f"Draft: {title}"
        if not head:
            head = self.runner(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip()
        base = self._request("GET", self._project())["default_branch"]
        found = self._request("POST", f"{self._project()}/merge_requests",
                              body={"source_branch": head, "target_branch": base,
                                    "title": title, "description": self._read_file(path)}, role=role)
        return found["web_url"] + "\n"

    def pr_merge(self, number, head_oid, role=None):
        base = f"{self._project()}/merge_requests/{number}"
        try:
            self._request("PUT", f"{base}/merge", body={"sha": head_oid}, role=role)
        except GitLabError as error:
            try:
                found = self._request("GET", base)
            except GitLabError:
                raise error from None
            status = (found or {}).get("detailed_merge_status") or "unknown"
            raise GitLabError(f"{error}; detailed_merge_status: {status}") from error
        return ""

    _REVIEW_EVENTS = ("approve", "request-changes", "comment")

    def pr_review(self, number, event, path, role=None, commit=None):
        if event not in self._REVIEW_EVENTS:
            raise Unsupported(f"the review event {event}")
        base = f"{self._project()}/merge_requests/{number}"
        if event == "approve":
            sha = commit
            if sha is None:
                sha = self._request("GET", base).get("sha")
            try:
                self._request("POST", f"{base}/approve", body={"sha": sha}, role=role)
            except GitLabError as error:
                if error.status != 409:
                    raise
                raise GitLabError(
                    f"GitLab refused a stale approval of {(sha or '')[:7]}: the merge "
                    f"request's head moved") from error
        self._request("POST", f"{base}/notes", body={"body": self._read_file(path)}, role=role)
        return ""

    def review_urls(self, number, login):
        web_url = self._request(
            "GET", f"{self._project()}/merge_requests/{number}")["web_url"]
        return [note["url"] for note in self._notes("merge_requests", number, web_url)
                if note["author"]["login"] == login
                and note["body"].startswith(_REVIEW_MARKER)]

    def pr_review_items(self, number):
        return []

    def review_request_remove(self, number, login, role=None):
        path = f"{self._project()}/merge_requests/{number}"
        reviewers = self._request("GET", path).get("reviewers") or []
        if not any(user.get("username") == login for user in reviewers):
            return ""
        ids = [user["id"] for user in reviewers if user.get("username") != login]
        self._request("PUT", path, body={"reviewer_ids": ids}, role=role)
        return ""

    def review_edit_body(self, number, review_id, path, role=None):
        raise Unsupported("editing a review body")

    def comment_edit_body(self, comment_id, path, role=None, number=None):
        if number is None:
            raise GitLabError(
                "a note can only be edited with its merge request number")
        self._request("PUT", f"{self._project()}/merge_requests/{number}/notes/{comment_id}",
                      body={"body": self._read_file(path)}, role=role)
        return ""

    def pr_ready(self, number, undo=False, role=None):
        path = f"{self._project()}/merge_requests/{number}"
        title = (self._request("GET", path) or {}).get("title") or ""
        if undo:
            new = title if self._DRAFT.match(title) else f"Draft: {title}"
        else:
            new = self._DRAFT.sub("", title, count=1)
        if new != title:
            self._request("PUT", path, body={"title": new}, role=role)
        return ""

    def workflow_entries_text(self, *args, **kwargs):
        raise Unsupported("GitHub Actions workflow reads")

    def workflow_file_text(self, *args, **kwargs):
        raise Unsupported("GitHub Actions workflow reads")

    def actions_permissions_text(self, *args, **kwargs):
        raise Unsupported("GitHub Actions permission reads")

    def issue_body_edits(self, number):
        try:
            notes = self._paged(f"{self._project()}/issues/{number}/notes",
                                {"sort": "desc", "order_by": "created_at"})
        except GitLabError:
            return None
        edits = []
        for note in notes:
            if not note.get("system") or not (note.get("body") or "").startswith(
                    "changed the description"):
                continue
            author = note.get("author")
            created = note.get("created_at") or ""
            edits.append({"editedAt": max(note.get("updated_at") or "", created),
                          "editor": ({"login": author.get("username", ""), "__typename": "User"}
                                     if author else None)})
        return edits

    def issue_timeline(self, number):
        return None

    def comment_author_types(self, number):
        return {}
