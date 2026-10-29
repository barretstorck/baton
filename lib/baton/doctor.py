"""`baton doctor`: print the identity tier and check each configured GitHub App."""
import time

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


def check(ids, repo, runner, get, post, now):
    if ids is None:
        return ["tier: 0"], True
    lines = ["tier: 1"]
    for name in identity.NAMES:
        app = getattr(ids, name, None)
        if app is not None:
            lines.append(_check_app(name, app, repo, runner, get, post, now))
    return lines, not any(": FAIL: " in line for line in lines[1:])


def main(repo, runner=_run.run, get=identity._get, post=identity._post, now=None, home=None):
    owner, name = repo.split("/", 1)
    try:
        ids = identity.load(owner, name, home=home)
    except identity.IdentityError as e:
        print("tier: unknown")
        print(f"identity.toml: {e}")
        return 1
    lines, ok = check(ids, repo, runner, get, post, time.time() if now is None else now)
    print("\n".join(lines))
    return 0 if ok else 1
