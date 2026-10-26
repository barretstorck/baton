"""Verification logs and result files kept under the Baton home folder."""
import json
import os
import pathlib
import shutil


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
