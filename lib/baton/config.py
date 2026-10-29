import copy
import datetime
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
    "needs-maintainer", "awaiting-feedback", "missed-sprint",
    "closed:spam", "closed:duplicate", "closed:support", "closed:wont-do",
]
PRIORITIES = [f"priority:{n}" for n in range(1, 5)]
SEVERITIES = [f"severity:{n}" for n in range(1, 5)]
EFFORTS = ("low", "medium", "high", "xhigh", "max")
MODELS = ("opus", "sonnet", "fable", "haiku")
FOLLOW_UP_SEVERITIES = ("critical", "important", "minor")
PLACEMENTS = ("deliverable", "stretch", "goal", "backlog", "skip")
ROLES = ("triager", "refiner", "researcher", "developer", "reviewer")
DEFAULTS = {
    "triager": {"model": "haiku", "effort": "medium"},
    "refiner": {"model": "opus", "effort": "medium"},
    "researcher": {"model": "opus", "effort": "medium"},
    "developer": {"model": "sonnet", "max_parallel": 1, "effort": "medium"},
    "reviewer": {"model": "opus", "effort": "medium"},
    "review": {"required": True, "max_rounds": 2},
    "refine": {"may_mark_ready": True},
    "follow_ups": {
        "critical": "deliverable", "important": "goal", "minor": "skip",
        "scheduled": {"critical": "deliverable", "important": "backlog"},
    },
    "labels": {
        "auto_create": True,
        "map": {name: name for name in STATUSES + OTHER_LABELS + PRIORITIES + SEVERITIES},
    },
    "merge": {"allow_no_ci": False},
    "verify": {"command": "", "timeout_minutes": 10},
    "docs": {"dir": "docs/baton"},
    "notify": {"severity": [], "labels": []},
    "triage": {"since": "", "max_per_run": 10, "stale_days": 14},
    "maintainers": [],
    "protected_paths": [".github/", ".baton/", ".claude/"],
    "fixtures_paths": [],
    "privacy_allow": [],
    "forge_clis": ["gh", "glab", "tea", "fj", "curl", "wget", "http", "https", "xh"],
    "body_file_flags": ["-F", "--body-file", "--input"],
    "forbid_added": {},
    "budgets": {},
    "work_mix": {},
    "spike": {"tokens": {}, "minutes": {}},
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
        if name == "notify.severity":
            if not (isinstance(value, list) and all(
                    isinstance(item, int) and not isinstance(item, bool) and 1 <= item <= 4 for item in value)):
                raise ConfigError(".baton/brief.md: notify.severity must be a list of integers from 1 to 4")
            continue
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


def _check_triage_since(data):
    since = data["triage"]["since"]
    if since:
        try:
            datetime.datetime.fromisoformat(since[:-1] + "+00:00" if since.endswith("Z") else since)
        except ValueError as error:
            raise ConfigError(".baton/brief.md: triage.since must be an ISO 8601 time") from error


def _check_forge(data):
    for key in ("forge_clis", "body_file_flags"):
        for entry in data[key]:
            if not entry or re.search(r"\s", entry) or (key == "body_file_flags" and not entry.startswith("-")):
                raise ConfigError(f".baton/brief.md: {key} entries must be non-empty words without spaces"
                                  + (" that start with -" if key == "body_file_flags" else ""))


def _check_follow_ups(data):
    follow_ups = data["follow_ups"]
    for name, mapping in (("follow_ups", follow_ups), ("follow_ups.scheduled", follow_ups["scheduled"])):
        allowed = FOLLOW_UP_SEVERITIES + (("scheduled",) if name == "follow_ups" else ())
        for key, value in mapping.items():
            if key not in allowed:
                raise ConfigError(f".baton/brief.md: {name}.{key} is not allowed; use " + ", ".join(allowed))
            if key != "scheduled" and value not in PLACEMENTS:
                raise ConfigError(f".baton/brief.md: {name}.{key} must be one of " + ", ".join(PLACEMENTS))
    for severity in FOLLOW_UP_SEVERITIES:
        follow_ups["scheduled"].setdefault(severity, follow_ups[severity])


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


WORK_MIX_KEYS = ("feature", "maintenance", "bug")


def _check_work_mix(data):
    mix = data["work_mix"]
    for key, value in mix.items():
        if key not in WORK_MIX_KEYS:
            raise ConfigError(f".baton/brief.md: work_mix key {key!r} must be one of " + ", ".join(WORK_MIX_KEYS))
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
            raise ConfigError(f".baton/brief.md: work_mix.{key} must be an integer from 0 to 100")
    if mix and sum(mix.values()) != 100:
        raise ConfigError(".baton/brief.md: work_mix values must add up to 100")


def _check_spike(data):
    for part, mapping in data["spike"].items():
        for key, value in mapping.items():
            if key not in ("min", "max"):
                raise ConfigError(f".baton/brief.md: spike.{part} key {key!r} must be min or max")
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ConfigError(f".baton/brief.md: spike.{part}.{key} must be a positive integer")
        if "min" in mapping and "max" in mapping and mapping["min"] > mapping["max"]:
            raise ConfigError(f".baton/brief.md: spike.{part}.min must not be greater than max")


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
    for key in ("max_per_run", "stale_days"):
        if merged["triage"][key] < 1:
            raise ConfigError(f".baton/brief.md: triage.{key} must be at least 1")
    _check_triage_since(merged)
    _check_follow_ups(merged)
    _check_forbid_added(merged)
    _check_spike(merged)
    _check_docs_dir(merged)
    _check_forge(merged)
    _check_work_mix(merged)
    return Config(root=root, data=merged)
