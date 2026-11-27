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

    def issue_lock(self, number, reason=None, role=None):
        raise Unsupported("issue lock")

    def review_edit_body(self, number, review_id, path, role=None):
        raise Unsupported("editing a review body")

    def pr_ready(self, number, undo=False, role=None):
        raise Unsupported("marking a pull request ready")

    def issue_body_edits(self, number):
        return None
