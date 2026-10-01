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


def _check(cfg, patch, new_files, terms, messages=""):
    text = "\n".join([added_text(patch), messages] + new_files)
    findings = scan.scan_text(text, terms, cfg.data["privacy_allow"])
    kinds = sorted({finding.kind for finding in findings})
    problems = [f"privacy: {kind} in added lines, new file names or commit message"
                for kind in kinds]
    return problems + _image_problems(cfg, new_files)


def _names(output):
    return [name for name in output.split("\n") if name]


def pre_commit(cwd, terms):
    cfg = config.load(cwd)
    if cfg is None:
        return []
    git = ["git", "-C", cwd, "-c", "core.quotePath=false"]
    patch = run.run(git + ["diff", "--cached", "-U0", "--no-color"])
    new_files = _names(run.run(git + ["diff", "--cached", "--name-only", "--diff-filter=AR"]))
    return _check(cfg, patch, new_files, terms)


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
        _, local_sha, _, remote_sha = parts
        if local_sha == ZEROS:
            continue
        revs = [local_sha, "--not", "--remotes"] if remote_sha == ZEROS else [f"{remote_sha}..{local_sha}"]
        patch = run.run(git + ["log", "-p", "-U0", "--no-color", "--format="] + revs)
        messages = run.run(git + ["log", "--format=%B"] + revs)
        new_files = _names(run.run(
            git + ["log", "--diff-filter=AR", "--name-only", "--format="] + revs))
        problems.extend(_check(cfg, patch, new_files, terms, messages))
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
