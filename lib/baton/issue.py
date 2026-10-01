"""Read the parts of an issue description that Baton relies on.

The description is the single source of truth (design spec, section 4):
headed sections, an `Expected files:` line and `Blocked by:` lines.
"""
import re

_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.M)
_BLOCKED = re.compile(r"^\s*Blocked by:(.*)$", re.M | re.I)
_FILES = re.compile(r"^\s*Expected files:(.*)$", re.M | re.I)
_CHECKBOX = re.compile(r"^\s*[-*]\s+\[[ xX]\]\s+\S", re.M)


def sections(body):
    """Map each lower-cased heading to the text under it."""
    body = body or ""
    found = list(_HEADING.finditer(body))
    result = {}
    for index, match in enumerate(found):
        end = found[index + 1].start() if index + 1 < len(found) else len(body)
        result[match.group(1).lower()] = body[match.end():end].strip()
    return result


def blocked_by(body):
    numbers = []
    for line in _BLOCKED.findall(body or ""):
        numbers.extend(int(n) for n in re.findall(r"#(\d+)", line))
    return numbers


def expected_files(body):
    files = []
    for line in _FILES.findall(body or ""):
        files.extend(part.strip().strip("`") for part in line.split(",") if part.strip().strip("`"))
    return files


def ready_problems(issue, cfg, owner):
    """Why this issue does not meet the definition of ready; empty when it does."""
    body = issue.get("body") or ""
    parts = sections(body)
    labels = {label["name"] for label in issue.get("labels") or []}
    spike = cfg.label("type:spike") in labels
    problems = []
    if issue.get("author", {}).get("login") != owner:
        problems.append("written by someone other than the maintainer; "
                        "only the maintainer may mark it ready")
    if not parts.get("context"):
        problems.append("Context section is missing or empty (say why the work matters)")
    if not _CHECKBOX.search(parts.get("acceptance criteria", "")):
        problems.append("Acceptance criteria section needs at least one checkbox")
    if "out of scope" not in parts:
        problems.append("Out of scope section is missing")
    if not parts.get("verification"):
        problems.append("Verification section is missing or empty "
                        "(the exact command, or 'repo default')")
    if not spike and not expected_files(body):
        problems.append("Expected files line is missing (used to avoid overlapping work)")
    if not any(name.startswith("type:") or name == cfg.label("type:spike") for name in labels):
        problems.append("a type label is missing (type:feature, type:bug, "
                        "type:maintenance or type:spike)")
    return problems
