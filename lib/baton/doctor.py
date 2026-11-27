"""`baton doctor`: print the identity tier, check each configured GitHub App and, at tier 2, the machine user, and warn about unlimited spikes."""
import datetime
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import identity
from . import run as _run


def _check_app(name, app, repo, runner, get, post, now):
    try:
        actual = identity.app_login(app, runner, get, now)
        if actual.lower() != app.login.lower():
            return f"{name}: FAIL: login is {actual} on GitHub, identity.toml says {app.login}"
        tok = identity.token(app, runner, post, now)
        repos = identity.installation_repos(tok, get)
    except identity.IdentityError as e:
        return f"{name}: FAIL: {e}"
    if repo not in repos:
        return f"{name}: FAIL: the installation does not include {repo}"
    return f"{name}: ok ({app.login}, can read {repo})"


def _check_machine(login, app, repo, runner, status, post, now):
    try:
        tok = identity.token(app, runner, post, now)
        code = status(f"https://api.github.com/repos/{repo}/assignees/{login}",
                      identity._headers(tok))
    except identity.IdentityError as e:
        return f"machine: FAIL: {e}"
    if code != 204:
        return (f"machine: FAIL: {login} cannot be assigned on {repo} (HTTP {code}); "
                "invite it as a collaborator and accept the invitation")
    return f"machine: ok ({login} can be assigned on {repo})"


def check(ids, repo, runner, get, post, now, status=identity._status):
    if ids is None:
        return ["tier: 0"], True
    lines = ["tier: 2" if ids.machine else "tier: 1"]
    for name in identity.NAMES:
        app = getattr(ids, name, None)
        if app is not None:
            lines.append(_check_app(name, app, repo, runner, get, post, now))
    if ids.machine:
        lines.append(_check_machine(ids.machine, ids.baton, repo, runner, status, post, now))
    return lines, not any(": FAIL: " in line for line in lines[1:])


SPIKE_WARNING = ("spike: WARN: spike.tokens and spike.minutes have no range, so spikes have no "
                 "token or time limit; set a max: under spike.tokens or spike.minutes in "
                 ".baton/brief.md")


def spike_lines(spike):
    if not spike or spike.get("tokens") or spike.get("minutes"):
        return []
    return [SPIKE_WARNING]


GITHUB_ONLY_SKIPS = [
    "app: skipped (GitHub-only: GitHub App installation check)",
    "machine: skipped (GitHub-only: api.github.com assignee check)",
]

FORGEJO_VERSION_PATH = "/api/forgejo/v1/version"


def forgejo_lines(url, get=identity._get):
    """Probe a Forgejo host (Gitea does not serve this path); (lines, ok)."""
    try:
        reply = get(url.rstrip("/") + FORGEJO_VERSION_PATH, {"Accept": "application/json"})
        version = reply.get("version") if isinstance(reply, dict) else None
        if not isinstance(version, str):
            raise identity.IdentityError("no version in the reply")
    except identity.IdentityError as e:
        return ([f"forge: FAIL: forge.url does not answer {FORGEJO_VERSION_PATH} ({e}), "
                 "so it is not Forgejo (Gitea does not serve this path)"], False)
    return [f"forge: ok (Forgejo {version})"], True


GITLAB_VERSION_PATH = "/api/v4/version"
GITLAB_SELF_PATH = "/api/v4/personal_access_tokens/self"
ROTATE_WITHIN_DAYS = 30
GITLAB_MIN_VERSION = (17, 0)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect, so a token header never reaches another URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def http_get(url, headers):
    """GET url; (status, headers with lower-cased names, body bytes); (None, {}, b"") when unreachable."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with _opener.open(req, timeout=30) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
        except OSError:
            body = b""
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, body
    except (urllib.error.URLError, OSError):
        return None, {}, b""


def gitlab_lines(url, token, get=http_get):
    """Probe a GitLab host by its X-Gitlab-Meta header, then its version; (lines, ok)."""
    headers = {"Accept": "application/json"}
    if token:
        headers["PRIVATE-TOKEN"] = token
    status, reply_headers, body = get(url.rstrip("/") + GITLAB_VERSION_PATH, headers)
    if "x-gitlab-meta" not in (reply_headers or {}):
        return [f"forge: FAIL: forge.url does not send the X-Gitlab-Meta header on {GITLAB_VERSION_PATH}, "
                "so it is not GitLab"], False
    if not token:
        host = urllib.parse.urlsplit(url).netloc or url
        return [f"forge: WARN: GitLab {host}; version not checked: no token configured"], True
    if status != 200:
        return [f"forge: FAIL: GitLab refused the token on {GITLAB_VERSION_PATH} (HTTP {status})"], False
    try:
        data = json.loads(body)
        version = data.get("version") if isinstance(data, dict) else None
        major, minor = (int(x) for x in version.split(".")[:2])
    except (ValueError, TypeError, AttributeError):
        return [f"forge: FAIL: {GITLAB_VERSION_PATH} gave no readable version"], False
    if (major, minor) < GITLAB_MIN_VERSION:
        floor = ".".join(str(x) for x in GITLAB_MIN_VERSION)
        return [f"forge: FAIL: GitLab {version} is below the minimum {floor}"], False
    return [f"forge: ok (GitLab {version}, plan {data.get('plan') or 'unknown'})"], True


def gitlab_expiry_lines(url, owner, repo, today, get=http_get, home=None):
    """Warnings for role tokens that expire within ROTATE_WITHIN_DAYS of `today`; never a token."""
    try:
        paths = identity.role_token_paths("gitlab", owner, repo, home=home)
        tokens = {name: identity._read_token(path, f"[gitlab.{name}] token_path file")
                  for name, path in paths.items()}
    except identity.IdentityError as e:
        return [f"token: WARN: {e}"]
    lines = []
    for name, token in tokens.items():
        status, _headers, body = get(url.rstrip("/") + GITLAB_SELF_PATH,
                                     {"Accept": "application/json", "PRIVATE-TOKEN": token})
        if status == 401:
            lines.append(f"token: WARN: [gitlab.{name}] was refused (HTTP 401); "
                         "it may have expired: renew it by hand")
            continue
        try:
            if status != 200:
                raise ValueError
            data = json.loads(body)
            value = data.get("expires_at") if isinstance(data, dict) else None
            expires = datetime.date.fromisoformat(str(value)[:10]) if value else None
        except (ValueError, TypeError):
            lines.append(f"token: WARN: [gitlab.{name}] expiry not checked (HTTP {status})")
            continue
        if expires is None:
            continue
        days = (expires - today).days
        if days <= ROTATE_WITHIN_DAYS:
            lines.append(f"token: WARN: [gitlab.{name}] expires {expires.isoformat()} (in {days} days); "
                         "run baton identity rotate")
    return lines


def main(repo, runner=_run.run, get=identity._get, post=identity._post, now=None, home=None,
         spike=None, status=identity._status):
    owner, name = repo.split("/", 1)
    try:
        ids = identity.load(owner, name, home=home)
    except identity.IdentityError as e:
        print("tier: unknown")
        print(f"identity.toml: {e}")
        for line in spike_lines(spike):
            print(line)
        return 1
    lines, ok = check(ids, repo, runner, get, post, time.time() if now is None else now, status)
    print("\n".join(lines + spike_lines(spike)))
    return 0 if ok else 1
