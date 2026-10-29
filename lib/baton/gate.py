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
# wrapper -> (short flags without a value, short flags that take a value,
#             long flags without a value, long flags that take a value)
_WRAPPERS = {
    "env": ("i0v", "uCS", {"--ignore-environment", "--null", "--debug"},
            {"--unset", "--chdir", "--split-string"}),
    "command": ("pvV", "", set(), set()),
    "sudo": ("AbEeHKklnPSsVvi", "ugphCrtTUDR", {"--background", "--preserve-env", "--login",
                                                 "--non-interactive", "--shell", "--set-home"},
             {"--user", "--group", "--host", "--prompt", "--close-from", "--role", "--type",
              "--chdir", "--chroot"}),
    "exec": ("cl", "a", set(), set()),
    "time": ("pv", "fo", {"--portability", "--verbose", "--append"}, {"--format", "--output"}),
    "nohup": ("", "", set(), set()),
    "export": ("nfp", "", set(), set()),
    "declare": ("xrfFgpaAilunt", "", set(), set()),
    "typeset": ("xrfFgpaAilunt", "", set(), set()),
    "readonly": ("afpA", "", set(), set()),
    "local": ("xrafFgpAilunt", "", set(), set()),
}
_ASSIGNMENT = re.compile(r"^([A-Za-z_]\w*)\+?=")
_SAFE_GIT_ENV = {"GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE",
                 "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_DATE"}
_BAD_ENV_NAMES = {"HOME", "XDG_CONFIG_HOME", "BATON_PRIVATE_TERMS"}
_URL = re.compile(r"^(?:[\w.+-]+://|[\w.-]+@[\w.-]+:)")
_VARIABLE = re.compile(r"^\$\{?\w+\}?$")
_INLINE_HEREDOC = "$(cat <<"
_HEREDOC = re.compile(r"""^\$\(cat <<-?\s*['"]?(\w+)['"]?\n(.*)\n\1\s*\)\s*$""", re.S)

_GIT_VALUE_OPTS = {"-c", "-C", "--git-dir", "--work-tree", "--namespace", "--exec-path",
                   "--config-env"}
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
_EVALUATORS = {"bash", "sh", "zsh", "dash", "eval", "xargs", "source", ".", "node", "perl", "ruby"}
_SHELLS = {"sh", "bash", "zsh", "dash"}
_GH_OR_GIT = re.compile(r"(?<![\w.\-])(?:gh|git)(?![\w.])")
_KEYWORDS = {"if", "then", "else", "elif", "while", "until", "do", "!", "{"}
_DIRECTORY_COMMANDS = ("cd", "pushd", "popd")
_PLAIN_GIT_OPTS = {"--git-dir", "--work-tree", "--exec-path", "--namespace", "--config-env"}
_API_VALUE_OPTS = {"--jq", "-q", "-H", "--header", "--hostname", "--cache", "--template", "-t"}
_API_PLAIN_OPTS = {"--paginate", "--slurp", "--silent", "--include", "-i", "--verbose"}
# Programs that only read or write data and never start another program from their
# arguments; any other program whose arguments mention gh or git is a possible launcher.
_DATA_PROGRAMS = {
    "baton", "echo", "printf", "cat", "rg", "grep", "egrep", "fgrep", "ls", "head", "tail",
    "wc", "sort", "uniq", "cut", "tr", "diff", "cmp", "tee", "basename", "dirname", "test",
    "[", "true", "false", "stat", "file", "du", "df", "mkdir", "rm", "cp", "mv", "touch",
    "ln", "chmod", "readlink", "realpath", "pwd", "cd", "pushd", "popd", "which", "type",
    "export", "declare", "typeset", "readonly", "local", "unset", "set", "exit", "return",
    "jq", "column", "nl", "fold", "fmt", "expand", "unexpand", "rev",
    "tac", "paste", "join", "comm", "od", "xxd", "hexdump", "strings", "md5sum", "sha1sum",
    "sha256sum", "cksum", "seq", "date", "sleep", "yes", "expr", "tsort", "shuf", "pr",
}
_FIND_RUNNERS = {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprintf", "-fls"}
_SED_RUNS = re.compile(
    r"(?:^|[;{}\n/\d$!\s])e(?:\s|$|;|\})"
    r"|s(.)(?:\\.|(?!\1).)*\1(?:\\.|(?!\1).)*\1[gpiImM0-9w]*e")
_AWKS = {"awk", "gawk", "mawk"}
_CHECKED = {"sed", "find"} | _AWKS
_EXPORTERS = {"export", "declare", "typeset", "readonly", "local"}
_NAME = re.compile(r"^([A-Za-z_]\w*)(?:\[[^\]]*\])?(?:\+?=.*)?$", re.S)
_ALIAS = "aliases and --config-env can run gh or git behind the gate; write the git command out in full"
_BYPASS = ("git hook bypass (--no-verify, -n, core.hooksPath or an overridden environment) is not allowed here; "
           "commit or push without it so the Baton hooks scan the change")
_NOT_PLAIN = ("git publishing must be a plain command: run it from the repository's directory, "
              "without cd, pushd or -C in the same command")
_WRAPPER_MESSAGE = ("cannot tell whether this runs gh or git; run the gh or git command directly, "
                    "as a command of its own, so the gate can check it")
_NO_BRIEF = ("no Baton brief found from the working directory, so this command cannot be checked; "
             "run it from the repository's directory")
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
    unknown_option: bool = False
    stdin: str = ""
    expands: bool = False
    piped: bool = False


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


_REDIRECTS = {">", ">>", "<", "<<", "<<<", ">&", "<&", "&>", "&>>", ">|", "<>"}
_HEREDOC_START = re.compile(r"<<(-?)[ \t]*(?:'([^'\n]*)'|\"([^\"\n]*)\"|([^\s;&|()<>'\"]+))")


def _split_heredocs(command):
    """Cut heredocs that start outside quotes and outside $(...) out of `command`.

    Each operator and delimiter becomes `<<` and a placeholder word; the map goes from
    placeholder to body. Heredocs inside quoted values are left in place.
    """
    out, bodies, pending, expanding = [], {}, [], set()
    index, quote, depth, length = 0, "", 0, len(command)
    while index < length:
        char = command[index]
        if quote == "'":
            if char == "'":
                quote = ""
        elif char == "\\" and index + 1 < length:
            out.append(char + command[index + 1])
            index += 2
            continue
        elif quote == '"':
            if char == '"':
                quote = ""
            elif command.startswith("$(", index):
                depth += 1
                out.append("$(")
                index += 2
                continue
            elif char == ")" and depth:
                depth -= 1
        elif char in "'\"":
            quote = char
        elif command.startswith("$(", index):
            depth += 1
        elif char == ")" and depth:
            depth -= 1
        elif char == "<" and command.startswith("<<", index) and not depth \
                and not command.startswith("<<<", index):
            match = _HEREDOC_START.match(command, index)
            if match:
                word = match.group(2) or match.group(3) or match.group(4) or ""
                name = f"__baton_heredoc_{len(bodies) + len(pending)}__"
                pending.append((name, word, bool(match.group(1))))
                if match.group(4) is not None:
                    expanding.add(name)
                out.append(f"<< {name}")
                index = match.end()
                continue
        elif char == "\n" and pending and not depth:
            out.append(char)
            index += 1
            for name, word, strip in pending:
                lines = []
                while index < length:
                    end = command.find("\n", index)
                    end = length if end < 0 else end
                    line = command[index:end]
                    index = min(end + 1, length)
                    if (line.lstrip("\t") if strip else line) == word:
                        break
                    lines.append(line)
                bodies[name] = "\n".join(lines)
            pending = []
            continue
        out.append(char)
        index += 1
    for name, _, _ in pending:   # no newline followed: an empty body
        bodies[name] = ""
    return "".join(out), bodies, expanding


def _invocations(tokens, heredocs=None, expanding=()):
    """Yield (words, stdin, expands, piped) per invocation; redirections and their targets
    are dropped. `expands` marks a heredoc whose delimiter was unquoted; `piped` marks a
    command whose output goes into the next one."""
    heredocs = heredocs or {}
    current, stdin, skip, here, expands = [], "", False, False, False
    for token in tokens:
        if token and set(token) <= _PUNCTUATION:
            if token in _REDIRECTS:
                if current and current[-1].isdigit():
                    current.pop()
                skip, here = True, token in ("<<", "<<<")
                continue
            skip = False
            if current:
                yield current, stdin, expands, token in ("|", "|&")
            current, stdin, expands = [], "", False
        elif skip:
            skip = False
            if here:
                stdin = heredocs.get(token, token)
                expands = token in expanding
        else:
            current.append(token)
    if current:
        yield current, stdin, expands, False


def _bad_name(name):
    return name in _BAD_ENV_NAMES or (name.startswith("GIT_") and name not in _SAFE_GIT_ENV)


def _bad_env(word):
    match = _ASSIGNMENT.match(word)
    return bool(match) and _bad_name(match.group(1))


def _bad_export(words):
    """True if an export-like command names a bad variable, bare, with a subscript or assigned."""
    for word in words:
        match = _NAME.match(word)
        if match and _bad_name(match.group(1)):
            return True
    return False


def _runs_programs(call):
    """True if a sed script or find expression can start a program."""
    if call.program == "find":
        return any(arg in _FIND_RUNNERS for arg in call.args)
    if call.program in _AWKS:
        return any("system" in arg or "|" in arg for arg in call.args)
    if call.program == "sed":
        return any(_SED_RUNS.search(arg) for arg in call.args if not arg.startswith("--"))
    return False


def _skip_options(words, index, wrapper):
    """Skip a wrapper's options (and their values); return (index, all options known)."""
    short_plain, short_value, long_plain, long_value = _WRAPPERS[wrapper]
    known = True
    while index < len(words) and words[index].startswith("-") and len(words[index]) > 1:
        word = words[index]
        index += 1
        if word == "--":
            break
        if word.startswith("--"):
            name = word.split("=", 1)[0]
            if name in long_value:
                if wrapper == "env" and name == "--split-string":
                    value = word.split("=", 1)[1] if "=" in word else (
                        words[index] if index < len(words) else "")
                    if _GH_OR_GIT.search(value):
                        known = False
                if "=" not in word:
                    index += 1
            elif name not in long_plain:
                known = False
            continue
        for position, char in enumerate(word[1:], 1):
            if char in short_value:
                if wrapper == "env" and char == "S":
                    value = word[position + 1:] or (words[index] if index < len(words) else "")
                    if _GH_OR_GIT.search(value):
                        known = False
                if position == len(word) - 1:
                    index += 1
                break
            if char not in short_plain:
                known = False
                break
    return index, known


def _split_hit(words, start, end):
    """True if an env -S / --split-string value in words[start:end] mentions gh or git."""
    for position in range(start, end):
        word = words[position]
        if word.startswith("--split-string"):
            value = word.split("=", 1)[1] if "=" in word else (
                words[position + 1] if position + 1 < end else "")
        elif word.startswith("-") and not word.startswith("--") and "S" in word:
            tail = word[word.index("S") + 1:]
            value = tail or (words[position + 1] if position + 1 < end else "")
        else:
            continue
        if _GH_OR_GIT.search(value):
            return True
    return False


def _call(words, stdin=""):
    bad_env, unknown, split_hit, index = False, False, False, 0
    while index < len(words):
        word = words[index]
        wrapper = os.path.basename(word)
        if word in _KEYWORDS:
            index += 1
        elif _ASSIGNMENT.match(word):
            bad_env = bad_env or _bad_env(word)
            index += 1
        elif wrapper in _WRAPPERS:
            start = index + 1
            if wrapper in _EXPORTERS and _bad_export(words[start:]):
                bad_env = True
            index, known = _skip_options(words, start, wrapper)
            unknown = unknown or not known
            if wrapper == "env" and not known:
                split_hit = split_hit or _split_hit(words, start, index)
        else:
            break
    unknown = unknown and (split_hit or any(_GH_OR_GIT.search(w) for w in words[index:]))
    if index >= len(words):
        return Call("", [], bad_env, unknown, stdin)
    return Call(os.path.basename(words[index]), words[index + 1:], bad_env, unknown, stdin)


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
    if (any(f"{flag}{value}".lower().lstrip("-c").startswith("alias.")
            or flag.split("=", 1)[0] == "--config-env" for flag, value in options)
            or (sub == "config" and any(w.lower().lstrip("-").startswith("alias.") for w in rest))):
        item.relevant = True
        item.problems.append(_ALIAS)
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
    """Why a `gh api` call is refused, or None when it is a plain read of one endpoint."""
    endpoints, index, seen_api = 0, 0, False
    while index < len(args):
        arg = args[index]
        index += 1
        if not arg.startswith("-") or arg == "-":
            if not seen_api:
                seen_api = True
            else:
                endpoints += 1
            continue
        name = arg.split("=", 1)[0]
        takes_value = name in _METHOD_OPTS | _API_VALUE_OPTS and (arg.startswith("--") or name == arg)
        if takes_value:
            if "=" in arg and arg.startswith("--"):
                value = arg.split("=", 1)[1]
            else:
                value = args[index] if index < len(args) else ""
                index += 1
            if name in _METHOD_OPTS and value.upper() != "GET":
                return f"`gh api` with method {value.upper() or '(none)'} writes to the forge"
        elif arg not in _API_PLAIN_OPTS:
            return f"`gh api` option {name} is not a plain read"
    if endpoints != 1:
        return "`gh api` must name exactly one endpoint"
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


def _named_directories(calls):
    """Directories a command names: cd and pushd arguments, and git -C values."""
    names = []
    for call in calls:
        args = call.args
        if call.program == "builtin" and args and args[0] in _DIRECTORY_COMMANDS:
            args = args[1:]
        elif call.program not in _DIRECTORY_COMMANDS and call.program != "git":
            continue
        if call.program == "git":
            options, _, _ = _git_parts(args)
            for flag, value in options:
                if flag == "-C":
                    names.append(value)
                elif flag.startswith("-C") and not flag.startswith("--"):
                    names.append(flag[2:])
        else:
            operands = [a for a in args if a != "--" and not (a.startswith("-") and len(a) > 1)]
            if operands:
                names.append(operands[0])
    return names


def _literal(name):
    if re.search(r"[$`*?\[]", name):
        return False
    return not (name.startswith("~") and name != "~" and not name.startswith("~/"))


def _has_brief(path):
    try:
        return config.load(path) is not None
    except config.ConfigError:
        return True
    except OSError:
        return False


def _reaches_shell(calls, number):
    """True when the call is a shell, or pipes into one later in its pipeline."""
    while True:
        if calls[number].program in _SHELLS:
            return True
        if not calls[number].piped or number + 1 >= len(calls):
            return False
        number += 1


def _substitutions(text):
    """Contents of `$(...)` and backtick substitutions in the body of an unquoted heredoc."""
    found, index = [], 0
    while index < len(text):
        if text[index] == "\\":
            index += 2
        elif text.startswith("$(", index):
            depth, end = 1, index + 2
            while end < len(text) and depth:
                depth += {"(": 1, ")": -1}.get(text[end], 0)
                end += 1
            found.append(text[index + 2:end - 1 if depth == 0 else end])
            index = end
        elif text[index] == "`":
            end = text.find("`", index + 1)
            end = len(text) if end < 0 else end
            found.append(text[index + 1:end])
            index = end + 1
        else:
            index += 1
    return found


def decide(command, cwd, terms):
    stripped, heredocs, expanding = _split_heredocs(command)
    calls = []
    for words, stdin, expands, piped in _invocations(tokenize(stripped), heredocs, expanding):
        call = _call(words, stdin)
        call.expands, call.piped = expands, piped
        calls.append(call)
    named = _named_directories(calls)
    if not _has_brief(cwd):
        literal = [os.path.join(cwd, os.path.expanduser(name)) for name in named
                   if _literal(name)]
        if not any(_has_brief(path) for path in literal):
            return []
    problems, items = [], []
    moves = any(_changes_directory(call) for call in calls)
    for number, call in enumerate(calls):
        if call.unknown_option:
            problems.append(_WRAPPER_MESSAGE)
        if call.stdin and _reaches_shell(calls, number):
            problems.extend(decide(call.stdin, cwd, terms))
        elif call.stdin and call.expands:
            for inner in _substitutions(call.stdin):
                problems.extend(decide(inner, cwd, terms))
        if _is_evaluator(call.program):
            if any(_GH_OR_GIT.search(arg) for arg in call.args):
                problems.append(_WRAPPER_MESSAGE)
        elif call.program == "gh":
            problems.extend(_gh_problems(call))
        elif call.program == "git":
            item = _inspect_git(call)
            if item.relevant:
                items.append(item)
        elif (call.program and (call.program not in _DATA_PROGRAMS)
                and call.program not in _CHECKED
                and any(_GH_OR_GIT.search(arg) for arg in call.args)):
            problems.append(_WRAPPER_MESSAGE)
        elif (call.program in _CHECKED and _runs_programs(call)
                and any(_GH_OR_GIT.search(arg) for arg in call.args)):
            problems.append(_WRAPPER_MESSAGE)
    for item in items:
        problems.extend(item.problems)
    publishing = [item for item in items if item.publish]
    if not publishing:
        return list(dict.fromkeys(problems))

    if _has_brief(cwd) and any(not _literal(name) for name in named):
        problems.append("a directory named by a command that commits, pushes or tags "
                        "cannot be resolved (variable, substitution, glob or ~user); "
                        "name it literally")
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
        problems.append("Baton git hooks are missing or point at a baton that no longer exists; "
            "run `baton install-hooks`")

    findings = scan.scan_text("\n".join(texts), terms, cfg.data["privacy_allow"])
    kinds = sorted({finding.kind for finding in findings})
    problems.extend(f"privacy: {kind} in the text this command would publish" for kind in kinds)
    return list(dict.fromkeys(problems))


def main(stdin_text):
    try:
        payload = json.loads(stdin_text)
    except ValueError:
        # A payload that is not JSON is not a Bash tool call Baton can check. Blocking it
        # would break every tool call if Claude Code changed its payload format. An error
        # inside Baton's own checks (below) blocks instead, because there the command was
        # understood and may publish.
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return 0
    try:
        command = payload.get("tool_input", {}).get("command", "")
        cwd = payload.get("cwd") or os.getcwd()
        problems = decide(command, cwd, scan.load_terms(warn=False))
    except Exception as error:  # fail closed: an unchecked command must not publish
        print(f"Baton publish gate could not check this command ({error!r}).", file=sys.stderr)
        return 2
    if not problems:
        return 0
    print("Baton publish gate blocked this command:", file=sys.stderr)
    for problem in problems:
        print(f"- {problem}", file=sys.stderr)
    if any(problem.startswith("privacy:") for problem in problems):
        print("Replace private details with placeholders and retry.", file=sys.stderr)
    return 2
