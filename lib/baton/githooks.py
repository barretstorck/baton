import json
import os
import pathlib
import re
import shlex

from . import config, run, scan

HOOK_TAG = "baton git-hook"
ZEROS = "0" * 40
HOOKS = ("pre-commit", "commit-msg", "pre-push")


class HookError(Exception):
    pass


def hooks_dir(repo):
    """The directory git actually runs hooks from (honours core.hooksPath)."""
    out = run.run(["git", "-C", str(repo), "rev-parse", "--git-path", "hooks"]).strip()
    return pathlib.Path(os.path.join(str(repo), out))


def added_text(patch):
    """The lines a patch adds, without diff headers, hunk headers or context."""
    return "\n".join(line[1:] for line in patch.split("\n")
                     if line.startswith("+") and not line.startswith("+++"))


_ESCAPES = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, "\\": 92, '"': 34}


def unquote_path(name):
    """Undo the C-style quoting of a path ("..." with backslash and octal escapes).

    A name that does not start with a double quote is returned as it is. Raises ValueError for
    a quoted name that cannot be parsed or is not UTF-8.
    """
    if not name.startswith('"'):
        return name
    if len(name) < 2 or not name.endswith('"'):
        raise ValueError(name)
    body, out, i = name[1:-1], bytearray(), 0
    while i < len(body):
        ch = body[i]
        if ch == '"':
            raise ValueError(name)
        if ch != "\\":
            out += ch.encode("utf-8")
            i += 1
            continue
        nxt = body[i + 1:i + 2]
        if nxt and nxt in "01234567":
            digits = body[i + 1:i + 4]
            if len(digits) < 3 or any(d not in "01234567" for d in digits) or int(digits, 8) > 255:
                raise ValueError(name)
            out.append(int(digits, 8))
            i += 4
        elif nxt in _ESCAPES:
            out.append(_ESCAPES[nxt])
            i += 2
        else:
            raise ValueError(name)
    try:
        return out.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(name) from None


def _new_path(line):
    """(path, parsed) from a "+++ " header; an unparsable quoted name is kept raw, parsed False."""
    name = line[4:].rstrip("\r")
    if not name.startswith('"'):
        name = name.split("\t")[0]
    try:
        name = unquote_path(name)
    except ValueError:
        return name, False
    if name == "/dev/null":
        return None, True
    return (name[2:] if name.startswith("b/") else name), True


def unparsable_headers(patch):
    """The raw "+++ " headers whose quoted path cannot be decoded."""
    return [line for line in patch.split("\n")
            if line.startswith("+++ ") and not _new_path(line)[1]]


def added_by_file(patch):
    """(path, added lines) for each file a patch changes, by the path on the new side.

    Files that are only deleted or only change mode have no added lines and give no entry.
    A line starting with "+++ " inside a hunk is an added line, not a header.
    """
    entries = []
    path = None
    added = []
    in_hunk = False

    def flush():
        if path is not None and added:
            entries.append((path, "\n".join(added)))

    for line in patch.split("\n"):
        if line.startswith("diff --git "):
            flush()
            path, added, in_hunk = None, [], False
        elif in_hunk:
            if line.startswith("+"):
                added.append(line[1:])
        elif line.startswith("+++ "):
            path = _new_path(line)[0]
        elif line.startswith("@@"):
            in_hunk = True
    flush()
    return entries


def _image_problems(cfg, paths):
    allowed = tuple(cfg.data["fixtures_paths"])
    problems = []
    for path in paths:
        if pathlib.PurePosixPath(path).suffix.lower() not in scan.IMAGE_SUFFIXES:
            continue
        if allowed and path.startswith(allowed):
            continue
        problems.append(f"new image file outside fixtures_paths: {path}")
    return problems


def _forbid_problems(cfg, patch, cwd, branch):
    rules = cfg.data["forbid_added"]
    if not rules:
        return []
    from . import forbid

    def runner(cmd):
        if cmd[0] == "gh":
            return run.run(cmd)
        return run.run(["git", "-C", str(cwd)] + cmd[1:])

    allowed = forbid.allowed_globs(forbid.plan_text(runner, cfg, branch))
    return forbid.violations(patch, rules, allowed)


def _current_branch(cwd):
    try:
        return run.run(["git", "-C", str(cwd), "branch", "--show-current"]).strip()
    except run.RunError:
        return ""


def _check(cfg, patch, new_files, terms, messages="", cwd=".", branch=""):
    allow = cfg.data["privacy_allow"]
    findings = scan.scan_text("\n".join([messages] + new_files), terms, allow)
    for path, text in added_by_file(patch):
        skip = {"private-ip"} if scan.skips_private_ip(path) else ()
        findings += scan.scan_text(text, terms, allow, skip=skip)
    kinds = sorted({finding.kind for finding in findings})
    problems = [f"privacy: {kind} in added lines, new file names or commit message"
                for kind in kinds]
    return problems + _image_problems(cfg, new_files) + _forbid_problems(cfg, patch, cwd, branch)


def _names(output):
    return [name for name in output.split("\n") if name]


def pre_commit(cwd, terms):
    cfg = config.load(cwd)
    if cfg is None:
        return []
    git = ["git", "-C", cwd, "-c", "core.quotePath=false"]
    patch = run.run(git + ["diff", "--cached", "-U0", "--no-color"])
    new_files = _names(run.run(git + ["diff", "--cached", "--name-only", "--diff-filter=AR"]))
    return _check(cfg, patch, new_files, terms, cwd=cwd, branch=_current_branch(cwd))


def commit_msg(cwd, message_path, terms):
    """Scan a commit message file; lines starting with `#` are git comments and are skipped."""
    cfg = config.load(cwd)
    if cfg is None:
        return []
    text = pathlib.Path(message_path).read_text(errors="replace")
    message = "\n".join(line for line in text.split("\n") if not line.startswith("#"))
    findings = scan.scan_text(message, terms, cfg.data["privacy_allow"])
    return [f"privacy: {kind} in the commit message" for kind in sorted({f.kind for f in findings})]


def _pushed_branch(local_ref, remote_ref):
    """The remote branch name when the push is tied to the issue it names, else "" (no allowances).

    A hook cannot know the pull request, so the remote branch must name an issue, as merge-pr
    requires of the head branch. A local branch must name that same issue; a local ref that is
    not a branch (`HEAD` from a detached worktree) names none, so the remote branch decides."""
    from . import forbid  # lazy: forbid imports this module
    prefix = "refs/heads/"
    if not remote_ref.startswith(prefix):
        return ""
    remote = remote_ref[len(prefix):]
    number = forbid.branch_issue(remote)
    if number is None:
        return ""
    if local_ref.startswith(prefix):
        return remote if forbid.branch_issue(local_ref[len(prefix):]) == number else ""
    return remote


def pre_push(cwd, stdin_text, terms):
    cfg = config.load(cwd)
    if cfg is None:
        return []
    git = ["git", "-C", cwd, "-c", "core.quotePath=false"]
    problems = []
    rows = [parts for parts in (line.split() for line in stdin_text.splitlines()) if len(parts) == 4]
    for local_ref, local_sha, remote_ref, remote_sha in rows:
        if local_sha == ZEROS:
            continue
        revs = [local_sha, "--not", "--remotes"] if remote_sha == ZEROS else [f"{remote_sha}..{local_sha}"]
        patch = run.run(git + ["log", "-p", "-U0", "--no-color", "--format="] + revs)
        messages = run.run(git + ["log", "--format=%B"] + revs)
        new_files = _names(run.run(
            git + ["log", "--diff-filter=AR", "--name-only", "--format="] + revs))
        branch = _pushed_branch(local_ref, remote_ref)
        problems.extend(_check(cfg, patch, new_files, terms, messages, cwd=cwd, branch=branch))
        if remote_ref.startswith("refs/tags/") and run.run(git + ["cat-file", "-t", local_sha]).strip() == "tag":
            raw = run.run(git + ["cat-file", "tag", local_sha])
            tag_message = raw.split("\n\n", 1)[1] if "\n\n" in raw else ""
            findings = scan.scan_text(tag_message, terms, cfg.data["privacy_allow"])
            problems.extend(f"privacy: {kind} in the tag message" for kind in sorted({f.kind for f in findings}))
    return sorted(set(problems))


def recorded_bin(text):
    """The baton path a hook's text records (new or old format), or None."""
    match = re.search(r"^baton_bin=(.+)$", text, re.M) or re.search(
        r'^exec ("[^"\n]*") git-hook ', text, re.M)
    if not match:
        return None
    try:
        words = shlex.split(match.group(1))
    except ValueError:
        return None
    return words[0] if len(words) == 1 and words[0] else None


# Prints the first executable bin/baton among the baton@baton entries of the install record.
# Run by the hook as `python3 -I -c <script> <root>`; keep it free of single quotes.
RESOLVE_SCRIPT = (
    "import json, os, sys\n"
    "try:\n"
    "    d = json.load(open(os.path.join(sys.argv[1], \"installed_plugins.json\")))\n"
    "    for e in d[\"plugins\"][\"baton@baton\"]:\n"
    "        p = os.path.join(e[\"installPath\"], \"bin\", \"baton\")\n"
    "        if os.access(p, os.X_OK):\n"
    "            print(p)\n"
    "            break\n"
    "except Exception:\n"
    "    pass\n"
)


def plugin_root():
    return os.environ.get("CLAUDE_CODE_PLUGIN_CACHE_DIR") or os.path.join(
        os.environ.get("HOME") or os.path.expanduser("~"), ".claude", "plugins")


def resolve_bin(recorded):
    """The baton a hook runs: the install record's, else the recorded path if executable, else None."""
    try:
        with open(os.path.join(plugin_root(), "installed_plugins.json")) as f:
            entries = json.load(f)["plugins"]["baton@baton"]
        for entry in entries:
            path = os.path.join(entry["installPath"], "bin", "baton")
            if os.access(path, os.X_OK):
                return path
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if recorded and os.access(recorded, os.X_OK) and os.path.isfile(recorded):
        return recorded
    return None


def installed(repo):
    try:
        directory = hooks_dir(repo)
    except run.RunError:
        return False
    for name in HOOKS:
        try:
            text = (directory / name).read_text()
            if HOOK_TAG not in text:
                return False
            recorded = recorded_bin(text)
            if recorded is None or resolve_bin(recorded) is None:
                return False
        except OSError:
            return False
    return True


def install(repo, baton_bin):
    directory = hooks_dir(repo)
    directory.mkdir(parents=True, exist_ok=True)
    targets = [directory / name for name in HOOKS]
    for target in targets:
        if target.exists() and HOOK_TAG not in target.read_text():
            raise HookError(f"{target} exists and is not a Baton hook; merge it by hand")
    written = []
    for target in targets:
        target.write_text(
            "#!/bin/sh\n"
            f"# {HOOK_TAG}: installed by `baton install-hooks`\n"
            f"baton_bin={shlex.quote(str(baton_bin))}\n"
            'recorded=$baton_bin\n'
            'root=${CLAUDE_CODE_PLUGIN_CACHE_DIR:-$HOME/.claude/plugins}\n'
            f"found=$(python3 -I -c '{RESOLVE_SCRIPT}' \"$root\" 2>/dev/null) || found=\n"
            'if [ -n "$found" ]; then baton_bin=$found\n'
            'elif [ ! -x "$baton_bin" ]; then baton_bin=$(command -v baton) || baton_bin=; fi\n'
            'if [ -z "$baton_bin" ]; then\n'
            '  echo "baton: this Baton git hook cannot find baton (installed from $recorded);'
            ' run \\`baton install-hooks\\`" >&2\n'
            "  exit 1\n"
            "fi\n"
            f'exec "$baton_bin" git-hook {target.name} "$@"\n')
        target.chmod(0o755)
        written.append(str(target))
    return written
