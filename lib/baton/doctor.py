"""`baton doctor`: print the identity tier, check each configured GitHub App and, at tier 2, the machine user, and warn about unlimited spikes."""
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
