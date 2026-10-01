import os
import pathlib

from . import config, gate, run, scan

ZEROS = "0" * 40
HOOKS = ("pre-commit", "pre-push")


class HookError(Exception):
    pass


def added_text(patch):
    """Lines a patch would add: everything except removed and context lines.

    Commit messages are scanned whole and separately, because a message line
    may itself start with "-" (a bullet) and must not be dropped.
    """
    return "\n".join(line for line in patch.splitlines()
                     if not line.startswith("-") and not line.startswith(" "))


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
    text = added_text(patch) + "\n" + messages
    findings = scan.scan_text(text, terms, cfg.data["privacy_allow"])
    kinds = sorted({finding.kind for finding in findings})
    problems = [f"privacy: {kind} in added lines or commit message" for kind in kinds]
    return problems + _image_problems(cfg, new_files)


def pre_commit(cwd, terms):
    cfg = config.load(cwd)
    if cfg is None:
        return []
    git = ["git", "-C", cwd]
    patch = run.run(git + ["diff", "--cached", "-U0", "--no-color"])
    new_files = run.run(git + ["diff", "--cached", "--name-only", "--diff-filter=A"]).split("\n")
    return _check(cfg, patch, [name for name in new_files if name], terms)


def pre_push(cwd, stdin_text, terms):
    cfg = config.load(cwd)
    if cfg is None:
        return []
    git = ["git", "-C", cwd]
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
        new_files = run.run(git + ["log", "--diff-filter=A", "--name-only", "--format="] + revs).split("\n")
        problems.extend(_check(cfg, patch, [name for name in new_files if name], terms, messages))
    return sorted(set(problems))


def install(repo, baton_bin):
    common = run.run(["git", "-C", repo, "rev-parse", "--git-common-dir"]).strip()
    hooks_dir = pathlib.Path(repo, common, "hooks")
    hooks_dir.mkdir(parents=True, exist_ok=True)
    targets = [hooks_dir / name for name in HOOKS]
    for target in targets:
        if target.exists() and gate.HOOK_TAG not in target.read_text():
            raise HookError(f"{target} exists and is not a Baton hook; merge it by hand")
    written = []
    for target in targets:
        target.write_text(
            "#!/bin/sh\n"
            f"# {gate.HOOK_TAG}: installed by `baton install-hooks`\n"
            f'exec "{baton_bin}" git-hook {target.name} "$@"\n')
        target.chmod(0o755)
        written.append(str(target))
    return written
