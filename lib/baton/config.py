import copy
import pathlib
from dataclasses import dataclass

from . import frontmatter, run

STATUSES = [
    "status:refinement", "status:ready", "status:in-dev", "status:blocked",
    "status:in-review", "status:verification", "status:complete",
]
OTHER_LABELS = [
    "type:spike", "deliverable", "stretch", "broken-default-branch",
    "needs-maintainer",
]
DEFAULTS = {
    "developer": {"model": "sonnet", "max_parallel": 2},
    "refine": {"may_mark_ready": True},
    "labels": {
        "auto_create": True,
        "map": {name: name for name in STATUSES + OTHER_LABELS},
    },
    "merge": {"allow_no_ci": False},
    "protected_paths": [".github/", ".baton/", ".claude/"],
    "fixtures_paths": [],
    "privacy_allow": [],
    "budgets": {},
}


class ConfigError(Exception):
    pass


@dataclass
class Config:
    root: pathlib.Path
    data: dict

    def label(self, name):
        return self.data["labels"]["map"].get(name, name)

    def status_labels(self):
        return [self.label(name) for name in STATUSES]


def _merge(base, extra):
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def repo_root(path):
    try:
        out = run.run(["git", "-C", path, "rev-parse", "--show-toplevel"])
    except (run.RunError, OSError):
        return None
    return pathlib.Path(out.strip())


def load(path):
    root = repo_root(path)
    if root is None:
        return None
    brief = root / ".baton" / "brief.md"
    if not brief.is_file():
        return None
    meta, _ = frontmatter.split(brief.read_text())
    try:
        data = frontmatter.parse(meta)
    except ValueError as error:
        raise ConfigError(f".baton/brief.md: {error}") from error
    return Config(root=root, data=_merge(copy.deepcopy(DEFAULTS), data))
