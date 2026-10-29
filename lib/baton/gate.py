"""Decide whether a Bash command or an MCP tool call may publish to the forge.

Runs as a Claude Code PreToolUse hook. The gate is a text scanner: it does not
parse shell and does not know which `gh` subcommand writes. For a command that
names a forge-facing program (`FORGE_CLIS`) or publishes with git (`commit`,
`push`, `tag`), it scans every quoted string, heredoc body and body file the
command would send. It refuses only for four safety reasons: a privacy finding
in that text, text it cannot read (stdin, a missing body file, command
substitution in a double-quoted body), a git hook bypass, and missing hooks.
Diffs are scanned by the git hooks (githooks.py), which the gate requires to be
installed and not bypassed.

This guards against mistakes and nudged agents. It is not a sandbox: a script
that talks to the forge without one of these programs is outside its view.
"""
import json
import os
import re
import signal
import sys
import tempfile

from . import config, githooks, scan

MARKER = "<!-- baton:"
HOOK_TAG = githooks.HOOK_TAG
_MAX_FILE = 1_000_000

FORGE_CLIS = ("gh", "glab", "tea", "fj", "curl", "wget", "http", "https", "xh")
_HTTP_CLIS = ("curl", "wget", "http", "https", "xh")

# Command position: start of text, after a separator, quote or newline, or after a launcher.
_POS = (r"""(?:^|[;&|(`'"\n{]|\bsudo\b|\benv\b|\bxargs\b|\bexec\b|\bcommand\b|\btime\b|\bnohup\b)"""
        r"""\s*(?:[A-Za-z_]\w*=\S*\s+)*""")
_FORGE = re.compile(_POS + r"(?:[\w.~+-]*/)*(" + "|".join(FORGE_CLIS) + r")(?![\w.-])")
_GITPUB = re.compile(_POS + r"(?:[\w.~+-]*/)*git\b[^;&|\n]*?\b(?:commit|push|tag)\b")
# Run on the command with quoted strings blanked, so a commit message may mention these.
_BYPASS_TEXT = re.compile(
    r"--no-verify\b|--config-env\b"
    r"|(?:^|[\s;&|])(?:HOME|XDG_CONFIG_HOME|BATON_PRIVATE_TERMS|GIT_CONFIG\w*)=")
# Run on the unblanked command: a quoted `-c "core.hooksPath=/x"` value must still be refused.
_BYPASS_CFG = re.compile(
    r"""-c\s*["']?core\.hooksPath|config\s+(?:--\S+\s+)*["']?core\.hooksPath["']?\s+\S""")
_SHORT_N = re.compile(_POS + r"git\b[^;&|\n]*?\b(?:commit|push)\b[^;&|\n]*?\s-[a-zA-Z]*n[a-zA-Z]*(?:\s|$)")
_STDIN_BODY = re.compile(r"(?:--body-file|-F|--input|--data(?:-binary|-raw)?|-d)(?:\s+|=)(?:-|@-)(?:\s|$)")
_GIT_STDIN = re.compile(r"(?:--file|-F)(?:\s+|=)-(?:\s|$)")
_FILE_ARG = re.compile(
    r"""(?<![\w-])(?:(--body-file|--file|--input|--data(?:-binary|-raw)?)(?:\s+|=)|(-F|-d)(?:\s+|=|(?=\S)))"""
    r"""(?:"([^"]*)"|'([^']*)'|([^\s'"<>|;&]+))""")
_UNRESOLVABLE = re.compile(r"[$`*?]")
_SUBST_BODY = re.compile(r"(?:--body|-b|--title|-t|--message|-m|--raw-field|-f|--field|-d|--data(?:-raw)?)"
                         r"(?:\s+|=)\"[^\"]*(?:\$\((?!cat <<)|`)")
_HEREDOC = re.compile(r"<<-?[ \t]*['\"]?(\w+)['\"]?\n(.*?)\n[ \t]*\1\b", re.S)
_QUOTED = re.compile(r"""'[^']*'|"[^"]*\"""")
_DATA_FLAG = re.compile(r"""(?:--data(?:-raw|-binary|-urlencode)?|--json|--form|-[dF])(?:\s+|=)('[^']*'|"[^"]*"|\S+)""")

_BYPASS = ("git hook bypass (--no-verify, -n, core.hooksPath or an overridden environment) is not allowed here; "
           "commit or push without it so the Baton hooks scan the change")
_STDIN = "a body read from stdin cannot be scanned; pass --body or a file path"
_SUBSTITUTED = ("text built from a command substitution or backticks in a double-quoted body cannot be "
                "scanned; pass it literally, as an inline heredoc, or as a file")
_HOOKS_MISSING = ("Baton git hooks are missing or point at a baton that no longer exists; "
                  "run `baton install-hooks`")


def forge_facing(command):
    """True when the command names a forge-facing program or publishes with git."""
    return bool(_FORGE.search(command) or _GITPUB.search(command))


def _read(path, base):
    """Return (text, problem) for a message file a command publishes, read relative to `base`."""
    if path == "-":
        return None, _STDIN
    full = os.path.join(base, os.path.expanduser(path))
    if not os.path.isfile(full):
        return None, (f"body file {path} not found, so it cannot be scanned; "
                      "write it in an earlier step")
    if os.path.getsize(full) > _MAX_FILE:
        return None, f"body file {path} is too large to scan"
    with open(full, errors="replace") as handle:
        return handle.read(), None


def _body_files(command, git_only):
    """Paths of body files the command names."""
    paths = []
    for match in _FILE_ARG.finditer(command):
        flag = match.group(1) or match.group(2)
        value = next(g for g in match.groups()[2:] if g is not None)
        if value.startswith("-") and value != "-":
            continue
        if git_only:
            if flag in ("-F", "--file") and not value.startswith("@"):
                paths.append(value)
        elif flag in ("--body-file", "--input"):
            paths.append(value.lstrip("@"))
        elif value.startswith("@"):
            paths.append(value[1:])
        elif flag == "-F":
            if "=@" in value:
                paths.append(value.split("=@", 1)[1])
            elif "=" not in value:
                paths.append(value)
    return [p for p in paths if p != "-"]


def decide(command, cwd, terms):
    stripped = _HEREDOC.sub("<<HD", command)
    forge = [m.group(1) for m in _FORGE.finditer(stripped)]
    git = _GITPUB.search(stripped)
    if not (forge or git):
        return []
    problems = []
    try:
        cfg = config.load(cwd)
    except config.ConfigError as error:
        cfg = None
        problems.append(f"{error}. Fix the brief before publishing.")
    allow = cfg.data["privacy_allow"] if cfg else []

    if git:
        blanked = _QUOTED.sub('""', stripped)
        if (_BYPASS_TEXT.search(blanked) or _SHORT_N.search(blanked)
                or _BYPASS_CFG.search(stripped)):
            problems.append(_BYPASS)
        if cfg is not None and not githooks.installed(cfg.root):
            problems.append(_HOOKS_MISSING)

    http_only = bool(forge) and not git and all(name in _HTTP_CLIS for name in forge)
    stdin_pattern = _STDIN_BODY if forge else _GIT_STDIN
    if stdin_pattern.search(stripped) and "<<" not in stripped and "|" not in stripped:
        problems.append(_STDIN)
    if forge and _SUBST_BODY.search(stripped):
        problems.append(_SUBSTITUTED)

    texts = [m.group(2) for m in _HEREDOC.finditer(command)]
    if http_only:
        texts += [m.group(1) for m in _DATA_FLAG.finditer(stripped)]
    else:
        texts += _QUOTED.findall(stripped)
    for path in _body_files(stripped, git_only=not forge):
        if _UNRESOLVABLE.search(path):
            problems.append(f"body file {path} is named by a variable or pattern, so it cannot be scanned; "
                            "pass a literal path")
            continue
        full = os.path.join(cwd, os.path.expanduser(path))
        if not os.path.isfile(full) and re.search(r">\s*" + re.escape(path), stripped):
            continue  # the same command writes it
        text, problem = _read(path, cwd)
        if problem:
            problems.append(problem)
        else:
            texts.append(text)

    findings = scan.scan_text("\n".join(texts), terms, allow)
    kinds = sorted({finding.kind for finding in findings})
    problems.extend(f"privacy: {kind} in the text this command would publish" for kind in kinds)
    return list(dict.fromkeys(problems))


TIME_LIMIT = 2.0  # seconds one command may take to check


class _TimedOut(Exception):
    pass


def _alarm(signum, frame):
    raise _TimedOut(f"the check took longer than {TIME_LIMIT} seconds")


def _strings(value):
    """Yield every string in nested dict/list JSON (dict keys are not scanned)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def decide_mcp(tool_input, cwd, terms):
    cfg = config.load(cwd)
    if cfg is None:
        return []
    findings = scan.scan_text("\n".join(_strings(tool_input)), terms, cfg.data["privacy_allow"])
    return [f"privacy: {kind} in the tool input" for kind in sorted({f.kind for f in findings})]


MERGE_HINT = "baton merge-pr <N> checks CI and review first; the sprint skill says who merges"
_MERGE_RE = re.compile(r"(?<![\w-])gh\s+(?:-\S+\s+)*pr\s+merge\b")


def _hint(payload):
    """The merge hint, once per session, for an allowed merge command; else None."""
    session = payload.get("session_id")
    if not isinstance(session, str) or not re.fullmatch(r"[\w.-]+", session):
        return None
    command = (payload.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not _MERGE_RE.search(command):
        return None
    marker = os.path.join(tempfile.gettempdir(), f"baton-gate-hint-{session}")
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError:
        return None
    os.close(fd)
    return MERGE_HINT


def main(stdin_text):
    try:
        payload = json.loads(stdin_text)
    except ValueError:
        # A payload that is not JSON is not a Bash tool call Baton can check. Blocking it
        # would break every tool call if Claude Code changed its payload format. An error
        # inside Baton's own checks (below) blocks instead, because there the command was
        # understood and may publish.
        return 0
    if not isinstance(payload, dict):
        return 0
    tool = payload.get("tool_name")
    mcp = isinstance(tool, str) and tool.startswith("mcp__")
    if tool != "Bash" and not mcp:
        return 0
    command = ""
    try:
        if not mcp:
            command = payload.get("tool_input", {}).get("command", "")
        cwd = payload.get("cwd") or os.getcwd()
        previous = signal.signal(signal.SIGALRM, _alarm)
        signal.setitimer(signal.ITIMER_REAL, TIME_LIMIT)
        try:
            if mcp:
                problems = decide_mcp(payload.get("tool_input"), cwd, scan.load_terms(warn=False))
            else:
                problems = decide(command, cwd, scan.load_terms(warn=False))
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
    except Exception as error:
        # Fail closed only when the command could publish; any other command is allowed.
        try:
            triggered = mcp or (forge_facing(command) if isinstance(command, str) else True)
        except Exception:
            triggered = True
        if not triggered:
            return 0
        print(f"Baton publish gate could not check this command ({error!r}).", file=sys.stderr)
        return 2
    if not problems:
        if not mcp:
            try:
                hint = _hint(payload)
            except Exception:
                hint = None
            if hint:
                print(json.dumps({"hookSpecificOutput": {
                    "hookEventName": "PreToolUse", "additionalContext": hint}}))
        return 0
    print("Baton publish gate blocked this command:", file=sys.stderr)
    for problem in problems:
        print(f"- {problem}", file=sys.stderr)
    if any(problem.startswith("privacy:") for problem in problems):
        print("Replace private details with placeholders and retry.", file=sys.stderr)
    return 2
