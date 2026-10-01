"""Decide whether a Bash command may publish to the forge.

Runs as a Claude Code PreToolUse hook. It scans the command text and any
files the command references. Diffs are scanned by the git hooks
(githooks.py), which this gate requires to be installed and not bypassed.
"""
import json
import os
import re
import sys

from . import config, run, scan

MARKER = "<!-- baton:"
HOOK_TAG = "baton git-hook"

_GH_PUBLISH = re.compile(
    r"\bgh\s+(?:(?:issue|pr)\s+(?:create|comment|edit|review|close)|api|"
    r"release\s+(?:create|edit)|label\s+(?:create|edit)|milestone)\b")
_GH_COMMENT = re.compile(r"\bgh\s+(?:issue|pr)\s+(?:comment|review)\b")
_GIT_COMMIT = re.compile(r"\bgit\b[^|;&\n]*?\bcommit\b")
_GIT_PUSH = re.compile(r"\bgit\b[^|;&\n]*?\bpush\b")
_BYPASS = re.compile(r"--no-verify\b|core\.hooksPath")
_COMMIT_SHORT_N = re.compile(r"\bgit\b[^|;&\n]*?\bcommit\b[^|;&\n]*?\s-[a-mo-zA-Z]*n[a-zA-Z]*\b")
_DIR = re.compile(r"""(?:\bcd\s+|\s-C\s+)("[^"]+"|'[^']+'|[^\s;&|]+)""")
_FILE_ARG = re.compile(
    r"""(?:--body-file|--file|--input|--template|-F)(?:=|\s+)("[^"]+"|'[^']+'|[^\s;&|]+)""")
_MAX_FILE = 1_000_000


def _candidate_dirs(command, cwd):
    dirs = [cwd]
    for match in _DIR.finditer(command):
        path = os.path.expanduser(match.group(1).strip("\"'"))
        dirs.append(os.path.join(cwd, path))
    return dirs


def _find_config(command, cwd):
    for directory in _candidate_dirs(command, cwd):
        if not os.path.isdir(directory):
            continue
        cfg = config.load(directory)
        if cfg is not None:
            return cfg
    return None


def _referenced_text(command, bases):
    chunks = []
    for match in _FILE_ARG.finditer(command):
        value = match.group(1).strip("\"'")
        if "@" in value:
            value = value.split("@", 1)[1]
        for base in bases:
            path = os.path.join(base, os.path.expanduser(value))
            if os.path.isfile(path) and os.path.getsize(path) <= _MAX_FILE:
                with open(path, errors="replace") as handle:
                    chunks.append(handle.read())
                break
    return "\n".join(chunks)


def _hooks_installed(root):
    try:
        common = run.run(["git", "-C", str(root), "rev-parse", "--git-common-dir"]).strip()
    except run.RunError:
        return False
    hooks = os.path.join(root, common, "hooks")
    for name in ("pre-commit", "pre-push"):
        try:
            with open(os.path.join(hooks, name)) as handle:
                if HOOK_TAG not in handle.read():
                    return False
        except OSError:
            return False
    return True


def decide(command, cwd, terms):
    is_commit = bool(_GIT_COMMIT.search(command))
    is_push = bool(_GIT_PUSH.search(command))
    if not (_GH_PUBLISH.search(command) or is_commit or is_push):
        return []
    try:
        cfg = _find_config(command, cwd)
    except config.ConfigError as error:
        return [f"{error}. Fix the brief before publishing."]
    if cfg is None:
        return []

    problems = []
    if is_commit or is_push:
        if _BYPASS.search(command) or (is_commit and _COMMIT_SHORT_N.search(command)):
            problems.append("git hook bypass (--no-verify, -n or core.hooksPath) is not allowed here")
        if not _hooks_installed(cfg.root):
            problems.append("Baton git hooks are not installed; run `baton install-hooks`")

    text = command + "\n" + _referenced_text(command, [cwd, str(cfg.root)])
    findings = scan.scan_text(text, terms, cfg.data["privacy_allow"])
    problems.extend(f"privacy: {line} of the command or its body file"
                    for line in scan.describe(findings))

    if _GH_COMMENT.search(command) and MARKER not in text:
        problems.append("agent comments must start with the Baton marker "
                        "<!-- baton:<role>:<kind> -->")
    return problems


def main(stdin_text):
    try:
        payload = json.loads(stdin_text)
    except ValueError:
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    command = payload.get("tool_input", {}).get("command", "")
    cwd = payload.get("cwd") or os.getcwd()
    problems = decide(command, cwd, scan.load_terms())
    if not problems:
        return 0
    print("Baton publish gate blocked this command:", file=sys.stderr)
    for problem in problems:
        print(f"- {problem}", file=sys.stderr)
    print("Replace private details with placeholders and retry.", file=sys.stderr)
    return 2
