"""Tier 1 identities: GitHub App entries read from ~/.baton/<owner>-<repo>/identity.toml."""
import tomllib
from dataclasses import dataclass

from . import verify

NAMES = ("baton", "reviewer")
DEFAULT_LOGINS = {"baton": "baton[bot]", "reviewer": "baton-reviewer[bot]"}


class IdentityError(Exception):
    pass


@dataclass
class App:
    app_id: int
    installation_id: int
    key_path: str
    login: str


@dataclass
class Identities:
    baton: "App | None" = None
    reviewer: "App | None" = None

    def logins(self):
        return [app.login for app in (self.baton, self.reviewer) if app is not None]


def _int(name, key, value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise IdentityError(f"identity.toml [{name}] {key} must be an integer")
    return value


def _app(name, table):
    if not isinstance(table, dict):
        raise IdentityError(f"identity.toml [{name}] must be a table")
    for key in ("app_id", "installation_id", "key_path"):
        if key not in table:
            raise IdentityError(f"identity.toml [{name}] is missing {key}")
    key_path = table["key_path"]
    if not isinstance(key_path, str) or not key_path:
        raise IdentityError(f"identity.toml [{name}] key_path must be a non-empty string")
    login = table.get("login", DEFAULT_LOGINS[name])
    if not isinstance(login, str) or not login:
        raise IdentityError(f"identity.toml [{name}] login must be a non-empty string")
    return App(_int(name, "app_id", table["app_id"]),
               _int(name, "installation_id", table["installation_id"]), key_path, login)


def load(owner, repo, home=None):
    path = verify._home(home) / f"{owner}-{repo}" / "identity.toml"
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise IdentityError(f"identity.toml is not valid TOML: {e}")
    unknown = sorted(set(data) - set(NAMES))
    if unknown:
        raise IdentityError(f"identity.toml has unknown entries: {', '.join(unknown)}")
    return Identities(**{name: _app(name, data[name]) for name in NAMES if name in data})
