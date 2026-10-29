"""Tier 1 identities: GitHub App entries read from ~/.baton/<owner>-<repo>/identity.toml."""
import base64
import datetime
import json
import os
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import run as _run
from . import verify

NAMES = ("baton", "reviewer")
DEFAULT_LOGINS = {"baton": "baton[bot]", "reviewer": "baton-reviewer[bot]"}


class IdentityError(Exception):
    pass


@dataclass
class App:
    app_id: int
    installation_id: int
    key_path: str
    login: str


@dataclass
class Identities:
    baton: "App | None" = None
    reviewer: "App | None" = None

    def logins(self):
        return [app.login for app in (self.baton, self.reviewer) if app is not None]


def _int(name, key, value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise IdentityError(f"identity.toml [{name}] {key} must be an integer")
    return value


def _app(name, table):
    if not isinstance(table, dict):
        raise IdentityError(f"identity.toml [{name}] must be a table")
    for key in ("app_id", "installation_id", "key_path"):
        if key not in table:
            raise IdentityError(f"identity.toml [{name}] is missing {key}")
    key_path = table["key_path"]
    if not isinstance(key_path, str) or not key_path:
        raise IdentityError(f"identity.toml [{name}] key_path must be a non-empty string")
    login = table.get("login", DEFAULT_LOGINS[name])
    if not isinstance(login, str) or not login:
        raise IdentityError(f"identity.toml [{name}] login must be a non-empty string")
    return App(_int(name, "app_id", table["app_id"]),
               _int(name, "installation_id", table["installation_id"]), key_path, login)


def load(owner, repo, home=None):
    path = verify._home(home) / f"{owner}-{repo}" / "identity.toml"
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise IdentityError(f"identity.toml is not valid TOML: {e}")
    unknown = sorted(set(data) - set(NAMES))
    if unknown:
        raise IdentityError(f"identity.toml has unknown entries: {', '.join(unknown)}")
    return Identities(**{name: _app(name, data[name]) for name in NAMES if name in data})


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def jwt(app, runner, now):
    """Sign a GitHub App JWT; openssl does the RS256 signing so the key stays out of memory."""
    head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = _b64(json.dumps({"iat": int(now) - 60, "exp": int(now) + 540, "iss": app.app_id},
                             separators=(",", ":")).encode())
    signing_input = f"{head}.{claims}"
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "sig")
        try:
            runner(["openssl", "dgst", "-sha256", "-sign", os.path.expanduser(app.key_path),
                    "-out", out], input=signing_input)
            with open(out, "rb") as f:
                sig = f.read()
        except (_run.RunError, OSError):
            raise IdentityError("could not sign the App JWT with openssl")
    if not sig:
        raise IdentityError("could not sign the App JWT with openssl")
    return f"{signing_input}.{_b64(sig)}"


def _post(url, headers):
    req = urllib.request.Request(url, data=b"", headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise IdentityError(f"token request failed: HTTP {e.code}")
    except (urllib.error.URLError, OSError, ValueError):
        raise IdentityError("token request failed")


_cache = {}


def _expiry(text):
    return datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def token(app, runner, post, now):
    """An installation token, memoised in-process until 60 seconds before it expires."""
    hit = _cache.get(app.installation_id)
    if hit and now < hit[1] - 60:
        return hit[0]
    signed = jwt(app, runner, now)
    url = f"https://api.github.com/app/installations/{app.installation_id}/access_tokens"
    headers = {"Authorization": f"Bearer {signed}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    try:
        reply = post(url, headers)
        tok = reply["token"]
        expires = _expiry(reply["expires_at"])
    except IdentityError:
        raise
    except Exception:
        raise IdentityError("token request failed or returned an unexpected reply")
    if not isinstance(tok, str) or not tok:
        raise IdentityError("token request returned no token")
    _cache[app.installation_id] = (tok, expires)
    return tok


def route(role):
    """The identity entry a role writes as: the reviewer uses its own App, every other role `baton`."""
    return "reviewer" if role == "reviewer" else "baton"


_UNSET = object()
_loader = None
_loaded = _UNSET


def use(loader):
    """Register the zero-argument loader of Identities (or None); it is called once, on the first write."""
    global _loader, _loaded
    _loader = loader
    _loaded = _UNSET


def _identities():
    global _loaded
    if _loaded is _UNSET:
        _loaded = _loader() if _loader else None
    return _loaded


def write(runner, argv, role, **kw):
    """Run a forge write as the role's App at tier 1; exactly `runner(argv, **kw)` at tier 0."""
    ids = _identities()
    if ids is None:
        return runner(argv, **kw)
    name = route(role)
    app = getattr(ids, name)
    if app is None:
        raise IdentityError(f"identity.toml has no [{name}] entry for role {role}")
    tok = token(app, _run.run, _post, time.time())
    return runner(argv, env={"GH_TOKEN": tok, "GH_PROMPT_DISABLED": "1"}, **kw)
