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
