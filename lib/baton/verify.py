"""Verification logs and result files kept under the Baton home folder."""
import datetime
import json
import os
import pathlib
import shlex
import shutil
import signal
import subprocess
import time


class VerifyError(Exception):
    pass


def _home(home=None):
    if home is not None:
        return pathlib.Path(home)
    return pathlib.Path(os.environ.get("BATON_HOME") or os.path.expanduser("~/.baton"))


def logs_dir(owner, repo, number, home=None):
    return _home(home) / f"{owner}-{repo}" / "logs" / f"pr-{number}"


def write_result(folder, commit, result, exit_status, seconds, stamp):
    folder = pathlib.Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"verify-{commit[:7]}-{stamp}.json"
    data = {"commit": commit, "result": result, "exit": exit_status, "seconds": seconds}
    path.write_text(json.dumps(data) + "\n")
    return path


def latest_result(owner, repo, number, commit, home=None):
    folder = logs_dir(owner, repo, number, home)
    try:
        files = sorted(folder.glob("verify-*.json"), key=lambda p: p.name.rsplit("-", 1)[-1],
                       reverse=True)
    except OSError:
        return None
    for path in files:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("commit") == commit:
            return data
    return None


def remove_logs(owner, repo, number, home=None):
    folder = logs_dir(owner, repo, number, home)
    if not folder.is_dir():
        return False
    shutil.rmtree(folder)
    return True


def tail(path, lines=40):
    text = pathlib.Path(path).read_text(errors="replace")
    return text.splitlines()[-lines:] if lines > 0 else []


def execute(command, cwd, log_path, timeout_seconds):
    """Run `command` through the shell in its own process group, logging stdout and stderr.

    Returns the exit status, or None when it ran past the timeout (the group is killed).
    """
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(command, shell=True, cwd=cwd, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)
        try:
            return proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.wait()
            return None


def verify(cfg, runner, number, worktree, owner, repo, execute=execute, now=None, home=None):
    """Run the project's verification command in `worktree` and record the result.

    Refuses if no command is configured, if the worktree is not at the pull
    request's head, or if it has uncommitted changes to tracked files.
    Writes nothing to the pull request.
    """
    command = cfg["verify"]["command"]
    if not command:
        raise VerifyError("verify.command is empty in .baton/brief.md; nothing to run")
    head = json.loads(runner(["gh", "pr", "view", str(number), "--json", "headRefOid"]))["headRefOid"]
    local = runner(["git", "-C", worktree, "rev-parse", "HEAD"]).strip()
    if local != head:
        raise VerifyError(f"the worktree is at {local[:7]} but the pull request's head is "
                          f"{head[:7]}; check out the head and run again")
    if runner(["git", "-C", worktree, "status", "--porcelain", "--untracked-files=no"]).strip():
        raise VerifyError("the worktree has uncommitted changes; commit or discard them first")
    command = command.replace("{worktree}", shlex.quote(worktree))
    folder = logs_dir(owner, repo, number, home)
    folder.mkdir(parents=True, exist_ok=True)
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    stamp = moment.strftime("%Y%m%dT%H%M%SZ")
    log = folder / f"verify-{head[:7]}-{stamp}.log"
    started = time.monotonic()
    status = execute(command, worktree, log, cfg["verify"]["timeout_minutes"] * 60)
    seconds = round(time.monotonic() - started)
    result = "timeout" if status is None else "green" if status == 0 else "red"
    write_result(folder, head, result, status, seconds, stamp)
    return {"result": result, "exit": status, "seconds": seconds, "commit": head,
            "log": str(log), "tail": tail(log, 40)}
