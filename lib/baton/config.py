import copy
import pathlib
import re
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
PRIORITIES = [f"priority:{n}" for n in range(1, 5)]
SEVERITIES = [f"severity:{n}" for n in range(1, 5)]
EFFORTS = ("low", "medium", "high", "xhigh", "max")
MODELS = ("opus", "sonnet", "fable", "haiku")
ROLES = ("triager", "refiner", "researcher", "developer", "reviewer")
DEFAULTS = {
    "triager": {"model": "haiku", "effort": "medium"},
    "refiner": {"model": "opus", "effort": "medium"},
    "researcher": {"model": "opus", "effort": "medium"},
    "developer": {"model": "sonnet", "max_parallel": 1, "effort": "medium"},
    "reviewer": {"model": "opus", "effort": "medium"},
    "review": {"required": True, "max_rounds": 2},
    "refine": {"may_mark_ready": True},
    "labels": {
        "auto_create": True,
        "map": {name: name for name in STATUSES + OTHER_LABELS + PRIORITIES + SEVERITIES},
    },
    "merge": {"allow_no_ci": False},
    "verify": {"command": "", "timeout_minutes": 10},
    "docs": {"dir": "docs/baton"},
    "maintainers": [],
    "protected_paths": [".github/", ".baton/", ".claude/"],
    "fixtures_paths": [],
    "privacy_allow": [],
    "forbid_added": {},
    "budgets": {},
}


class ConfigError(Exception):
    pass


def logins(value):
    """A login or an iterable of logins as a frozenset."""
    if isinstance(value, str):
        return frozenset([value])
    return frozenset(value or ())


@dataclass
class Config:
    root: pathlib.Path
    data: dict

    def label(self, name):
        return self.data["labels"]["map"].get(name, name)

    def maintainers(self, owner):
        return list(self.data["maintainers"]) or [owner]

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


def _check_forbid_added(data):
    for glob, pattern in data["forbid_added"].items():
        if not isinstance(pattern, str):
            raise ConfigError(f".baton/brief.md: forbid_added[{glob!r}] must be a regular expression string")
        try:
            re.compile(pattern)
        except re.error as error:
            raise ConfigError(f".baton/brief.md: forbid_added[{glob!r}] is not a valid regular expression: {error}") from error


def _check_docs_dir(data):
    docs_dir = data["docs"]["dir"]
    path = pathlib.PurePosixPath(docs_dir)
    if not docs_dir.strip() or path.is_absolute() or pathlib.PureWindowsPath(docs_dir).is_absolute():
        raise ConfigError(".baton/brief.md: docs.dir must be a relative path inside the repository")
    if ".." in path.parts:
        raise ConfigError(".baton/brief.md: docs.dir must not contain '..'")
    parts = [part for part in path.parts if part != "."]
    if not parts:
        raise ConfigError(".baton/brief.md: docs.dir must name a folder, not the repository root")
    data["docs"]["dir"] = "/".join(parts)
    norm = data["docs"]["dir"] + "/"
    for entry in data["protected_paths"]:
        prefix = "/".join(part for part in pathlib.PurePosixPath(entry).parts if part != ".")
        if prefix and (norm + "/").startswith(prefix.rstrip("/") + "/") and (
                entry.endswith("/") or norm.rstrip("/") == prefix):
            raise ConfigError(f".baton/brief.md: docs.dir {docs_dir} is under protected path {entry}")


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
    for role in ROLES:
        if merged[role]["effort"] not in EFFORTS:
            raise ConfigError(f".baton/brief.md: {role}.effort must be one of " + ", ".join(EFFORTS))
        if merged[role]["model"] not in MODELS:
            raise ConfigError(f".baton/brief.md: {role}.model must be one of " + ", ".join(MODELS))
    if merged["review"]["max_rounds"] < 1:
        raise ConfigError(".baton/brief.md: review.max_rounds must be at least 1")
    if merged["verify"]["timeout_minutes"] < 1:
        raise ConfigError(".baton/brief.md: verify.timeout_minutes must be at least 1")
    _check_forbid_added(merged)
    _check_docs_dir(merged)
    return Config(root=root, data=merged)
