"""`baton doctor`: print the identity tier, check each configured GitHub App, and warn about unlimited spikes."""
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


SPIKE_WARNING = ("spike: WARN: spike.tokens and spike.minutes have no range, so spikes have no "
                 "token or time limit; set a max: under spike.tokens or spike.minutes in "
                 ".baton/brief.md")


def spike_lines(spike):
    if not spike or spike.get("tokens") or spike.get("minutes"):
        return []
    return [SPIKE_WARNING]


def main(repo, runner=_run.run, get=identity._get, post=identity._post, now=None, home=None,
         spike=None):
    owner, name = repo.split("/", 1)
    try:
        ids = identity.load(owner, name, home=home)
    except identity.IdentityError as e:
        print("tier: unknown")
        print(f"identity.toml: {e}")
        for line in spike_lines(spike):
            print(line)
        return 1
    lines, ok = check(ids, repo, runner, get, post, time.time() if now is None else now)
    print("\n".join(lines + spike_lines(spike)))
    return 0 if ok else 1
