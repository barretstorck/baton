"""The forge a repository lives on: GitHub, or Forgejo when forge detection says so."""
import os
import pathlib
import re

from .. import config, identity, run, sandbox
from .forgejo import Forgejo, ForgejoError
from .github import GitHub

_git = run.run
_detected = {}


def resolve(url, cfg):
    """{kind, url, owner, repo} for an origin URL and a loaded config (or None)."""
    parts = identity.remote_parts(url)
    local = None
    if parts:
        path = sandbox.brief_path(parts[1], parts[2])
        if path.is_file():
            local = config.parse_local_forge(path.read_text())
    committed = cfg.data["forge"] if cfg is not None else config.DEFAULTS["forge"]
    return identity.detect_forge(url, config.effective_forge(committed, local))


def detect(cwd=None):
    """The detected forge of the repository at `cwd`, or None; memoised per real path."""
    key = os.path.realpath(cwd or os.getcwd())
    if key in _detected:
        return _detected[key]
    try:
        url = _git(["git", "-C", key, "remote", "get-url", "origin"]).strip()
        top = _git(["git", "-C", key, "rev-parse", "--show-toplevel"]).strip()
        found = resolve(url, config.load_root(pathlib.Path(top)))
    except (run.RunError, OSError, identity.ForgeError, config.ConfigError):
        found = None
    _detected[key] = found
    return found


def _origin(cwd):
    try:
        return _git(["git", "-C", cwd or os.getcwd(), "remote", "get-url", "origin"]).strip()
    except (run.RunError, OSError):
        return ""


def _scheme_port(url):
    m = re.match(r"(https?)://(?:[^/@\s]*@)?[^/@:\s]+(?::(\d+))?(?:/|$)", url or "")
    if not m:
        return None, None
    return m.group(1), m.group(2) or {"http": "80", "https": "443"}[m.group(1)]


def check_url(url, origin):
    """Refuse a Forgejo URL that is cleartext, or whose scheme or port differs from origin's."""
    scheme, port = _scheme_port(url)
    o_scheme, o_port = _scheme_port(origin)
    if scheme is None:
        raise ForgejoError("Forgejo url must be http(s)")
    if scheme == "http" and o_scheme != "http":
        raise ForgejoError("Forgejo url must use https unless the origin remote is http")
    if o_scheme and (scheme, port) != (o_scheme, o_port):
        raise ForgejoError("Forgejo url scheme or port differs from the origin remote's")


def for_repo(cwd=None, runner=None):
    """The backend for this repository: Forgejo when detected, else GitHub."""
    found = detect(cwd)
    if found and found["kind"] == "forgejo":
        check_url(found["url"], _origin(cwd))
        return Forgejo(found["url"], found["owner"], found["repo"], runner=runner or run.run)
    return GitHub(runner or run.run)
