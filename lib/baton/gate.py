"""Decide whether a Bash command or an MCP tool call may publish to the forge.

Runs as a Claude Code PreToolUse hook. The gate is a text scanner: it does not
parse shell and does not know which `gh` subcommand writes. For a command that
names a forge-facing program (`FORGE_CLIS`) or publishes with git (`commit`,
`push`, `tag`), it scans every quoted string, heredoc body and body file the
command would send, and the unquoted value of a title, body, message, field or
description flag. It refuses only for four safety reasons: a privacy finding
in that text, text it cannot read (a stdin body without a heredoc, even when
piped, and `--description -`; a missing body file; a variable such as `$B` or
`${B}`, or command substitution, in the value of such a flag), a git hook
bypass, and missing hooks. The refusal says to pass the text literally, as an
inline heredoc, or as a file.
A body file is read relative to the last literal `cd` before it in the same
command (else the hook's working directory). A double-quoted body whose only
substitutions are `$(cat <literal path>)` is scanned by reading those files;
any other substitution or backticks, or a `cd` to a non-literal target, is
still refused as unreadable text.
Diffs are scanned by the git hooks (githooks.py), which the gate requires to be
installed and not bypassed.
In a Baton repository (a brief exists) it also refuses an empty or whitespace-only
`--body` / `-b` value or `--body-file` / `-F` file on `gh issue|pr create|edit`.

In a Baton repository a raw `gh issue|pr close|reopen` also needs `--comment`.
From Claude Code 2.1.290 the gate adds the `baton:<role>:closed` (or
`reopened`) marker to the comment through a rewrite (`updatedInput`) that keeps
the permission prompt; otherwise, or for a compound command, it refuses and
names the marker line to add.

The gate also completes one command: a `gh pr create` from a detached HEAD gets
`--head <branch>` added (the one pushed issue branch at HEAD) through the same
rewrite; it never changes a command otherwise.

This guards against mistakes and nudged agents. It is not a sandbox: a script
that talks to the forge without one of these programs is outside its view.
"""
import collections
import functools
import json
import os
import re
import shlex
import signal
import sys
import tempfile

from . import branches, config, githooks, identity, run, scan

MARKER = "<!-- baton:"
HOOK_TAG = githooks.HOOK_TAG
_MAX_FILE = 1_000_000

FORGE_CLIS = tuple(config.DEFAULTS["forge_clis"])
BODY_FILE_FLAGS = tuple(config.DEFAULTS["body_file_flags"])
_HTTP_CLIS = ("curl", "wget", "http", "https", "xh")

# Command position: start of text, after a separator, quote or newline, or after a launcher.
_POS = (r"""(?:^|[;&|(`'"\n{]|\bsudo\b|\benv\b|\bxargs\b|\bexec\b|\bcommand\b|\btime\b|\bnohup\b)"""
        r"""\s*(?:[A-Za-z_]\w*=\S*\s+)*""")


@functools.lru_cache(maxsize=16)
def _forge_re(clis):
    names = "|".join(re.escape(name) for name in sorted(clis, key=len, reverse=True)) or "(?!)"
    return re.compile(_POS + r"(?:[\w.~+-]*/)*(" + names + r")(?![\w.-])")


_GITPUB = re.compile(_POS + r"(?:[\w.~+-]*/)*git\b[^;&|\n]*?\b(?:commit|push|tag)\b")
# Run on the command with quoted dash words unquoted (`"--no-verify"` is the flag), then quoted
# strings blanked, so a commit message may mention these.
_QUOTED_OPT = re.compile(r"""(?<!-m )(?<!--message )(?<![^\s(;&|])(["'])(-[^\s"']*)\1(?=[\s;&|)`]|$)""")
_BYPASS_TEXT = re.compile(
    r"--no-v[a-z-]*|--config-env\b"
    r"|(?:^|[\s;&|])(?:HOME|XDG_CONFIG_HOME|BATON_PRIVATE_TERMS|GIT_CONFIG\w*)=")
# Run on the unblanked command: a quoted `-c "core.hooksPath=/x"` value must still be refused.
_BYPASS_CFG = re.compile(
    r"""-c\s*["']?core\.hooksPath|config\s+(?:(?:--\S+|set)\s+)*["']?core\.hooksPath["']?\s+\S""", re.IGNORECASE)
_SHORT_N = re.compile(_POS + r"git\b[^;&|\n]*?\b(?:commit|push)\b[^;&|\n]*?\s-[a-zA-Z]*n[a-zA-Z]*(?:\s|$)")


@functools.lru_cache(maxsize=16)
def _stdin_re(flags):
    names = "|".join([re.escape(f) for f in sorted(flags, key=len, reverse=True)]
                     + [r"--data(?:-binary|-raw)?", "-d", "--description"])
    return re.compile(r"(?:" + names + r")(?:\s+|=)(?:-|@-)(?:\s|$)")


_GIT_STDIN = re.compile(r"(?:--file|-F)(?:\s+|=)-(?:\s|$)")


@functools.lru_cache(maxsize=16)
def _file_arg_re(flags):
    names = {f for f in flags} | {"--file", "-F"}
    longs = sorted((re.escape(f) for f in names if f.startswith("--")), key=len, reverse=True)
    shorts = sorted((re.escape(f) for f in names if not f.startswith("--")), key=len, reverse=True)
    longs.append(r"--data(?:-binary|-raw)?")
    shorts.append("-d")
    return re.compile(
        r"""(?<![\w-])(?:(""" + "|".join(longs) + r""")(?:\s+|=)|(""" + "|".join(shorts)
        + r""")(?:\s+|=|(?=\S)))"""
        r"""(?:"([^"]*)"|'([^']*)'|([^\s'"<>|;&]+))""")


_UNRESOLVABLE = re.compile(r"[$`*?]")
_SUBST_BODY = re.compile(r"(?:--body|-b|--title|-t|--message|-m|--raw-field|-f|--field|-d|--data(?:-raw)?)"
                         r"(?:\s+|=)\"[^\"]*(?:\$\((?!cat <<)|`)")
_BODY_VALUE = re.compile(
    r"""(?<![\w-])(?:--body|-b|--title|-t|--message|-m|--raw-field|-f|--field|-F|--description|-d"""
    r"""|--data(?:-raw)?)(?:\s+|=)(?:"([^"]*)"|'([^']*)'|([^\s'"<>|;&]+))""")
_VARIABLE = re.compile(r"\$[A-Za-z_{]")
_CAT_SUBST = re.compile(r"""\$\(cat\s+(?:'([^\s'$`]+)'|([^\s'"$`()<>|;&*?\\-][^\s'"$`()<>|;&*?\\]*))\s*\)""")
_HEREDOC = re.compile(r"<<-?[ \t]*['\"]?(\w+)['\"]?([^\n]*)\n(.*?)\n[ \t]*\1\b", re.S)
_QUOTED = re.compile(r"""'[^']*'|"[^"]*\"""")
_DATA_FLAG = re.compile(r"""(?:--data(?:-raw|-binary|-urlencode)?|--json|--form|-[dF])(?:\s+|=)('[^']*'|"[^"]*"|\S+)""")

_CLOSE_RE = re.compile(r"(?<![\w-])gh\s+(?:(?:-R|--repo)\s+\S+\s+|-\S+\s+)*(issue|pr)\s+(close|reopen)\b")
_CLOSE_REASON = ("`gh issue|pr close|reopen` needs a reason in a Baton repository: add --comment \"<reason>\"; "
                 "Baton adds its marker to the comment")
_HAS_REASON = re.compile(r"(?<![\w-])(?:-c|--comment)(?=[\s=]|$)|(?<![\w-])-c\S")
_HELP = re.compile(r"(?<![\w-])(?:--help|-h)(?![\w-])")

REWRITE_FLOOR = (2, 1, 290)
_VERSION_RE = re.compile(r"claude-code_(\d+)-(\d+)-(\d+)")
_ROLE_RE = re.compile(r"baton-(developer|refiner|reviewer|researcher)")
_ROLE_NAMES = {"developer": "developer", "refiner": "refine", "reviewer": "reviewer", "researcher": "researcher"}


def _claude_version(env):
    """The Claude Code version from AI_AGENT, or None when it is missing or not Claude Code's."""
    match = _VERSION_RE.search(env.get("AI_AGENT") or "")
    return tuple(int(part) for part in match.groups()) if match else None


def _role(agent_type):
    """The comment role for a hook payload's agent_type; everything else is the orchestrator."""
    match = _ROLE_RE.search(agent_type or "")
    return _ROLE_NAMES[match.group(1)] if match else "orchestrator"


def _stamp(role, kind, reason):
    """What comment.compose(role, kind, reason) returns (comment imports gate, so it is rebuilt here)."""
    return f"{MARKER}{role}:{kind} -->\n**Baton {role}: {kind}**\n\n{reason.strip()}\n"


def _simple_argv(command):
    """The argv of a single plain command, or None for anything shlex cannot take apart safely."""
    if re.search(r"[$`\n]", command):
        return None
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        if any(token and set(token) <= set("();<>|&") for token in lexer):
            return None
        return shlex.split(command)
    except ValueError:
        return None


def _close_command(stripped):
    """(target, action, has_reason, count) for the gh issue|pr close|reopen commands, else None.

    Every close or reopen is judged on its own segment (cut at the next ; & | or newline); a segment
    with --help is skipped. target and action are the first judged one's; has_reason is False when any
    judged segment lacks a reason; count is how many were judged.
    """
    blanked = _QUOTED.sub('""', stripped)
    judged = []
    for match in _CLOSE_RE.finditer(blanked):
        segment = re.split(r"[;&|\n]", blanked[match.start():], maxsplit=1)[0]
        if not _HELP.search(segment):
            judged.append((match.group(1), match.group(2), bool(_HAS_REASON.search(segment))))
    if not judged:
        return None
    return judged[0][0], judged[0][1], all(j[2] for j in judged), len(judged)


_BYPASS = ("git hook bypass (--no-verify, -n, core.hooksPath or an overridden environment) is not allowed here; "
           "commit or push without it so the Baton hooks scan the change")
_STDIN = ("a body read from stdin cannot be scanned unless a heredoc in the same command supplies it; "
          "pass the text literally, as an inline heredoc, or as a file")
_SUBSTITUTED = ("text built from a variable, a command substitution or backticks in a title, body or "
                "field cannot be scanned; pass it literally, as an inline heredoc, or as a file")
_EMPTY_BODY = ("the body is empty or only whitespace, so this would publish a blank issue or pull request "
               "text; write the full text first, then pass it with --body or --body-file")
_HOOKS_MISSING = ("Baton git hooks are missing or point at a baton that no longer exists; "
                  "run `baton install-hooks`")


_BODY_WRITE = re.compile(
    _POS + r"""gh\s+(?:(?:-R|--repo)(?:\s+|=)\S+\s+|--repo=\S+\s+)?(?:issue|pr)\s+(?:create|edit)(?=\s|$)""")
_EMPTY_INLINE = re.compile(r"""(?<![\w-])(?:--body|-b)(?:\s+|=)(?:"\s*"|'\s*')(?=\s|[;&|)]|$)""")


_BODY_FILE_FLAG = re.compile(r"\s*(?:--body-file|-F)(?![\w-])")


def _body_write_spans(command):
    """(start, end) offsets of each `gh [-R repo] issue|pr create|edit` segment in `command`.

    A segment runs to the next unquoted ; & | or newline."""
    spans = []
    for match in _BODY_WRITE.finditer(command):
        index, quote = match.end(), None
        while index < len(command):
            char = command[index]
            if quote:
                if char == quote:
                    quote = None
            elif char in "\"'":
                quote = char
            elif char in ";&|\n":
                break
            index += 1
        spans.append((match.start(), index))
    return spans


def _empty_inline_body(command, spans):
    """True when a quoted --body / -b value inside one of the spans is empty or only whitespace."""
    return any(_EMPTY_INLINE.search(command, start, end) for start, end in spans)


def forge_facing(command, forge_clis=FORGE_CLIS):
    """True when the command names a forge-facing program or publishes with git."""
    return bool(_forge_re(tuple(forge_clis)).search(command) or _GITPUB.search(command))


def _brief_lists(cwd):
    """(forge_clis, body_file_flags) from the brief, or the defaults when there is none."""
    try:
        cfg = config.load(cwd)
    except config.ConfigError:
        return FORGE_CLIS, BODY_FILE_FLAGS
    if cfg is None:
        return FORGE_CLIS, BODY_FILE_FLAGS
    return tuple(cfg.data["forge_clis"]), tuple(cfg.data["body_file_flags"])


def _read(path, base):
    """Return (text, problem) for a message file a command publishes, read relative to `base`."""
    if path == "-":
        return None, _STDIN
    full = os.path.join(base, os.path.expanduser(path)) if base is not None else None
    if full is None or not os.path.isfile(full):
        return None, (f"body file {path} not found, so it cannot be scanned; "
                      "write it in an earlier step")
    if os.path.getsize(full) > _MAX_FILE:
        return None, f"body file {path} is too large to scan"
    with open(full, errors="replace") as handle:
        return handle.read(), None


_CD = re.compile(r"""(?:^|(?<=[;&|(\n]))[ \t]*cd(?:[ \t]+(?:"([^"`]*)"|'([^']*)'|([^\s'"`;&|()<>]*)))?[ \t]*(?=$|[;&|)\n])""")


_OPAQUE = re.compile(r"""'[^']*'|"[^"]*"|\$\{[^}]*\}|\$\([^()]*\)|`[^`]*`|(?:(?<=\s)|^)#[^\n]*""")


def _cd_base(command, end, cwd):
    """Directory a file named at offset `end` is read from, following literal `cd`s before it.

    A `cd` counts only when it is a plain command the shell ran in the publishing command's
    shell: outside quotes, comments and substitutions, not in a closed `( )` or `{ }` group,
    not in a pipeline, not before `||` or `&`, and joined onward by `;`, `&&` or a newline.
    None when a `cd` has no target or a non-literal one, or when it may not have applied."""
    masked = _OPAQUE.sub(lambda m: " " * len(m.group(0)), command)
    stack, owner = [], {}  # innermost open bracket (its offset) at each offset
    for i, ch in enumerate(masked[:end]):
        owner[i] = stack[-1] if stack else -1
        if ch in "({":
            stack.append(i)
        elif ch in ")}" and stack:
            stack.pop()
    base = cwd
    for match in _CD.finditer(command[:end]):
        pos = match.start() + len(match.group(0)) - len(match.group(0).lstrip())
        if masked[pos:pos + 2] != "cd":
            continue  # inside quotes, a comment or a substitution: the shell never runs it
        before = masked[:pos].rstrip(" \t")
        after = masked[match.end():end].lstrip(" \t")
        joined = after[:1] in (";", "\n") or after.startswith("&&")
        ran = not before.endswith(("|", "&")) or before.endswith("&&")
        if owner.get(pos, -1) not in [-1] + stack or not joined or not ran:
            base = None
            continue
        target = next((g for g in match.groups() if g is not None), None)
        if not target or target == "-" or re.search(r"[$`*?]", target):
            base = None
            continue
        target = os.path.expanduser(target)
        if os.path.isabs(target):
            base = target
        elif base is not None:
            base = os.path.join(base, target)
    return base


def _body_files(command, git_only, flags=BODY_FILE_FLAGS):
    """(path, offset of the flag) of the body files the command names."""
    paths = []
    flags = tuple(flags)
    for match in _file_arg_re(flags).finditer(command):
        flag = match.group(1) or match.group(2)
        value = next(g for g in match.groups()[2:] if g is not None)
        if value.startswith("-") and value != "-":
            continue
        if git_only:
            if flag in ("-F", "--file") and not value.startswith("@"):
                paths.append((value, match.start()))
        elif flag in flags and flag.startswith("--"):
            paths.append((value.lstrip("@"), match.start()))
        elif value.startswith("@") and (flag in flags or flag == "-d" or flag.startswith("--data")):
            paths.append((value[1:], match.start()))
        elif flag in flags:
            if "=@" in value:
                paths.append((value.split("=@", 1)[1], match.start()))
            elif "=" not in value:
                paths.append((value, match.start()))
    return [pair for pair in paths if pair[0] != "-"]


_MARKED = re.compile(re.escape(MARKER) + r"(?:developer|refine|reviewer|researcher|orchestrator):(?:closed|reopened) -->")


def close_marker(command, cwd, agent_type, env):
    """(new_command, problems) for a gh issue|pr close|reopen that has a reason.

    (None, []) when there is nothing to do; (new_command, []) when the comment gets Baton's marker;
    (None, [refusal]) when the marker cannot be added and the agent has to write it.
    """
    stripped = _HEREDOC.sub(r"<<HD\2", command)
    found = _close_command(stripped)
    if not found or not found[2]:
        return None, []
    try:
        if config.load(cwd) is None:
            return None, []
    except config.ConfigError:
        return None, []
    target, action, _, count = found
    if count > 1:
        if len(_MARKED.findall(command)) >= count:
            return None, []
        return None, [f"`gh {target} {action}`: several close/reopen commands in one command cannot be "
                      "rewritten; run each on its own, or put Baton's marker "
                      f"(`{MARKER}<role>:closed -->`) on every --comment"]
    if _MARKED.search(command):
        return None, []
    kind = "closed" if action == "close" else "reopened"
    role = _role(agent_type)
    line = f"{MARKER}{role}:{kind} -->"
    version = _claude_version(env)
    if version is None or version < REWRITE_FLOOR:
        return None, [f"`gh {target} {action}`: add `{line}` as the first line of the --comment text "
                      "(this Claude Code version cannot have Baton add it)"]
    argv = _simple_argv(command)
    if argv is None:
        return None, [f"`gh {target} {action}`: add `{line}` as the first line of the --comment text "
                      "(Baton cannot add it to a compound command or one with variables, substitutions, "
                      "heredocs or several lines)"]
    if "gh" not in argv:
        return None, []
    for i in range(argv.index("gh"), len(argv)):
        arg = argv[i]
        if arg in ("-c", "--comment") and i + 1 < len(argv):
            argv[i + 1] = _stamp(role, kind, argv[i + 1])
        elif arg.startswith("--comment="):
            argv[i] = "--comment=" + _stamp(role, kind, arg[len("--comment="):])
        elif arg.startswith("-c") and not arg.startswith("--") and len(arg) > 2:
            argv[i] = "-c" + _stamp(role, kind, arg[2:])
        else:
            continue
        return shlex.join(argv), []
    return None, []


def _has_variable(segment):
    for m in _BODY_VALUE.finditer(segment):
        value = m.group(1) if m.group(1) is not None else m.group(3)
        if value and _VARIABLE.search(value):
            return True
    return False


def _forge_segments(command, clis):
    """The parts of `command`, split at ; && || | & and newlines outside quotes, that run a forge CLI."""
    segments, current, quote = [], [], ""
    for ch in command:
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch in ";&|\n":
            segments.append("".join(current))
            current = []
            continue
        current.append(ch)
    segments.append("".join(current))
    pattern = _forge_re(tuple(clis))
    return [seg for seg in segments if pattern.search(seg)]


def decide(command, cwd, terms):
    stripped = _HEREDOC.sub(r"<<HD\2", command)
    clis, flags = _brief_lists(cwd)
    forge = [m.group(1) for m in _forge_re(clis).finditer(stripped)]
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
        blanked = _QUOTED.sub('""', _QUOTED_OPT.sub(r"\2", stripped))
        if (_BYPASS_TEXT.search(blanked) or _SHORT_N.search(blanked)
                or _BYPASS_CFG.search(stripped)):
            problems.append(_BYPASS)
        if cfg is not None and not githooks.installed(cfg.root):
            problems.append(_HOOKS_MISSING)

    if cfg is not None and forge and _empty_inline_body(stripped, _body_write_spans(stripped)):
        problems.append(_EMPTY_BODY)

    closing = _close_command(stripped) if cfg is not None else None
    if closing and not closing[2]:
        problems.append(_CLOSE_REASON)

    http_only = bool(forge) and not git and all(name in _HTTP_CLIS for name in forge)
    stdin_pattern = _stdin_re(flags) if forge else _GIT_STDIN
    if stdin_pattern.search(stripped) and "<<" not in stripped and (forge or "|" not in stripped):
        problems.append(_STDIN)
    cats = []  # (path, offset) of each literal $(cat <path>) inside a double-quoted string
    scanned = []
    last = 0
    for quoted in _QUOTED.finditer(stripped):
        if not quoted.group().startswith('"'):
            continue
        for sub in _CAT_SUBST.finditer(quoted.group()):
            cats.append((sub.group(1) or sub.group(2), quoted.start() + sub.start()))
            scanned.append(stripped[last:quoted.start() + sub.start()])
            last = quoted.start() + sub.end()
    scanned.append(stripped[last:])
    without_cats = "".join(scanned)
    segments = _forge_segments(without_cats, clis) if forge else []
    if any(_SUBST_BODY.search(seg) or _has_variable(seg) for seg in segments):
        problems.append(_SUBSTITUTED)

    texts = [m.group(3) for m in _HEREDOC.finditer(command)]
    if http_only:
        texts += [m.group(1) for m in _DATA_FLAG.finditer(stripped)]
    else:
        texts += _QUOTED.findall(without_cats)
    for seg in segments:
        texts += [m.group(3) for m in _BODY_VALUE.finditer(seg)
                  if m.group(3) and m.group(3)[0] not in "-@$"]
    for path, start in cats:
        text, problem = _read(path, _cd_base(stripped, start, cwd))
        if problem:
            problems.append(problem)
        else:
            texts.append(text)
    for path, start in _body_files(stripped, git_only=not forge, flags=flags):
        if _UNRESOLVABLE.search(path):
            problems.append(f"body file {path} is named by a variable or pattern, so it cannot be scanned; "
                            "pass a literal path")
            continue
        base = _cd_base(stripped, start, cwd)
        full = os.path.join(base, os.path.expanduser(path)) if base is not None else None
        if base is not None and not os.path.isfile(full):
            masked = _OPAQUE.sub(lambda m: " " * len(m.group(0)), stripped)
            if re.search(r"(?<![<>&\d])>>?[ \t]*['\"]?" + re.escape(path) + r"(?=[\s;&|)'\"]|$)", masked[:start]):
                continue  # an earlier step of the same command writes it
        text, problem = _read(path, base)
        if problem:
            problems.append(problem)
        else:
            texts.append(text)
            if (cfg is not None and forge and not text.strip() and _BODY_FILE_FLAG.match(stripped, start)
                    and any(a <= start < b for a, b in _body_write_spans(stripped))):
                problems.append(_EMPTY_BODY)

    findings = scan.scan_text("\n".join(texts), terms, allow)
    kinds = sorted({finding.kind for finding in findings})
    problems.extend(f"privacy: {kind} in the text this command would publish" for kind in kinds)
    return list(dict.fromkeys(problems))


Completion = collections.namedtuple("Completion", "command note problem")

_PR_CREATE_RE = re.compile(_POS + r"(?:[\w.~+-]*/)*gh\s+(?:-\S+\s+)*pr\s+create\b")
_HEAD_FLAG = re.compile(r"(?<![\w-])(?:--head(?=[\s=]|$)|-H)")
_PR_CREATE_HEAD = re.compile(r"^\s*(?:[\w.~+-]*/)*gh[ \t]+pr[ \t]+create(?=[ \t]|$)")
_PUNCT = "();<>|&\n`"


def _command_tokens(command):
    """The command's shell tokens, or None when it is not simple (unbalanced quotes, or any
    token made only of separator, redirection, substitution or newline characters)."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=_PUNCT)
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return None
    if any(token and all(ch in _PUNCT for ch in token) for token in tokens):
        return None
    return tokens

_GH_WRITES = {
    "issue": frozenset("create edit comment close reopen delete lock unlock pin unpin transfer develop".split()),
    "pr": frozenset("create edit comment close reopen ready review lock unlock".split()),
    "label": frozenset("create edit delete clone".split()),
    "release": frozenset("create edit delete upload".split()),
}
_CD_STEPS = re.compile(r"^\s*(?:cd[ \t]+(?:'[^']*'|\"[^\"$`\\]*\"|[^\s'\"$`*?;&|()<>]+)[ \t]*&&[ \t]*)*")
_API_FIELD_FLAGS = frozenset(("-f", "-F", "--field", "--raw-field", "--input"))


def _gh_tokens(command):
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=_PUNCT)
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        return None


def _is_punct(token):
    return bool(token) and all(ch in _PUNCT for ch in token)


def _api_is_write(args):
    method = None
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-X", "--method"):
            method = args[i + 1] if i + 1 < len(args) else ""
            i += 1
        elif a.startswith("--method="):
            method = a[len("--method="):]
        elif a.startswith("-X") and len(a) > 2:
            method = a[2:]
        i += 1
    if method is not None:
        return method.upper() not in ("GET", "HEAD")
    return any(a in _API_FIELD_FLAGS or a.startswith(("--field=", "--raw-field=", "--input=")) for a in args)


def _gh_write_offset(command):
    """Offset of the `gh` starting a recognised write (after only `cd <dir> &&` steps), else None."""
    tokens = _gh_tokens(command)
    if not tokens:
        return None
    m = _CD_STEPS.match(command)
    steps = 0
    i = 0
    while i + 2 < len(tokens) and tokens[i] == "cd" and tokens[i + 2] == "&&" and not _is_punct(tokens[i + 1]):
        steps += 1
        i += 3
    if _count_cd(command[:m.end()]) != steps:
        return None
    rest = tokens[i:]
    if len(rest) < 3 or any(_is_punct(t) for t in rest):
        return None
    if rest[0].rsplit("/", 1)[-1] != "gh" or not re.match(r"(?:[\w.~+-]*/)*gh(?=[ \t]|$)", command[m.end():]):
        return None
    return m.end() if _is_write_words(rest) else None


def exec_role_problem(command, agent_type):
    """A refusal when an agent-typed `baton exec` names a role other than the caller's, else None."""
    tokens = _gh_tokens(command)
    if not tokens:
        return None
    i = 0
    while i + 2 < len(tokens) and tokens[i] == "cd" and tokens[i + 2] == "&&" and not _is_punct(tokens[i + 1]):
        i += 3
    rest = tokens[i:]
    if len(rest) < 2 or rest[0].rsplit("/", 1)[-1] != "baton" or rest[1] != "exec":
        return None
    role = "orchestrator"
    j = 2
    while j < len(rest) and rest[j] not in ("gh", "--"):
        if rest[j] == "--role" and j + 1 < len(rest):
            role = rest[j + 1]
            j += 1
        elif rest[j].startswith("--role="):
            role = rest[j][len("--role="):]
        j += 1
    mine = _role(agent_type)
    if role == mine:
        return None
    return (f"`baton exec` may only write as your own role: use `--role {mine}` "
            f"(this command names `{role}`; the default is `orchestrator`).")


def _is_write_words(rest):
    """True when `rest` (gh, group, sub, ...) is a write the gate recognises."""
    if len(rest) < 3:
        return False
    group, sub = rest[1], rest[2]
    if group == "api":
        return _api_is_write(rest[2:])
    return sub in _GH_WRITES.get(group, ())


def is_recognised_write(words):
    """True when the argv `words` is a plain `gh` write the gate recognises (no `cd &&` prefix)."""
    return bool(words) and words[0] == "gh" and not any(_is_punct(t) for t in words) and _is_write_words(words)


def _count_cd(prefix):
    return len(re.findall(r"(?:^|&&)\s*cd[ \t]", prefix))


def _head_step(command, cwd, env, runner=run.run):
    """Add --head to a `gh pr create` from a detached HEAD: a Completion, or None when not needed."""
    stripped = _QUOTED.sub('""', _HEREDOC.sub("<<HD", command))
    if not _PR_CREATE_RE.search(stripped) or _HEAD_FLAG.search(stripped):
        return None
    if not branches.detached(runner, cwd):
        return None
    names = branches.pushed_branches_at_head(runner, cwd)
    if len(names) != 1 or not branches.BRANCH_NAME.fullmatch(names[0]):
        return Completion(None, None, (
            "`gh pr create` from a detached HEAD needs `--head <branch>`, and no single pushed "
            "issue-<N>-* branch points at HEAD. Push it with `baton push HEAD:refs/heads/<branch>`, "
            "then add `--head <branch>` after `gh pr create`."))
    name = shlex.quote(names[0])
    need = (f"`gh pr create` from a detached HEAD needs `--head <name>`. "
            f"Add `--head {name}` after `gh pr create` and run it again.")
    tokens = _command_tokens(command)
    match = _PR_CREATE_HEAD.match(command)
    if (tokens is None or len(tokens) < 3 or tokens[1:3] != ["pr", "create"]
            or os.path.basename(tokens[0]) != "gh" or match is None):
        return Completion(None, None, need)
    version = _claude_version(env)
    if version is None or version < REWRITE_FLOOR:
        return Completion(None, None, need[:-len(" and run it again.")]
                          + ": this Claude Code cannot have Baton add it.")
    new = command[:match.end()] + f" --head {name}" + command[match.end():]
    note = (f"Baton added `--head {name}` to `gh pr create` (HEAD is detached). "
            f"The command that ran: {new}")
    return Completion(new, note, None)


def _complete(command, cwd, env, runner=run.run, agent_type=None):
    """The --head step, then at tier 1 the `baton exec` prefix on a recognised gh write."""
    done = _head_step(command, cwd, env, runner)
    if done is not None and done.problem:
        return done
    current = done.command if done is not None and done.command else command
    version = _claude_version(env)
    if version is None or version < REWRITE_FLOOR:
        return done
    offset = _gh_write_offset(current)
    if offset is None:
        return done
    ids = identity.configured_for(cwd, runner)
    if ids is None:
        return done
    role = _role(agent_type)
    app = getattr(ids, identity.route(role))
    if app is None:
        return done
    new = current[:offset] + f"baton exec --role {role} " + current[offset:]
    note = f"Baton ran this write as the {app.login} App: {new}"
    if done is not None and done.command:
        note = f"{done.note} {note}"
    return Completion(new, note, None)


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
    cwd = os.getcwd()
    rewritten = None
    completion = None
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
                if not problems:
                    agent = payload.get("agent_type")
                    refusal = exec_role_problem(command, agent if isinstance(agent, str) else None)
                    problems = [refusal] if refusal else []
                if not problems:
                    rewritten, problems = close_marker(
                        command, cwd, payload.get("agent_type"), os.environ)
                if not problems:
                    agent_type = payload.get("agent_type")
                    completion = _complete(
                        rewritten if rewritten is not None else command, cwd, os.environ,
                        agent_type=agent_type if isinstance(agent_type, str) else None)
                    if completion is not None and completion.problem:
                        problems = [completion.problem]
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
    except Exception as error:
        # Fail closed only when the command could publish; any other command is allowed.
        try:
            triggered = mcp or (forge_facing(command, _brief_lists(cwd)[0]) if isinstance(command, str) else True)
        except Exception:
            triggered = True
        if not triggered:
            return 0
        print(f"Baton publish gate could not check this command ({error!r}).", file=sys.stderr)
        return 2
    if not problems:
        if rewritten is not None or (completion is not None and completion.command):
            notes = []
            final = rewritten
            if rewritten is not None:
                marker = re.search(r"<!-- baton:\S+ -->", rewritten)
                notes.append(f"Baton added its {marker.group(0) if marker else 'marker'} marker to the "
                             f"comment. The command that ran: {rewritten}")
            if completion is not None and completion.command:
                final = completion.command
                notes.append(completion.note)
            updated = dict(payload.get("tool_input") or {})
            updated["command"] = final
            note = " ".join(notes)
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "updatedInput": updated,
                "additionalContext": note}}))
            return 0
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
