import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import identity, run


class GitLabError(run.RunError):
    pass


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

    def __init__(self, url, owner, repo, token=None, runner=None, transport=None, sleep=None):
        self.url = url.rstrip("/")
        self.owner = owner
        self.repo = repo
        self._token = token
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

    def _project(self, owner=None, repo=None):
        full = f"{owner or self.owner}/{repo or self.repo}"
        return "/projects/" + urllib.parse.quote(full, safe="")

    def _request(self, method, path, params=None, body=None, raw=False):
        url = f"{self.url}/api/v4{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"PRIVATE-TOKEN": self._get_token(), "Accept": "application/json"}
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
            raise GitLabError(f"GitLab {method} {path}: HTTP {status}{message}")
        if raw:
            return text
        if not text.strip():
            return None
        return json.loads(text)

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

    def label_create(self, name, color, description, role=None):
        self._request("POST", f"{self._project()}/labels",
                      body={"name": name, "color": "#" + color, "description": description})
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
        self._request("POST", f"{self._project()}/milestones", body=body)
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
        self._request("PUT", f"{self._project()}/milestones/{number}", body=body)
        return ""

    def _milestone_id(self, title):
        for item in self._milestone_list("all"):
            if item["title"] == title:
                return item["number"]
        raise GitLabError(f"GitLab milestone not found: {title}")

    # --- repository, issues and notes ---

    _REPO_FIELDS = ("owner", "name", "nameWithOwner", "defaultBranchRef")
    _ISSUE_FIELDS = ("number", "title", "body", "state", "author", "labels", "createdAt",
                     "assignees", "comments")

    def repo_view(self, fields, empty=None):
        names = self._field_names(fields, self._REPO_FIELDS)
        item = self._request("GET", self._project())
        shapes = {"owner": lambda: {"login": (item.get("namespace") or {}).get("full_path", "")},
                  "name": lambda: item["path"],
                  "nameWithOwner": lambda: item["path_with_namespace"],
                  "defaultBranchRef": lambda: {"name": item["default_branch"]}}
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
        if search is not None:
            raise Unsupported("issue search")
        names = self._field_names(fields, self._ISSUE_FIELDS)
        params = {"state": {"open": "opened"}.get(state, state)}
        if label is not None:
            params["labels"] = label
        if milestone is not None:
            params["milestone"] = milestone
        items = self._paged(f"{self._project()}/issues", params, limit)
        return [self._issue_shape(item, names) for item in items]

    def issue_list_text(self, fields, state, limit, milestone=None, label=None, search=None):
        return json.dumps(self.issue_list(fields, state, limit, milestone, label, search))

    def issue_edit_body(self, number, path, role=None):
        self._request("PUT", f"{self._project()}/issues/{number}",
                      body={"description": self._read_file(path)})
        return ""

    def issue_edit_labels(self, number, add, remove=(), role=None):
        body = {}
        if add:
            body["add_labels"] = ",".join(add)
        if remove:
            body["remove_labels"] = ",".join(remove)
        if body:
            self._request("PUT", f"{self._project()}/issues/{number}", body=body)
        return ""

    def comment(self, target, number, path, role=None):
        kind = "merge_requests" if target == "pr" else "issues"
        made = self._request("POST", f"{self._project()}/{kind}/{number}/notes",
                             body={"body": self._read_file(path)})
        note = (made or {}).get("id", "")
        return f"{self.url}/{self.owner}/{self.repo}/-/{kind}/{number}#note_{note}\n"

    def change_state(self, target, action, number, role=None):
        kind = "merge_requests" if target == "pr" else "issues"
        event = "close" if action == "close" else "reopen"
        self._request("PUT", f"{self._project()}/{kind}/{number}", body={"state_event": event})
        return ""

    def issue_close(self, number, reason, duplicate_of=None, role=None):
        return self.change_state("issue", "close", number, role)

    def issue_lock(self, number, reason=None, role=None):
        self._request("PUT", f"{self._project()}/issues/{number}",
                      body={"discussion_locked": True})
        return ""

    def issue_create(self, title, path, labels=(), milestone=None, role=None):
        body = {"title": title, "description": self._read_file(path)}
        if labels:
            body["labels"] = ",".join(labels)
        if milestone:
            body["milestone_id"] = self._milestone_id(milestone)
        made = self._request("POST", f"{self._project()}/issues", body=body)
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
            self._request("PUT", f"{self._project()}/{kind}/{number}", body=body)
        return ""

    def user(self, login):
        found = self._request("GET", "/users", params={"username": login}) or []
        if not found:
            raise GitLabError(f"GitLab user not found: {login}")
        return {**found[0], "login": found[0].get("username", login)}

    # --- merge requests ---

    _PR_FIELDS = ("number", "title", "body", "state", "isDraft", "mergeable", "headRefName",
                  "headRefOid", "baseRefName", "isCrossRepository", "changedFiles", "author",
                  "url", "reviewRequests", "comments", "files", "commits")

    _UNKNOWN_MERGE = ("checking", "unchecked", "preparing", "approvals_syncing")

    @staticmethod
    def _mr_state(item):
        state = item.get("state")
        if state == "merged":
            return "MERGED"
        return "OPEN" if state == "opened" else "CLOSED"

    @classmethod
    def _mr_mergeable(cls, item):
        if item.get("has_conflicts"):
            return "CONFLICTING"
        if item.get("detailed_merge_status") in cls._UNKNOWN_MERGE:
            return "UNKNOWN"
        return "MERGEABLE"

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
            out.append({"path": item["new_path"],
                        "additions": sum(1 for line in lines if line.startswith("+")),
                        "deletions": sum(1 for line in lines if line.startswith("-"))})
        return out

    def _mr_commits(self, iid):
        out = []
        for item in self._paged(f"{self._project()}/merge_requests/{iid}/commits"):
            _, _, rest = (item.get("message") or "").partition("\n")
            out.append({"oid": item["id"], "messageHeadline": item.get("title") or "",
                        "messageBody": rest.lstrip("\n")})
        return out

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
        }
        return {name: shapes[name]() for name in names}

    def pr_view(self, number, fields, empty=None):
        names = self._field_names(fields, self._PR_FIELDS)
        item = self._request("GET", f"{self._project()}/merge_requests/{number}")
        return self._mr_shape(item, names)

    def pr_list_text(self, fields, state, limit):
        names = self._field_names(fields, self._PR_FIELDS)
        wanted = {"open": "opened"}.get(state, state)
        items = self._paged(f"{self._project()}/merge_requests", {"state": wanted}, limit)
        return json.dumps([self._mr_shape(item, names) for item in items])

    def pr_diff(self, number):
        out = []
        for item in self._mr_diffs(number):
            old, new = item.get("old_path"), item.get("new_path")
            text = item.get("diff") or ""
            if not text and (item.get("too_large") or item.get("collapsed")):
                raise GitLabError(f"GitLab merge request {number}: the diff of {new} "
                                  "is too large to read")
            out.append(f"diff --git a/{old} b/{new}\n")
            out.append("--- " + ("/dev/null" if item.get("new_file") else f"a/{old}") + "\n")
            out.append("+++ " + ("/dev/null" if item.get("deleted_file") else f"b/{new}") + "\n")
            out.append(text if not text or text.endswith("\n") else text + "\n")
        return "".join(out)

    # --- not supported yet ---

    def review_edit_body(self, number, review_id, path, role=None):
        raise Unsupported("editing a review body")

    def comment_edit_body(self, comment_id, path, role=None):
        raise Unsupported("editing a note")

    def pr_ready(self, number, undo=False, role=None):
        raise Unsupported("marking a merge request ready")

    def workflow_entries_text(self, *args, **kwargs):
        raise Unsupported("GitHub Actions workflow reads")

    def workflow_file_text(self, *args, **kwargs):
        raise Unsupported("GitHub Actions workflow reads")

    def actions_permissions_text(self, *args, **kwargs):
        raise Unsupported("GitHub Actions permission reads")

    def issue_body_edits(self, number):
        return None

    def issue_timeline(self, number):
        return None

    def comment_author_types(self, number):
        return {}
