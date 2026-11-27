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
