import json
import urllib.error
import urllib.parse
import urllib.request

from .. import identity, run


class ForgejoError(run.RunError):
    pass


class Unsupported(ForgejoError):
    def __init__(self, what):
        super().__init__(f"not supported on Forgejo: {what}")


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
        raise ForgejoError(f"Forgejo {method} {where}: {type(err).__name__}") from None


class Forgejo:
    kind = "forgejo"

    def __init__(self, url, owner, repo, token=None, runner=None, transport=None):
        self.url = url.rstrip("/")
        self.owner = owner
        self.repo = repo
        self._token = token
        self.runner = runner or run.run
        self.transport = transport or urllib_transport

    def _get_token(self):
        if not self._token:
            try:
                self._token = identity.forge_token("forgejo", self.owner, self.repo)
            except identity.IdentityError as err:
                raise ForgejoError(f"Forgejo token: {err}") from None
        return self._token

    def _request(self, method, path, params=None, body=None, raw=False):
        url = f"{self.url}/api/v1{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Authorization": f"token {self._get_token()}",
                   "Accept": "application/json"}
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
                if isinstance(found, dict) and found.get("message"):
                    message = f": {found['message']}"
            except ValueError:
                pass
            raise ForgejoError(f"Forgejo {method} {path}: HTTP {status}{message}")
        if raw:
            return text
        if not text.strip():
            return None
        return json.loads(text)

    def _paged(self, path, params=None, limit=None):
        items = []
        page = 1
        while True:
            query = {**(params or {}), "limit": 50, "page": page}
            batch = self._request("GET", path, params=query) or []
            items.extend(batch)
            if limit is not None and len(items) >= limit:
                return items[:limit]
            if len(batch) < 50:
                return items
            page += 1

    def _labels(self):
        items = list(self._paged(f"/repos/{self.owner}/{self.repo}/labels"))
        try:
            items += self._paged(f"/orgs/{self.owner}/labels")
        except ForgejoError as err:
            if "HTTP 404" not in str(err):
                raise
        return items

    def label_names(self):
        return {item["name"] for item in self._labels()}

    def label_create(self, name, color, description, role=None):
        self._request("POST", f"/repos/{self.owner}/{self.repo}/labels",
                      body={"name": name, "color": "#" + color, "description": description})
        return ""

    def _label_ids(self, names):
        known = {item["name"]: item["id"] for item in self._labels()}
        ids = []
        for name in names:
            if name not in known:
                raise ForgejoError(f"Forgejo label not found: {name}")
            ids.append(known[name])
        return ids

    @staticmethod
    def _milestone_shape(item):
        return {"number": item["id"], "title": item.get("title"),
                "description": item.get("description", ""), "state": item.get("state"),
                "due_on": item.get("due_on")}

    def _milestone_list(self, state):
        items = self._paged(f"/repos/{self.owner}/{self.repo}/milestones", {"state": state})
        return [self._milestone_shape(item) for item in items]

    def milestones(self):
        return self._milestone_list("all")

    def milestones_text(self, owner, repo, state):
        items = self._paged(f"/repos/{owner}/{repo}/milestones", {"state": state})
        return json.dumps([self._milestone_shape(item) for item in items])

    def milestone_create(self, title, description=None, due_on=None, role=None):
        body = {"title": title}
        if description:
            body["description"] = description
        if due_on:
            body["due_on"] = due_on
        self._request("POST", f"/repos/{self.owner}/{self.repo}/milestones", body=body)
        return ""

    def milestone_edit(self, number, changes, role=None):
        self._request("PATCH", f"/repos/{self.owner}/{self.repo}/milestones/{number}",
                      body=dict(changes))
        return ""

    def _milestone_id(self, title):
        for item in self._milestone_list("all"):
            if item["title"] == title:
                return item["number"]
        raise ForgejoError(f"Forgejo milestone not found: {title}")

    def issue_lock(self, number, reason=None, role=None):
        raise Unsupported("issue lock")

    def review_edit_body(self, number, review_id, path, role=None):
        raise Unsupported("editing a review body")

    def pr_ready(self, number, undo=False, role=None):
        raise Unsupported("marking a pull request ready")

    def issue_body_edits(self, number):
        return None

    # --- repository, issues and comments ---

    def _repo_path(self):
        return f"/repos/{self.owner}/{self.repo}"

    @staticmethod
    def _field_names(fields, known):
        names = [name for name in fields.split(",") if name]
        for name in names:
            if name not in known:
                raise Unsupported(f"the {name} field")
        return names

    @staticmethod
    def _login(user):
        return {"login": (user or {}).get("login", "")}

    def _comment_shape(self, item):
        return {"id": str(item["id"]), "author": self._login(item.get("user")),
                "body": item.get("body") or "", "createdAt": item.get("created_at"),
                "url": item.get("html_url")}

    def _comments(self, number):
        items = self._paged(f"{self._repo_path()}/issues/{number}/comments")
        return [self._comment_shape(item) for item in items]

    _REPO_FIELDS = ("owner", "name", "nameWithOwner", "defaultBranchRef")
    _ISSUE_FIELDS = ("number", "title", "body", "state", "author", "labels", "createdAt",
                     "assignees", "comments")

    def repo_view(self, fields, empty=None):
        names = self._field_names(fields, self._REPO_FIELDS)
        item = self._request("GET", self._repo_path())
        shapes = {"owner": lambda: self._login(item.get("owner")),
                  "name": lambda: item["name"],
                  "nameWithOwner": lambda: item["full_name"],
                  "defaultBranchRef": lambda: {"name": item["default_branch"]}}
        return {name: shapes[name]() for name in names}

    def _issue_shape(self, item, names):
        state = "OPEN" if item.get("state") == "open" else "CLOSED"
        shapes = {"number": lambda: item["number"],
                  "title": lambda: item["title"],
                  "body": lambda: item.get("body") or "",
                  "state": lambda: state,
                  "author": lambda: self._login(item.get("user")),
                  "labels": lambda: [{"name": label["name"]} for label in item.get("labels") or []],
                  "createdAt": lambda: item.get("created_at"),
                  "assignees": lambda: [self._login(user) for user in item.get("assignees") or []],
                  "comments": lambda: self._comments(item["number"])}
        return {name: shapes[name]() for name in names}

    def issue_view(self, number, fields, empty=None):
        names = self._field_names(fields, self._ISSUE_FIELDS)
        item = self._request("GET", f"{self._repo_path()}/issues/{number}")
        return self._issue_shape(item, names)

    def issue_view_text(self, number, fields):
        return json.dumps(self.issue_view(number, fields))

    def issue_exists(self, number):
        try:
            return bool(self._request("GET", f"{self._repo_path()}/issues/{number}"))
        except ForgejoError:
            return False

    def issue_list(self, fields, state, limit, milestone=None, label=None, search=None,
                   empty=None):
        if search is not None:
            raise Unsupported("issue search")
        names = self._field_names(fields, self._ISSUE_FIELDS)
        params = {"type": "issues", "state": state}
        if label is not None:
            params["labels"] = label
        if milestone is not None:
            params["milestones"] = milestone
        items = self._paged(f"{self._repo_path()}/issues", params, limit)
        return [self._issue_shape(item, names) for item in items]

    def issue_list_text(self, fields, state, limit, milestone=None, label=None, search=None):
        return json.dumps(self.issue_list(fields, state, limit, milestone, label, search))

    @staticmethod
    def _read_file(path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def issue_edit_body(self, number, path, role=None):
        self._request("PATCH", f"{self._repo_path()}/issues/{number}",
                      body={"body": self._read_file(path)})
        return ""

    def issue_edit_labels(self, number, add, remove=(), role=None):
        base = f"{self._repo_path()}/issues/{number}/labels"
        if add:
            self._request("POST", base, body={"labels": list(add)})
        for name in remove:
            self._request("DELETE", f"{base}/{urllib.parse.quote(name, safe='')}")
        return ""

    def comment(self, target, number, path, role=None):
        made = self._request("POST", f"{self._repo_path()}/issues/{number}/comments",
                             body={"body": self._read_file(path)})
        return (made or {}).get("html_url", "") + "\n"

    def change_state(self, target, action, number, role=None):
        kind = "pulls" if target == "pr" else "issues"
        state = "closed" if action == "close" else "open"
        self._request("PATCH", f"{self._repo_path()}/{kind}/{number}", body={"state": state})
        return ""

    def issue_close(self, number, reason, duplicate_of=None, role=None):
        self._request("PATCH", f"{self._repo_path()}/issues/{number}", body={"state": "closed"})
        return ""

    def issue_create(self, title, path, labels=(), milestone=None, role=None):
        body = {"title": title, "body": self._read_file(path)}
        if labels:
            body["labels"] = self._label_ids(labels)
        if milestone:
            body["milestone"] = self._milestone_id(milestone)
        made = self._request("POST", f"{self._repo_path()}/issues", body=body)
        return (made or {}).get("html_url", "") + "\n"

    def item_edit(self, target, number, title=None, add_labels=(), remove_labels=(),
                  milestone=None, remove_milestone=False, path=None, role=None):
        body = {}
        if title:
            body["title"] = title
        if path is not None:
            body["body"] = self._read_file(path)
        if milestone:
            body["milestone"] = self._milestone_id(milestone)
        if remove_milestone:
            body["milestone"] = 0
        if body:
            kind = "pulls" if target == "pr" else "issues"
            self._request("PATCH", f"{self._repo_path()}/{kind}/{number}", body=body)
        if add_labels or remove_labels:
            self.issue_edit_labels(number, add_labels, remove_labels, role)
        return ""

    def comment_edit_body(self, comment_id, path, role=None):
        self._request("PATCH", f"{self._repo_path()}/issues/comments/{comment_id}",
                      body={"body": self._read_file(path)})
        return ""

    def comment_author_types(self, number):
        return {}

    def user(self, login):
        return self._request("GET", f"/users/{login}")

    def issue_timeline(self, number):
        try:
            items = self._paged(f"{self._repo_path()}/issues/{number}/timeline")
        except ForgejoError:
            return None
        events = []
        for item in items:
            kind = item.get("type")
            if kind == "assignees":
                names = ("unassigned", "assigned")
            elif kind == "review_request":
                names = ("review_request_removed", "review_requested")
            else:
                continue
            removed = bool(item.get("removed_assignee"))
            events.append({"event": names[0] if removed else names[1],
                           "by": (item.get("user") or {}).get("login", ""),
                           "subject": (item.get("assignee") or {}).get("login", ""),
                           "at": item.get("created_at")})
        return events

    # --- pull requests, reviews and merge ---

    _PR_FIELDS = ("number", "title", "body", "state", "isDraft", "mergeable", "headRefName",
                  "headRefOid", "baseRefName", "isCrossRepository", "changedFiles", "author",
                  "url", "reviewRequests", "comments", "files", "commits", "statusCheckRollup")

    @staticmethod
    def _pr_state(item):
        if item.get("merged"):
            return "MERGED"
        return "OPEN" if item.get("state") == "open" else "CLOSED"

    def _pr_files(self, number):
        return [{"path": item["filename"], "additions": item.get("additions", 0),
                 "deletions": item.get("deletions", 0)}
                for item in self._paged(f"{self._repo_path()}/pulls/{number}/files")]

    def _pr_commits(self, number):
        out = []
        for item in self._paged(f"{self._repo_path()}/pulls/{number}/commits"):
            head, _, rest = ((item.get("commit") or {}).get("message") or "").partition("\n")
            out.append({"oid": item["sha"], "messageHeadline": head,
                        "messageBody": rest.lstrip("\n")})
        return out

    def _pr_rollup(self, sha):
        found = self._request("GET", f"{self._repo_path()}/commits/{sha}/status") or {}
        return [{"__typename": "StatusContext", "context": status.get("context"),
                 "state": (status.get("status") or "").upper(),
                 "targetUrl": status.get("target_url")}
                for status in found.get("statuses") or []]

    def _pr_shape(self, item, names):
        head = item.get("head") or {}
        base = item.get("base") or {}
        number = item["number"]
        shapes = {
            "number": lambda: number,
            "title": lambda: item["title"],
            "body": lambda: item.get("body") or "",
            "state": lambda: self._pr_state(item),
            "isDraft": lambda: bool(item.get("draft")),
            "mergeable": lambda: "MERGEABLE" if item.get("mergeable") else "CONFLICTING",
            "headRefName": lambda: head.get("ref"),
            "headRefOid": lambda: head.get("sha"),
            "baseRefName": lambda: base.get("ref"),
            "isCrossRepository": lambda: (head.get("repo") or {}).get("full_name")
            != (base.get("repo") or {}).get("full_name"),
            "changedFiles": lambda: item.get("changed_files"),
            "author": lambda: self._login(item.get("user")),
            "url": lambda: item.get("html_url"),
            "reviewRequests": lambda: [self._login(user)
                                       for user in item.get("requested_reviewers") or []],
            "comments": lambda: self._comments(number),
            "files": lambda: self._pr_files(number),
            "commits": lambda: self._pr_commits(number),
            "statusCheckRollup": lambda: self._pr_rollup(head.get("sha")),
        }
        return {name: shapes[name]() for name in names}

    def pr_view(self, number, fields, empty=None):
        names = self._field_names(fields, self._PR_FIELDS)
        item = self._request("GET", f"{self._repo_path()}/pulls/{number}")
        return self._pr_shape(item, names)

    def pr_list_text(self, fields, state, limit):
        names = self._field_names(fields, self._PR_FIELDS)
        items = self._paged(f"{self._repo_path()}/pulls", {"state": state}, limit)
        return json.dumps([self._pr_shape(item, names) for item in items])

    def pr_create(self, title, path, head=None, draft=False, role=None):
        if draft:
            raise Unsupported("creating a draft pull request")
        if not head:
            head = self.runner(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip()
        base = self._request("GET", self._repo_path())["default_branch"]
        found = self._request("POST", f"{self._repo_path()}/pulls",
                              body={"title": title, "body": self._read_file(path),
                                    "head": head, "base": base})
        return found["html_url"] + "\n"

    def pr_diff(self, number):
        return self._request("GET", f"{self._repo_path()}/pulls/{number}.diff", raw=True)

    def pr_merge(self, number, head_oid, role=None):
        self._request("POST", f"{self._repo_path()}/pulls/{number}/merge",
                      body={"Do": "merge", "head_commit_id": head_oid})
        return ""

    _REVIEW_EVENTS = {"approve": "APPROVED", "request-changes": "REQUEST_CHANGES",
                      "comment": "COMMENT"}

    def pr_review(self, number, event, path, role=None):
        if event not in self._REVIEW_EVENTS:
            raise Unsupported(f"the review event {event}")
        self._request("POST", f"{self._repo_path()}/pulls/{number}/reviews",
                      body={"event": self._REVIEW_EVENTS[event], "body": self._read_file(path)})
        return ""

    def _reviews(self, number):
        return self._paged(f"{self._repo_path()}/pulls/{number}/reviews")

    def review_urls(self, number, login):
        items = [item for item in self._reviews(number) if item.get("submitted_at")
                 and (item.get("user") or {}).get("login") == login]
        items.sort(key=lambda item: item.get("submitted_at") or "")
        return [item["html_url"] for item in items if item.get("html_url")]

    def pr_review_items(self, number):
        return [{"id": str(item["id"]),
                 "author": {"login": (item.get("user") or {}).get("login", ""),
                            "type": "User"},
                 "body": item.get("body") or "", "createdAt": item["submitted_at"],
                 "url": item.get("html_url")}
                for item in self._reviews(number) if item.get("submitted_at")]

    def review_request_remove(self, number, login, role=None):
        self._request("DELETE", f"{self._repo_path()}/pulls/{number}/requested_reviewers",
                      body={"reviewers": [login]})
        return ""

    # --- actions runs, workflows and branches ---

    _RUN_FIELDS = ("databaseId", "status", "conclusion", "headSha", "headBranch",
                   "workflowName", "event", "url")
    _DONE = ("success", "failure", "cancelled", "skipped")

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
                  "conclusion": lambda: state if status == "completed" else "",
                  "headSha": lambda: item.get("commit_sha"),
                  "headBranch": lambda: item.get("prettyref"),
                  "workflowName": lambda: item.get("workflow_id"),
                  "event": lambda: item.get("event"),
                  "url": lambda: item.get("html_url")}
        return {name: shapes[name]() for name in names}

    def run_view(self, run_id, fields):
        names = self._field_names(fields, self._RUN_FIELDS)
        item = self._request("GET", f"{self._repo_path()}/actions/runs/{run_id}")
        return self._run_shape(item, names)

    def run_list_text(self, branch, sha, event, limit, fields):
        names = self._field_names(fields, self._RUN_FIELDS)
        found = self._request("GET", f"{self._repo_path()}/actions/runs",
                              params={"head_sha": sha, "event": event, "limit": limit}) or {}
        items = [item for item in found.get("workflow_runs") or []
                 if item.get("prettyref") == branch]
        return json.dumps([self._run_shape(item, names) for item in items])

    def workflow_entries_text(self, owner, repo, sha):
        base = f"/repos/{owner}/{repo}/contents"
        try:
            found = self._request("GET", f"{base}/.forgejo/workflows", params={"ref": sha})
        except ForgejoError as err:
            if "404" not in str(err):
                raise
            found = self._request("GET", f"{base}/.github/workflows", params={"ref": sha})
        return json.dumps(found)

    def workflow_file_text(self, owner, repo, name, sha):
        base = f"/repos/{owner}/{repo}/raw"
        try:
            return self._request("GET", f"{base}/.forgejo/workflows/{name}",
                                 params={"ref": sha}, raw=True)
        except ForgejoError as err:
            if "404" not in str(err):
                raise
            return self._request("GET", f"{base}/.github/workflows/{name}",
                                 params={"ref": sha}, raw=True)

    def actions_permissions_text(self, owner, repo):
        found = self._request("GET", f"/repos/{owner}/{repo}")
        return json.dumps({"enabled": bool(found.get("has_actions"))})

    def branch_head(self, owner, repo, branch):
        found = self._request(
            "GET", f"/repos/{owner}/{repo}/branches/{urllib.parse.quote(branch, safe='')}")
        commit = found.get("commit") or {}
        return {"sha": commit.get("id"),
                "commit": {"committer": {"date": commit.get("timestamp")}}}

    def last_push(self, owner, repo, branch):
        found = self._request("GET", f"/repos/{owner}/{repo}/activities/feeds",
                              params={"limit": 50}) or []
        for entry in found:
            if (entry.get("op_type") == "commit_repo"
                    and entry.get("ref_name") == f"refs/heads/{branch}"):
                return entry.get("created")
        return None
