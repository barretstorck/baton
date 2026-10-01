import copy
import pathlib
from dataclasses import dataclass

from . import frontmatter, run

STATUSES = [
    "status:refinement", "status:ready", "status:in-dev", "status:blocked",
    "status:in-review", "status:verification", "status:complete",
]
OTHER_LABELS = [
    "type:feature", "type:bug", "type:maintenance", "type:spike", "deliverable", "stretch", "broken-default-branch",
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


def _kind_ok(value, default):
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(default, list):
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    return isinstance(value, type(default))


def _validate(data, defaults, prefix=""):
    for key, default in defaults.items():
        value, name = data[key], prefix + key
        if not _kind_ok(value, default):
            raise ConfigError(f".baton/brief.md: {name} must be a {type(default).__name__}"
                              + (" of strings" if isinstance(default, list) else ""))
        if name == "labels.map":
            if not all(isinstance(item, str) for item in value.values()):
                raise ConfigError(".baton/brief.md: labels.map values must be strings")
        elif isinstance(default, dict) and default:
            _validate(value, default, name + ".")


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
    try:
        meta, _ = frontmatter.split(brief.read_text())
        data = frontmatter.parse(meta)
    except ValueError as error:
        raise ConfigError(f".baton/brief.md: {error}") from error
    merged = _merge(copy.deepcopy(DEFAULTS), data)
    _validate(merged, DEFAULTS)
    return Config(root=root, data=merged)
