"""Decide whether a Bash command may publish to the forge.

Runs as a Claude Code PreToolUse hook. The command is split into shell
words, each `gh` and `git` invocation is inspected, and the text it would
publish is scanned. Text the gate cannot see (stdin, command substitution,
variables, missing files) is refused rather than assumed clean. Diffs are
scanned by the git hooks (githooks.py), which this gate requires to be
installed and not bypassed.

This guards against mistakes and nudged agents. It is not a sandbox: a
program that talks to the forge without `gh` or `git` is outside its view.
"""
import json
import os
import re
import shlex
import sys
from dataclasses import dataclass, field

from . import config, githooks, scan

MARKER = "<!-- baton:"
HOOK_TAG = githooks.HOOK_TAG
_MAX_FILE = 1_000_000

_PUNCTUATION = set("();<>|&")
_WRAPPERS = {"env", "command", "sudo", "exec", "time", "nohup", "export"}
_ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*=")
_BAD_ENV = re.compile(r"^(?:GIT_CONFIG\w*|GIT_DIR|GIT_WORK_TREE|BATON_PRIVATE_TERMS|HOME)=")
_URL = re.compile(r"^(?:[\w.+-]+://|[\w.-]+@[\w.-]+:)")
_VARIABLE = re.compile(r"^\$\{?\w+\}?$")
_INLINE_HEREDOC = "$(cat <<"
_HEREDOC = re.compile(r"""^\$\(cat <<-?\s*['"]?(\w+)['"]?\n(.*)\n\1\s*\)\s*$""", re.S)

_GIT_VALUE_OPTS = {"-c", "-C", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
_COMMIT_VALUE_OPTS = {
    "-m", "-F", "-C", "-c", "-t", "--message", "--file", "--author", "--date",
    "--reuse-message", "--reedit-message", "--fixup", "--squash", "--template",
    "--cleanup", "--trailer",
}
_GIT_MESSAGE_OPTS = {"-m", "--message"}
_GIT_FILE_OPTS = {"-F", "--file"}

_GH_PUBLISH = {
    ("issue", "create"), ("issue", "comment"), ("issue", "edit"), ("issue", "close"),
    ("issue", "reopen"), ("pr", "create"), ("pr", "comment"), ("pr", "edit"),
    ("pr", "review"), ("pr", "close"), ("pr", "reopen"), ("release", "create"),
    ("release", "edit"), ("label", "create"), ("label", "edit"), ("repo", "edit"),
    ("repo", "create"), ("gist", "create"), ("gist", "edit"),
}
_GH_COMMENT = {("issue", "comment"), ("pr", "comment"), ("pr", "review")}
_GH_CLOSE = {("issue", "close"), ("issue", "reopen"), ("pr", "close"), ("pr", "reopen")}
_GH_BODY_OPTS = {"--body", "-b"}
_GH_TEXT_OPTS = {"--title", "-t", "--notes", "-n"}
_GH_FILE_OPTS = {"--body-file", "-F", "--notes-file"}
_API_FIELD_OPTS = {"-f", "--raw-field", "-F", "--field"}
_API_TYPED_OPTS = {"-F", "--field"}
_CLOSE_MESSAGE = (
    "closing or reopening directly is not allowed; use `baton close|reopen "
    "<issue|pr> <number> <role> <reason-file>` so a marker comment says who did it and why")
_API_MERGE = re.compile(r"/pulls/\d+/merge\b")
_API_STATE_ENDPOINT = re.compile(r"(?:^|/)(?:issues|pulls)/\d+/?(?:\?|$)")
_API_STATE_KEY = re.compile(r'"state(?:_reason)?"\s*:')
_API_CLOSE_MUTATION = re.compile(r"\b(?:closeIssue|reopenIssue|closePullRequest|reopenPullRequest)\b")
_API_MUTATION = re.compile(r"\bmutation\b")
_API_STATE_ARG = re.compile(r"(?<![$\w])state(?:Reason)?(?:[\s,]|#[^\n\r]*(?:[\r\n]|$))*:")
_API_GRAPHQL = re.compile(r"(?:^|/)graphql/?(?:\?|$)")
_API_COMMENT = re.compile(r"/(?:comments|reviews)(?:/\d+)?(?:/|\?|$)")


@dataclass
class Call:
    program: str
    args: list
    bad_env: bool


@dataclass
class Item:
    """What one gh or git invocation would publish."""
    relevant: bool = False
    git_publish: bool = False
    comment: bool = False
    problems: list = field(default_factory=list)
    texts: list = field(default_factory=list)     # scanned
    values: list = field(default_factory=list)    # must be literal text
    bodies: list = field(default_factory=list)    # must start with the marker
    files: list = field(default_factory=list)     # (path, is_body)
    state_files: dict = field(default_factory=dict)  # path -> "rest" | "graphql"
    dirs: list = field(default_factory=list)


def tokenize(command):
    command = command.replace("\\\n", " ")
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        return command.split()


def _invocations(tokens):
    current = []
    for token in tokens:
        if token and set(token) <= _PUNCTUATION:
            if current:
                yield current
            current = []
        else:
            current.append(token)
    if current:
        yield current


def _call(words):
    bad_env, index = False, 0
    while index < len(words) and (
            _ASSIGNMENT.match(words[index]) or os.path.basename(words[index]) in _WRAPPERS):
        bad_env = bad_env or bool(_BAD_ENV.match(words[index]))
        index += 1
    if index >= len(words):
        return Call("", [], bad_env)
    return Call(os.path.basename(words[index]), words[index + 1:], bad_env)


def _options(args, value_opts):
    """Yield (flag, value) for flags that take a value, and (None, word) otherwise.

    Understands `--flag value`, `--flag=value`, `-f value` and `-fvalue`.
    """
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in value_opts:
            yield arg, args[index + 1] if index + 1 < len(args) else ""
            index += 2
            continue
        if arg.startswith("--") and "=" in arg and arg.split("=", 1)[0] in value_opts:
            flag, value = arg.split("=", 1)
            yield flag, value
        elif len(arg) > 2 and arg[0] == "-" and arg[1] != "-" and arg[:2] in value_opts:
            yield arg[:2], arg[2:]
        else:
            yield None, arg
        index += 1


def _git_parts(args):
    options, index = [], 0
    while index < len(args):
        arg = args[index]
        if arg in _GIT_VALUE_OPTS:
            options.append((arg, args[index + 1] if index + 1 < len(args) else ""))
            index += 2
        elif arg.startswith("-"):
            options.append((arg, ""))
            index += 1
        else:
            return options, arg, args[index + 1:]
    return options, "", []


def _commit_skips_hooks(rest):
    index = 0
    while index < len(rest):
        arg = rest[index]
        if arg == "--":
            return False
        if arg in _COMMIT_VALUE_OPTS:
            index += 2
            continue
        if re.fullmatch(r"-[A-Za-z]+", arg):
            if "n" in arg:
                return True
            if arg[-1] in "mFCct":
                index += 1
        index += 1
    return False


def _inspect_git(call):
    item = Item()
    options, sub, rest = _git_parts(call.args)
    item.dirs = [value for flag, value in options if flag == "-C"]
    sets_hooks_path = any("core.hookspath" in f"{flag}{value}".lower() for flag, value in options)
    bypass = "git hook bypass (--no-verify, -n, core.hooksPath or an overridden environment) is not allowed here"

    if sub == "config" and any(word.lower().startswith("core.hookspath") for word in rest):
        item.relevant = True
        item.problems.append(bypass)
    if sub not in ("commit", "push", "tag"):
        return item

    item.relevant = True
    item.git_publish = sub in ("commit", "push")
    if (sets_hooks_path or call.bad_env
            or any(word.lower().startswith("--no-v") for word in rest)
            or (sub == "commit" and _commit_skips_hooks(rest))):
        item.problems.append(bypass)

    for flag, value in _options(rest, _GIT_MESSAGE_OPTS | _GIT_FILE_OPTS):
        if flag in _GIT_FILE_OPTS and sub != "push":
            item.files.append((value, False))
        elif flag in _GIT_MESSAGE_OPTS:
            item.values.append(value)
            item.texts.append(value)
        elif not _URL.match(value):
            item.texts.append(value)
    return item


def _gh_words(args):
    words, index = [], 0
    while index < len(args) and len(words) < 2:
        arg = args[index]
        if arg in ("-R", "--repo"):
            index += 2
            continue
        if not arg.startswith("-"):
            words.append(arg)
        index += 1
    return (words + ["", ""])[:2]


def _graphql_sets_state(content):
    """True for a close/reopen mutation, or any mutation passing a `state:` argument."""
    return bool(_API_CLOSE_MUTATION.search(content)
                or (_API_MUTATION.search(content) and _API_STATE_ARG.search(content)))


def _inspect_gh_api(call, item):
    has_payload = False
    payload_files = []
    state_endpoint = graphql = state_field = close_mutation = False
    for flag, value in _options(call.args, _API_FIELD_OPTS | {"--input"}):
        if flag is None:
            state_endpoint = state_endpoint or bool(_API_STATE_ENDPOINT.search(value))
            graphql = graphql or bool(_API_GRAPHQL.search(value))
            if _API_MERGE.search(value):
                item.problems.append("merging directly is not allowed; use `baton merge-pr <number>`")
            if _API_COMMENT.search(value):
                item.comment = True
            item.texts.append(value)
            continue
        has_payload = True
        if flag == "--input":
            item.files.append((value, False))
            payload_files.append(value)
            continue
        key, _, content = value.partition("=")
        state_field = state_field or key in ("state", "state_reason")
        close_mutation = close_mutation or _graphql_sets_state(content)
        if flag in _API_TYPED_OPTS and content.startswith("@"):
            item.files.append((content[1:], key == "body"))
            payload_files.append(content[1:])
            continue
        item.values.append(content)
        item.texts.append(value)
        if key == "body":
            item.bodies.append(content)
    item.comment = item.comment and has_payload
    kind = "graphql" if graphql else "rest" if state_endpoint else None
    if kind:
        for path in payload_files:
            item.state_files[path] = kind
    if (state_endpoint and state_field) or (graphql and close_mutation):
        item.problems.append(_CLOSE_MESSAGE)


def _inspect_gh(call):
    item = Item()
    group, action = _gh_words(call.args)
    if (group, action) == ("pr", "merge"):
        item.relevant = True
        item.problems.append("merging directly is not allowed; use `baton merge-pr <number>`")
        return item
    if group == "api":
        item.relevant = True
        _inspect_gh_api(call, item)
        return item
    if (group, action) in _GH_CLOSE:
        item.relevant = True
        item.problems.append(_CLOSE_MESSAGE)
        return item
    if (group, action) not in _GH_PUBLISH:
        return item

    item.relevant = True
    item.comment = (group, action) in _GH_COMMENT
    value_opts = _GH_BODY_OPTS | _GH_TEXT_OPTS | _GH_FILE_OPTS
    for flag, value in _options(call.args, value_opts):
        if flag in _GH_FILE_OPTS:
            item.files.append((value, True))
            continue
        if flag is not None:
            item.values.append(value)
        if flag in _GH_BODY_OPTS:
            item.bodies.append(value)
        if not _URL.match(value):
            item.texts.append(value)
    return item


def _heredoc_text(value):
    """The text of `$(cat <<'EOF' ... EOF)`, or the value itself."""
    match = _HEREDOC.match(value)
    return match.group(2) if match else value


def _unscannable(value):
    if value == "$" or _VARIABLE.match(value):
        return True
    return "$(" in value and not value.startswith(_INLINE_HEREDOC)


def _read(path, bases):
    """Return (text, problem) for a file a command publishes."""
    if path == "-":
        return None, "a body read from stdin cannot be scanned; pass --body or a file path"
    for base in bases:
        full = os.path.join(base, os.path.expanduser(path))
        if os.path.isfile(full):
            if os.path.getsize(full) > _MAX_FILE:
                return None, f"body file {path} is too large to scan"
            with open(full, errors="replace") as handle:
                return handle.read(), None
    return None, (f"body file {path} not found, so it cannot be scanned; "
                  "write it in an earlier step")


def _find_config(dirs):
    for directory in dirs:
        if os.path.isdir(directory):
            cfg = config.load(directory)
            if cfg is not None:
                return cfg
    return None


def decide(command, cwd, terms):
    calls = [_call(words) for words in _invocations(tokenize(command))]
    dirs, items = [cwd], []
    for call in calls:
        if call.program in ("cd", "pushd") and call.args:
            dirs.append(os.path.join(cwd, os.path.expanduser(call.args[0])))
        elif call.program == "git":
            item = _inspect_git(call)
            dirs.extend(os.path.join(cwd, os.path.expanduser(path)) for path in item.dirs)
            items.append(item)
        elif call.program == "gh":
            items.append(_inspect_gh(call))
    items = [item for item in items if item.relevant]
    if not items:
        return []

    try:
        cfg = _find_config(dirs)
    except config.ConfigError as error:
        return [f"{error}. Fix the brief before publishing."]
    if cfg is None:
        return []

    problems, texts = [], []
    bases = dirs + [str(cfg.root)]
    git_publish = any(item.git_publish for item in items)
    if git_publish and any(call.bad_env for call in calls):
        problems.append("git hook bypass (--no-verify, -n, core.hooksPath or an overridden "
                        "environment) is not allowed here")
    for item in items:
        problems.extend(item.problems)
        if any(_unscannable(value) for value in item.values):
            problems.append("text built from a variable or command substitution cannot be "
                            "scanned; pass it literally, as an inline heredoc, or as a file")
        bodies = [_heredoc_text(body) for body in item.bodies]
        for path, is_body in item.files:
            text, problem = _read(path, bases)
            if problem:
                problems.append(problem)
                continue
            texts.append(text)
            if is_body:
                bodies.append(text)
            kind = item.state_files.get(path)
            if kind == "graphql" and _graphql_sets_state(text):
                problems.append(_CLOSE_MESSAGE)
            elif kind == "rest" and _API_STATE_KEY.search(text):
                problems.append(_CLOSE_MESSAGE)
        texts.extend(item.texts)
        if any(not body.strip() for body in bodies):
            problems.append("the body is empty; write the full text first")
        if item.comment and not (bodies and all(
                body.lstrip().startswith(MARKER) for body in bodies)):
            problems.append("agent comments need a body that starts with the Baton marker "
                            "<!-- baton:<role>:<kind> -->")

    if git_publish and not githooks.installed(cfg.root):
        problems.append("Baton git hooks are not installed; run `baton install-hooks`")

    findings = scan.scan_text("\n".join(texts), terms, cfg.data["privacy_allow"])
    kinds = sorted({finding.kind for finding in findings})
    problems.extend(f"privacy: {kind} in the text this command would publish" for kind in kinds)
    return list(dict.fromkeys(problems))


def main(stdin_text):
    try:
        payload = json.loads(stdin_text)
    except ValueError:
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return 0
    try:
        command = payload.get("tool_input", {}).get("command", "")
        cwd = payload.get("cwd") or os.getcwd()
        problems = decide(command, cwd, scan.load_terms())
    except Exception as error:  # fail closed: an unchecked command must not publish
        print(f"Baton publish gate could not check this command ({error!r}).", file=sys.stderr)
        return 2
    if not problems:
        return 0
    print("Baton publish gate blocked this command:", file=sys.stderr)
    for problem in problems:
        print(f"- {problem}", file=sys.stderr)
    print("Replace private details with placeholders and retry.", file=sys.stderr)
    return 2
