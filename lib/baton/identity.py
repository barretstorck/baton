"""Tier 1 identities: GitHub App entries read from ~/.baton/<owner>-<repo>/identity.toml."""
import base64
import datetime
import json
import os
import re
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import config
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
    if config.bot_slug(login) is None:
        raise IdentityError(f"identity.toml [{name}] login must be an App login such as {DEFAULT_LOGINS[name]}")
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


def _get(url, headers):
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise IdentityError(f"request failed: HTTP {e.code}")
    except (urllib.error.URLError, OSError, ValueError):
        raise IdentityError("request failed")


def _headers(bearer):
    return {"Authorization": f"Bearer {bearer}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def app_login(app, runner, get, now):
    """The App's actual login on GitHub: <slug>[bot]."""
    reply = get("https://api.github.com/app", _headers(jwt(app, runner, now)))
    slug = reply.get("slug") if isinstance(reply, dict) else None
    if not isinstance(slug, str) or not slug:
        raise IdentityError("GET /app returned no slug")
    return f"{slug}[bot]"


def installation_repos(token, get):
    """The full_name of every repository the installation token can reach."""
    names, page = set(), 1
    while True:
        reply = get(f"https://api.github.com/installation/repositories?per_page=100&page={page}",
                    _headers(token))
        repos = reply.get("repositories") if isinstance(reply, dict) else None
        if not isinstance(repos, list):
            raise IdentityError("GET /installation/repositories returned no repositories")
        names.update(r["full_name"] for r in repos
                     if isinstance(r, dict) and isinstance(r.get("full_name"), str))
        total = reply.get("total_count")
        if not repos or not isinstance(total, int) or len(names) >= total:
            return names
        page += 1


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


def bot_logins():
    """The login of each configured App; empty at tier 0 or with no loader."""
    ids = _identities()
    return frozenset(ids.logins()) if ids is not None else frozenset()


def reviewer_logins():
    """The reviewer App's login; empty at tier 0, with no loader or with no reviewer entry."""
    ids = _identities()
    if ids is None or ids.reviewer is None:
        return frozenset()
    return frozenset({ids.reviewer.login})


AUTHOR_TYPES_JQ = '.[] | [.node_id, .user.type] | @tsv'


def _is_candidate(comment, slugs):
    author = (comment or {}).get("author") or {}
    login = (author.get("login") or "").strip().lower()
    return (login in slugs and config.bot_slug(login) is None
            and "type" not in author and "__typename" not in author)


def needs_author_types(comments, slugs):
    """Whether some comment's author is a bare App slug with no type."""
    return any(_is_candidate(c, slugs) for c in comments or [])


def parse_author_types(text):
    """`node_id -> type` from tab-separated lines; blank or malformed lines are skipped."""
    types = {}
    for line in (text or "").splitlines():
        parts = line.strip().split("\t")
        if len(parts) == 2 and parts[0] and parts[1]:
            types[parts[0]] = parts[1]
    return types


def with_author_types(comments, types, slugs):
    """A new list; each candidate is copied with the REST author type ("User" when unknown)."""
    out = []
    for c in comments or []:
        if _is_candidate(c, slugs):
            author = {**c["author"], "type": types.get(c.get("id"), "User")}
            out.append({**c, "author": author})
        else:
            out.append(c)
    return out


def mark_authors(runner, number, comments):
    """Add the REST author type to comments whose bare login is an App's slug (one call per thread)."""
    slugs = {config.bot_slug(login) for login in bot_logins()} - {None}
    if not slugs or not needs_author_types(comments, slugs):
        return comments or []
    try:
        text = runner(["gh", "api", "--paginate",
                       f"repos/{{owner}}/{{repo}}/issues/{number}/comments?per_page=100",
                       "--jq", AUTHOR_TYPES_JQ])
        types = parse_author_types(text)
    except _run.RunError:
        types = {}
    return with_author_types(comments, types, slugs)


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


def bot_user_id(runner, login):
    """The numeric id of the App's bot user, which the noreply commit email needs (not the App id)."""
    try:
        reply = json.loads(runner(["gh", "api", f"users/{login}"]))
        uid = reply["id"]
    except (_run.RunError, ValueError, KeyError, TypeError):
        raise IdentityError(f"could not read the user id of {login}")
    if isinstance(uid, bool) or not isinstance(uid, int):
        raise IdentityError(f"could not read the user id of {login}")
    return uid


def commit_identity(runner):
    """(name, email) the developer commits as at tier 1; None at tier 0."""
    ids = _identities()
    if ids is None:
        return None
    if ids.baton is None:
        raise IdentityError("identity.toml has no [baton] entry for commits")
    login = ids.baton.login
    return login, f"{bot_user_id(runner, login)}+{login}@users.noreply.github.com"


_HELPER = ('!f() { test "$1" = get && echo username=x-access-token'
           ' && echo "password=$BATON_GIT_TOKEN"; }; f')


def refspec_problem(refspec, default_branch, ref_ok):
    """Why `baton push` refuses this refspec, or None when it is <source>:refs/heads/<branch>.

    `ref_ok(ref)` says whether git accepts the destination as a ref name (git check-ref-format).
    """
    if refspec.startswith("-"):
        return 'starts with "-"'
    if refspec.startswith("+"):
        return 'force push ("+")'
    if refspec.count(":") != 1:
        return "needs <source>:refs/heads/<branch>"
    for ch in refspec:
        if ch.isspace():
            return "whitespace in the refspec"
        if ch in "*^?[\\":
            return f"wildcard or special character {ch!r} in the refspec"
    src, dst = refspec.split(":")
    if not src:
        return "empty source deletes the branch"
    if not dst.startswith("refs/heads/") or dst == "refs/heads/":
        return "destination outside refs/heads/"
    if not ref_ok(dst):
        return "destination is not a valid branch ref (git check-ref-format)"
    if dst == f"refs/heads/{default_branch}":
        return f"destination is the default branch ({default_branch})"
    return None


def ref_format_ok(ref):
    """True when `git check-ref-format` accepts the ref name."""
    try:
        _run.run(["git", "check-ref-format", ref])
    except _run.RunError:
        return False
    return True


def github_repo(url):
    """`owner/repo` of a GitHub remote URL (https, ssh:// or scp-style); None for anything else."""
    url = (url or "").strip()
    m = (re.fullmatch(r"https://(?:[^/@]*@)?github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?/?", url)
         or re.fullmatch(r"ssh://(?:[^/@]*@)?github\.com(?::\d+)?/([^/\s]+)/([^/\s]+?)(?:\.git)?/?", url)
         or re.fullmatch(r"(?:[^/@:\s]+@)?github\.com:([^/\s]+)/([^/\s]+?)(?:\.git)?/?", url))
    return f"{m.group(1)}/{m.group(2)}" if m else None


def origin_problem(url, repo):
    """Why origin is not `repo` (owner/repo), or None when it is. Never repeats the URL."""
    found = github_repo(url)
    if found is None:
        return "origin is not a GitHub repository"
    if found.lower() != repo.lower():
        return f"origin is {found}, not {repo}"
    return None


def pushes_to_origin():
    """True at tier 0, where a push goes to the `origin` remote."""
    return _identities() is None


def push_command(refspec, repo):
    """(argv, env) for pushing a refspec; at tier 1 over HTTPS with the token in the child's env only."""
    if pushes_to_origin():
        return ["git", "push", "--", "origin", refspec], None
    ids = _identities()
    if ids.baton is None:
        raise IdentityError("identity.toml has no [baton] entry for pushes")
    tok = token(ids.baton, _run.run, _post, time.time())
    argv = ["git", "-c", "credential.helper=", "-c", f"credential.helper={_HELPER}",
            "push", "--", f"https://github.com/{repo}.git", refspec]
    return argv, {"BATON_GIT_TOKEN": tok, "GIT_TERMINAL_PROMPT": "0"}


def describe(ids):
    """Tier and the route and login per role, for `baton config`; no id, path or token."""
    roles = {}
    for role in (*config.ROLES, "orchestrator"):
        if ids is None:
            roles[role] = {"login": None, "route": "maintainer"}
            continue
        name = route(role)
        app = getattr(ids, name)
        roles[role] = {"login": app.login if app else None, "route": name}
    return {"tier": 0 if ids is None else 1, "roles": roles}
