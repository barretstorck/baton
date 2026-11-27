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


def urllib_transport(method, url, headers, data):
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as reply:
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
