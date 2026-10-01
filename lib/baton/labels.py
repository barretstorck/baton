import json

from . import config

_STYLE = {
    "status:refinement": ("d4c5f9", "Needs work before it is ready"),
    "status:ready": ("0e8a16", "Meets the definition of ready"),
    "status:in-dev": ("1d76db", "Claimed by a developer"),
    "status:blocked": ("b60205", "Waiting on an answer or dependency"),
    "status:in-review": ("fbca04", "Pull request ready for the acceptance check"),
    "status:verification": ("c2e0c6", "Merged; waiting for the default branch check"),
    "status:complete": ("ededed", "Verified and closed"),
    "type:feature": ("a2eeef", "New behaviour"),
    "type:bug": ("d73a4a", "Something is wrong"),
    "type:maintenance": ("cfd3d7", "Refactoring, tooling, upkeep"),
    "type:spike": ("5319e7", "Research; a pull request is optional"),
    "deliverable": ("0052cc", "Accepted into the sprint"),
    "stretch": ("bfd4f2", "In the sprint if time allows"),
    "broken-default-branch": ("b60205", "The default branch is failing; top priority"),
    "needs-maintainer": ("d93f0b", "Waiting for the maintainer"),
}


def _existing(runner):
    out = runner(["gh", "label", "list", "--limit", "500", "--json", "name"])
    return {item["name"] for item in json.loads(out or "[]")}


def missing(cfg, runner):
    existing = _existing(runner)
    return [cfg.label(name) for name in _STYLE if cfg.label(name) not in existing]


def ensure(cfg, runner, create):
    existing = _existing(runner)
    created, still = [], []
    for name, (color, description) in _STYLE.items():
        actual = cfg.label(name)
        if actual in existing:
            continue
        if create:
            runner(["gh", "label", "create", actual, "--color", color,
                    "--description", description])
            created.append(actual)
        else:
            still.append(actual)
    return created, still


def set_status(cfg, runner, number, status):
    if status not in config.STATUSES:
        raise ValueError(f"unknown status {status!r}")
    out = runner(["gh", "issue", "view", str(number), "--json", "labels"])
    present = {item["name"] for item in json.loads(out)["labels"]}
    target = cfg.label(status)
    argv = ["gh", "issue", "edit", str(number), "--add-label", target]
    for other in cfg.status_labels():
        if other != target and other in present:
            argv += ["--remove-label", other]
    runner(argv)
