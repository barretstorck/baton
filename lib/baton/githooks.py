import os
import pathlib

from . import config, run, scan

HOOK_TAG = "baton git-hook"
ZEROS = "0" * 40
HOOKS = ("pre-commit", "pre-push")


class HookError(Exception):
    pass


def hooks_dir(repo):
    """The directory git actually runs hooks from (honours core.hooksPath)."""
    out = run.run(["git", "-C", str(repo), "rev-parse", "--git-path", "hooks"]).strip()
    return pathlib.Path(os.path.join(str(repo), out))


def added_text(patch):
    """The lines a patch adds, without diff headers, hunk headers or context."""
    return "\n".join(line[1:] for line in patch.splitlines()
                     if line.startswith("+") and not line.startswith("+++"))


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

    for line in patch.splitlines():
        if line.startswith("diff --git "):
            flush()
            path, added, in_hunk = None, [], False
        elif in_hunk:
            if line.startswith("+"):
                added.append(line[1:])
        elif line.startswith("+++ "):
            name = line[4:].split("\t")[0]
            path = None if name == "/dev/null" else name[2:] if name.startswith("b/") else name
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


def pre_push(cwd, stdin_text, terms):
    cfg = config.load(cwd)
    if cfg is None:
        return []
    git = ["git", "-C", cwd, "-c", "core.quotePath=false"]
    problems = []
    for line in stdin_text.splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        _, local_sha, remote_ref, remote_sha = parts
        if local_sha == ZEROS:
            continue
        revs = [local_sha, "--not", "--remotes"] if remote_sha == ZEROS else [f"{remote_sha}..{local_sha}"]
        patch = run.run(git + ["log", "-p", "-U0", "--no-color", "--format="] + revs)
        messages = run.run(git + ["log", "--format=%B"] + revs)
        new_files = _names(run.run(
            git + ["log", "--diff-filter=AR", "--name-only", "--format="] + revs))
        branch = (remote_ref[len("refs/heads/"):] if remote_ref.startswith("refs/heads/")
                  else _current_branch(cwd))
        problems.extend(_check(cfg, patch, new_files, terms, messages, cwd=cwd, branch=branch))
    return sorted(set(problems))


def installed(repo):
    try:
        directory = hooks_dir(repo)
    except run.RunError:
        return False
    for name in HOOKS:
        try:
            if HOOK_TAG not in (directory / name).read_text():
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
            f'exec "{baton_bin}" git-hook {target.name} "$@"\n')
        target.chmod(0o755)
        written.append(str(target))
    return written
