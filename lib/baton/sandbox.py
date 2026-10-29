"""Sandbox command template from the local brief, and its rendering."""
import pathlib
import re
import shlex

from baton import verify


class SandboxError(Exception):
    pass


def parse_template(brief_text):
    """Return the `Command:` value of the `## Sandbox` section, or None."""
    text = re.sub(r"<!--.*?-->", "", brief_text, flags=re.S)
    in_section = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_section = line[3:].strip().lower() == "sandbox"
            continue
        if in_section:
            m = re.match(r"\s*Command:\s*(.*?)\s*$", line)
            if m and m.group(1):
                return m.group(1)
    return None


def render(template, checkout, command):
    for placeholder in ("{checkout}", "{command}"):
        if placeholder not in template:
            raise SandboxError(f"sandbox template lacks {placeholder}")
    return template.replace("{checkout}", shlex.quote(str(checkout))).replace(
        "{command}", shlex.quote(command))


def brief_path(owner, repo, home=None):
    return pathlib.Path(verify._home(home)) / f"{owner}-{repo}" / "brief.local.md"


def load_template(owner, repo, home=None):
    path = brief_path(owner, repo, home)
    if not path.is_file():
        return None
    return parse_template(path.read_text())
