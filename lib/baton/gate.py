"""Decide whether a Bash command may publish to the forge.

Runs as a Claude Code PreToolUse hook. The command is split into shell
words and each `gh` and `git` invocation is inspected. It predicts nothing
about the shell: every `gh` write is refused wherever it runs, and the
`baton` commands (which scan their own text) do the writing. `git commit`,
`push` and `tag` must be plain commands in the repository's own directory,
and their messages are scanned. Text the gate cannot see (stdin, command
substitution, variables, missing files) is refused rather than assumed
clean. Diffs are scanned by the git hooks (githooks.py), which this gate
requires to be installed and not bypassed.

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

_GH_READ_ACTIONS = {"view", "list", "status", "checks", "diff", "download", "checkout",
                    "search", "browse", "watch"}
_GH_READ_GROUPS = {"", "status", "search", "help"}
_METHOD_OPTS = {"-X", "--method"}
_API_BODY_OPTS = {"-f", "-F", "--raw-field", "--field", "--input"}
_MUTATION = re.compile(r"mutation", re.I)
_EVALUATORS = {"bash", "sh", "zsh", "dash", "eval", "xargs", "source", ".", "node", "perl", "ruby"}
_GH_OR_GIT = re.compile(r"\b(?:gh|git)\b")
_DIRECTORY_COMMANDS = ("cd", "pushd", "popd")
_PLAIN_GIT_OPTS = {"--git-dir", "--work-tree", "--exec-path", "--namespace"}
_BYPASS = "git hook bypass (--no-verify, -n, core.hooksPath or an overridden environment) is not allowed here"
_NOT_PLAIN = "git publishing must be a plain command in the repository's own directory"
_WRAPPER_MESSAGE = "cannot tell whether this writes to the forge"
_NO_BRIEF = "no Baton brief found from the working directory; cannot check this command"
_NO_COMMAND = "no baton command does this; ask the maintainer"
_USE = {
    ("issue", "comment"): "baton comment", ("pr", "comment"): "baton comment",
    ("issue", "create"): "baton issue create", ("issue", "edit"): "baton issue edit",
    ("pr", "create"): "baton pr create", ("pr", "edit"): "baton pr edit",
    ("pr", "ready"): "baton pr ready", ("pr", "review"): "baton review post",
    ("issue", "close"): "baton close|reopen <issue|pr> <number> <role> <reason-file>",
    ("issue", "reopen"): "baton close|reopen <issue|pr> <number> <role> <reason-file>",
    ("pr", "close"): "baton close|reopen <issue|pr> <number> <role> <reason-file>",
    ("pr", "reopen"): "baton close|reopen <issue|pr> <number> <role> <reason-file>",
    ("pr", "merge"): "baton merge-pr <number>", ("label", "create"): "baton labels",
    ("label", "edit"): "baton labels", ("label", "delete"): "baton labels",
}


@dataclass
class Call:
    program: str
    args: list
    bad_env: bool


@dataclass
class Item:
    """What one git invocation would publish."""
    relevant: bool = False
    publish: bool = False     # commit, push or tag
    hooked: bool = False      # commit or push: the hooks must be installed
    plain: bool = True
    problems: list = field(default_factory=list)
    texts: list = field(default_factory=list)     # scanned
    values: list = field(default_factory=list)    # must be literal text
    files: list = field(default_factory=list)     # message files


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
        while index < len(words) and words[index].startswith("-") and len(words[index]) > 1:
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
    sets_hooks_path = any("core.hookspath" in f"{flag}{value}".lower() for flag, value in options)

    if sub == "config" and any(word.lower().startswith("core.hookspath") for word in rest):
        item.relevant = True
        item.problems.append(_BYPASS)
    if sub not in ("commit", "push", "tag"):
        return item

    item.relevant = item.publish = True
    item.hooked = sub in ("commit", "push")
    item.plain = not any(
        flag.split("=", 1)[0] in _PLAIN_GIT_OPTS
        or (not flag.startswith("--") and flag[:2] in ("-C", "-c"))
        for flag, _ in options)
    if (sets_hooks_path or call.bad_env
            or any(word.lower().startswith("--no-v") for word in rest)
            or (sub == "commit" and _commit_skips_hooks(rest))):
        item.problems.append(_BYPASS)

    for flag, value in _options(rest, _GIT_MESSAGE_OPTS | _GIT_FILE_OPTS):
        if flag in _GIT_FILE_OPTS and sub != "push":
            item.files.append(value)
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


def _api_problem(args):
    """Why a `gh api` call is refused, or None when it is a plain read."""
    for flag, value in _options(args, _METHOD_OPTS | _API_BODY_OPTS):
        if flag in _METHOD_OPTS and value.upper() != "GET":
            return f"`gh api` with method {value.upper() or '(none)'} writes to the forge"
        if flag in _API_BODY_OPTS:
            return "`gh api` with fields or an input file writes to the forge"
    if any(_MUTATION.search(arg) for arg in args):
        return "`gh api` with a GraphQL mutation writes to the forge"
    return None


def _gh_problems(call):
    group, action = _gh_words(call.args)
    if group == "api":
        problem = _api_problem(call.args)
        if problem is None:
            return []
        return [f"{problem}; use the fitting baton command (`baton comment`, "
                "`baton issue create|edit`, `baton pr create|edit|ready`, "
                "`baton milestone create|edit`, `baton review post`, `baton close|reopen`, "
                "`baton merge-pr`, `baton labels`, `baton set-status`); otherwise "
                + _NO_COMMAND]
    if (action in _GH_READ_ACTIONS and group) or group in _GH_READ_GROUPS:
        return []
    command = _USE.get((group, action))
    what = f"gh {group} {action}".strip()
    if command is None:
        return [f"`{what}` is not allowed here; {_NO_COMMAND}"]
    return [f"`{what}` is not allowed here; use `{command}`"]


def _heredoc_text(value):
    """The text of `$(cat <<'EOF' ... EOF)`, or the value itself."""
    match = _HEREDOC.match(value)
    return match.group(2) if match else value


def _unscannable(value):
    if value == "$" or _VARIABLE.match(value):
        return True
    return "$(" in value and not value.startswith(_INLINE_HEREDOC)


def _read(path, base):
    """Return (text, problem) for a message file a git command publishes, read relative to `base`."""
    if path == "-":
        return None, "a body read from stdin cannot be scanned; pass --body or a file path"
    full = os.path.join(base, os.path.expanduser(path))
    if not os.path.isfile(full):
        return None, (f"body file {path} not found, so it cannot be scanned; "
                      "write it in an earlier step")
    if os.path.getsize(full) > _MAX_FILE:
        return None, f"body file {path} is too large to scan"
    with open(full, errors="replace") as handle:
        return handle.read(), None


def _changes_directory(call):
    if call.program in _DIRECTORY_COMMANDS:
        return True
    return call.program == "builtin" and bool(call.args) and call.args[0] in _DIRECTORY_COMMANDS


def _is_evaluator(program):
    return program in _EVALUATORS or program.startswith("python")


def decide(command, cwd, terms):
    calls = [_call(words) for words in _invocations(tokenize(command))]
    problems, items = [], []
    moves = any(_changes_directory(call) for call in calls)
    for call in calls:
        if _is_evaluator(call.program):
            if any(_GH_OR_GIT.search(arg) for arg in call.args):
                problems.append(_WRAPPER_MESSAGE)
        elif call.program == "gh":
            problems.extend(_gh_problems(call))
        elif call.program == "git":
            item = _inspect_git(call)
            if item.relevant:
                items.append(item)
    for item in items:
        problems.extend(item.problems)
    publishing = [item for item in items if item.publish]
    if not publishing:
        return list(dict.fromkeys(problems))

    if moves or not all(item.plain for item in publishing):
        problems.append(_NOT_PLAIN)
    if any(call.bad_env for call in calls):
        problems.append(_BYPASS)
    try:
        cfg = config.load(cwd)
    except config.ConfigError as error:
        problems.append(f"{error}. Fix the brief before publishing.")
        return list(dict.fromkeys(problems))
    if cfg is None:
        problems.append(_NO_BRIEF)
        return list(dict.fromkeys(problems))

    texts = []
    for item in publishing:
        if any(_unscannable(value) for value in item.values):
            problems.append("text built from a variable or command substitution cannot be "
                            "scanned; pass it literally, as an inline heredoc, or as a file")
        for path in item.files:
            text, problem = _read(path, cwd)
            if problem:
                problems.append(problem)
            else:
                texts.append(text)
        texts.extend(_heredoc_text(text) for text in item.texts)
    if any(item.hooked for item in publishing) and not githooks.installed(cfg.root):
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
