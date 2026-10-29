"""Sandbox command template from the local brief, and its rendering."""
import datetime
import pathlib
import re
import shlex
import shutil
import tempfile

from baton import verify


class SandboxError(Exception):
    pass


def parse_template(brief_text):
    """Return the `Command:` value of the `## Sandbox` section, or None."""
    text = re.sub(r"<!--.*?-->", "", brief_text, flags=re.S)
    in_section = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_section = line[3:].strip().lower() == "sandbox"
            continue
        if in_section:
            m = re.match(r"\s*Command:\s*(.*?)\s*$", line)
            if m and m.group(1):
                return m.group(1)
    return None


def render(template, checkout, command):
    for placeholder in ("{checkout}", "{command}"):
        if placeholder not in template:
            raise SandboxError(f"sandbox template lacks {placeholder}")
    return template.replace("{checkout}", shlex.quote(str(checkout))).replace(
        "{command}", shlex.quote(command))


def brief_path(owner, repo, home=None):
    return pathlib.Path(verify._home(home)) / f"{owner}-{repo}" / "brief.local.md"


def load_template(owner, repo, home=None):
    path = brief_path(owner, repo, home)
    if not path.is_file():
        return None
    return parse_template(path.read_text())


def _log_path(owner, repo, now, home):
    folder = pathlib.Path(verify._home(home)) / f"{owner}-{repo}" / "logs" / "sandbox"
    folder.mkdir(parents=True, exist_ok=True)
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    return folder / f"sandbox-{moment.strftime('%Y%m%dT%H%M%SZ')}.log"


def _run(runner, owner, repo, default_branch, command, timeout_seconds, execute, now, home):
    template = load_template(owner, repo, home)
    if not template:
        raise SandboxError("no sandbox configured: add a `Command:` line under `## Sandbox` "
                           "in the local brief")
    url = runner(["git", "remote", "get-url", "origin"]).strip()
    base = pathlib.Path(verify._home(home)) / f"{owner}-{repo}" / "sandbox"
    base.mkdir(parents=True, exist_ok=True)
    checkout = tempfile.mkdtemp(dir=base)
    try:
        runner(["git", "clone", "--quiet", "--single-branch", "--branch", default_branch,
                url, checkout])
        rendered = render(template, checkout, command)
        log = _log_path(owner, repo, now, home)
        status = execute(rendered, checkout, log, timeout_seconds)
    finally:
        shutil.rmtree(checkout, ignore_errors=True)
    return status, log


def run_in_sandbox(runner, owner, repo, default_branch, command, timeout_seconds,
                   execute=verify.execute, now=None, home=None):
    """Run `command` in a fresh checkout of the default branch through the template."""
    status, log = _run(runner, owner, repo, default_branch, command, timeout_seconds,
                       execute, now, home)
    return {"exit": 3 if status is None else status, "log": str(log),
            "tail": verify.tail(log)}


def check(owner, repo, home=None):
    """Return "configured" if the local brief has a sandbox command, else "none". Runs nothing."""
    return "configured" if load_template(owner, repo, home) else "none"
