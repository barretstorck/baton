"""Read the parts of an issue description that Baton relies on.

The description is the single source of truth (design spec, section 4):
headed sections, an `Expected files:` line and `Blocked by:` lines.
"""
import re

from . import checklist, gate, planfile
from .config import has_login, logins

_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.M)
_BLOCKED = re.compile(r"^\s*Blocked by:(.*)$", re.M | re.I)
_NO_BLOCKERS = re.compile(r"^\(?none", re.I)
_FILES = re.compile(r"^\s*Expected files:(.*)$", re.M | re.I)
_TIMEBOX = re.compile(r"^\s*Timebox:(.*)$", re.M | re.I)
_TIMEBOX_PART = re.compile(r"^(\d+)\s*(tokens|min)$", re.I)
_ORIGIN_OUTSIDE = re.compile(r"^\s*Origin:\s*outside\s*\(@[\w-]+\)\s*$", re.M | re.I)
APPROVAL = "/baton ready"
_LIST_ITEM = re.compile(r"^\s*(?:[-*]|\d+\.)\s+\S", re.M)


def sections(body):
    """Map each lower-cased heading to the text under it."""
    body = body or ""
    found = list(_HEADING.finditer(body))
    result = {}
    for index, match in enumerate(found):
        end = found[index + 1].start() if index + 1 < len(found) else len(body)
        result[match.group(1).lower()] = body[match.end():end].strip()
    return result


def add_missed_line(body, title, url):
    """Add `Missed sprint: [title](url)` as the last line of `## Pointers`.

    Earlier lines stay. A body without a Pointers section gets one at the end.
    """
    body = body or ""
    line = f"Missed sprint: [{title}]({url})"
    found = list(_HEADING.finditer(body))
    for index, match in enumerate(found):
        if match.group(1).lower() != "pointers":
            continue
        end = found[index + 1].start() if index + 1 < len(found) else len(body)
        text = body[match.end():end].rstrip()
        rest = body[end:]
        gap = "\n\n" if rest else "\n"
        return body[:match.end()] + text + "\n" + line + gap + rest
    return body.rstrip("\n") + ("\n\n" if body.strip() else "") + "## Pointers\n" + line + "\n"


def blocked_by(body):
    numbers = []
    for line in _BLOCKED.findall(body or ""):
        if _NO_BLOCKERS.match(line.strip()):
            continue
        numbers.extend(int(n) for n in re.findall(r"#(\d+)", line))
    return numbers


def expected_files(body):
    files = []
    for line in _FILES.findall(body or ""):
        files.extend(part.strip().strip("`") for part in line.split(",") if part.strip().strip("`"))
    return files


def timebox(body):
    """The spike's `Timebox:` line as {"tokens": int | None, "minutes": int | None}, or None.

    Raises ValueError when the line is not `<integer> tokens`, `<integer> min` or both, comma-separated.
    """
    found = _TIMEBOX.search(body or "")
    if not found:
        return None
    line = found.group(0).strip()
    result = {"tokens": None, "minutes": None}
    for part in found.group(1).split(","):
        match = _TIMEBOX_PART.match(part.strip())
        key = None if not match else ("tokens" if match.group(2).lower() == "tokens" else "minutes")
        if key is None or result[key] is not None:
            raise ValueError(f"cannot read the line {line!r} "
                             "(write 'Timebox: <tokens> tokens, <minutes> min', either part optional)")
        result[key] = int(match.group(1))
    return result


def is_outside(issue, maintainers, bots=()):
    """True when the author is neither a maintainer nor a Baton login, or the body has an
    `Origin: outside (@login)` line."""
    author = issue.get("author")
    known = has_login(author, maintainers) or has_login(author, list(bots))
    return not known or bool(_ORIGIN_OUTSIDE.search(issue.get("body") or ""))


def approved(issue, maintainers, bots=()):
    """True when a maintainer (never a Baton login) wrote an unmarked comment that is exactly `/baton ready`."""
    for comment in issue.get("comments") or []:
        body = comment.get("body") or ""
        author = comment.get("author")
        if (has_login(author, maintainers) and not has_login(author, list(bots))
                and gate.MARKER not in body and body.strip() == APPROVAL):
            return True
    return False


def ready_problems(issue, cfg, owner, runner=None, bots=()):
    """Why this issue does not meet the definition of ready; empty when it does.

    A non-spike with a `Plan:` link has its Verification and plan read from the linked file
    (through `runner`); a plan on `refs/plans/<N>` is reported as needing `baton plan migrate`.
    A legacy body plan is read from the body.
    """
    body = issue.get("body") or ""
    parts = sections(body)
    labels = {label["name"] for label in issue.get("labels") or []}
    spike = cfg.label("type:spike") in labels
    problems = []
    if is_outside(issue, owner, bots):
        if not approved(issue, owner, bots):
            problems.append(f"outside issue: it needs a comment '{APPROVAL}' from a maintainer")
    if not parts.get("context"):
        problems.append("Context section is missing or empty (say why the work matters)")
    if not _LIST_ITEM.search(parts.get("acceptance criteria", "")):
        problems.append("Acceptance criteria section needs at least one list item")
    if "out of scope" not in parts:
        problems.append("Out of scope section is missing")
    try:
        link = None if spike else planfile.parse_link(body, cfg.data["docs"]["dir"])
    except checklist.TaskError as error:
        link = None
        problems.append(str(error))
    plan_text, plan_where = body, "Plan section"
    if link:
        plan_where = link["path"]
        try:
            if link["kind"] == "ref":
                number = link["ref"].rsplit("/", 1)[-1]
                plan_text = None
                problems.append(f"the plan is on refs/plans/{number}; "
                                f"run 'baton plan migrate {number}'")
            else:
                if runner is None:
                    raise checklist.TaskError("no way to read it")
                plan_text = planfile.read(runner, link["ref"], link["path"])
        except checklist.TaskError as error:
            plan_text = None
            problems.append(f"the Plan: link cannot be read ({error})")
        if checklist.span(body, "plan") is not None:
            problems.append("the description has both a Plan: line and a '## Plan' section "
                            "(keep only the linked file)")
    verification = sections(plan_text).get("verification") if link and plan_text is not None \
        else parts.get("verification")
    if not verification and not (link and plan_text is None):
        problems.append("Verification section is missing or empty "
                        "(the exact command, or 'repo default')"
                        + (f" in {plan_where}" if link else ""))
    if not spike and not expected_files(body):
        problems.append("Expected files line is missing (used to show where issues may conflict)")
    if not spike:
        found = checklist.plan(plan_text) if plan_text is not None else None
        if plan_text is None:
            pass  # already reported: the file could not be read
        elif found is None or not found["tasks"]:
            problems.append("Plan section with at least one task is missing"
                            + (f" in {plan_where}" if link else
                               " and no 'Plan:' link to a plan file")
                            + " (- [ ] Task 1: <title> followed by an indented <details> block "
                            "with Files, Uses, Provides and Check lines)")
        else:
            if not link:
                problems.extend(checklist.layout_problems(plan_text))
            if not found["written_against"]:
                problems.append("the plan needs a 'Written against: <full commit sha>' line")
            ids = [task["id"] for task in found["tasks"]]
            if ids != [str(n) for n in range(1, len(ids) + 1)]:
                problems.append("plan tasks must be numbered 1, 2, 3 ... in order")
    if spike:
        problems.extend(_timebox_problems(body, cfg))
    if not any(name.startswith("type:") or name == cfg.label("type:spike") for name in labels):
        problems.append("a type label is missing (type:feature, type:bug, "
                        "type:maintenance or type:spike)")
    return problems


UNTRUSTED = "[untrusted text from @{author} withheld; see Triage summary]"
_KEPT_OUTSIDE = ("triage summary", "developer plan")
_TITLE_MAX = 120


def _login(person):
    return (person or {}).get("login") or "ghost"


def _clean_title(title):
    text = "".join(" " if ch in "\r\n\t" else ch for ch in (title or ""))
    text = "".join(ch for ch in text if ch.isprintable())
    return text[:_TITLE_MAX]


def _kept_sections(body):
    """The `Triage summary` and `Developer Plan` sections of `body`, with their headings."""
    body = body or ""
    found = list(_HEADING.finditer(body))
    kept = []
    for index, match in enumerate(found):
        if match.group(1).lower() not in _KEPT_OUTSIDE:
            continue
        level = len(match.group(0)) - len(match.group(0).lstrip("#"))
        end = len(body)
        for later in found[index + 1:]:
            if len(later.group(0)) - len(later.group(0).lstrip("#")) <= level:
                end = later.start()
                break
        kept.append(body[match.start():end].strip())
    return kept


def render(data, maintainers, bots=()):
    """The issue as text for an agent, with outsider-written text withheld.

    A maintainer's text is printed in full. On an issue by anyone else the body is replaced
    by a placeholder, keeping only the Triage summary and Developer Plan sections; a comment
    by anyone else is replaced by the placeholder. A Baton login (`bots`) counts as a
    maintainer for its issues (unless the body has an `Origin: outside` line) and for comments
    that start with the Baton marker.
    """
    allowed = {name.lower() for name in logins(maintainers)}
    author = _login(data.get("author"))
    trusted = has_login(data.get("author"), maintainers) or (
        has_login(data.get("author"), list(bots)) and not _ORIGIN_OUTSIDE.search(data.get("body") or ""))
    labels = ", ".join(label["name"] for label in data.get("labels") or []) or "none"
    lines = [
        f"#{data.get('number')} {_clean_title(data.get('title'))}",
        f"State: {data.get('state')}",
        f"Author: @{author} ({'maintainer' if trusted else 'outside'})",
        f"Labels: {labels}",
        "--- body ---",
    ]
    if trusted:
        lines.append((data.get("body") or "").strip())
    else:
        lines.append(UNTRUSTED.format(author=author))
        for text in _kept_sections(data.get("body")):
            lines.extend(["", text])
    lines.append("--- comments ---")
    for comment in data.get("comments") or []:
        who = _login(comment.get("author"))
        lines.append(f"--- comment by @{who} ---")
        text = (comment.get("body") or "").strip()
        marked = text.split("\n", 1)[0].startswith(gate.MARKER)
        if who.lower() in allowed or (has_login(comment.get("author"), list(bots)) and marked):
            lines.append(text)
        else:
            lines.append(UNTRUSTED.format(author=who))
    return "\n".join(lines) + "\n"


def _timebox_problems(body, cfg):
    try:
        box = timebox(body)
    except ValueError as error:
        return [f"Timebox: {error}"]
    if box is None:
        return []
    problems = []
    for part, unit in (("tokens", "tokens"), ("minutes", "min")):
        limits = cfg.data.get("spike", {}).get(part) or {}
        value = box[part]
        low, high = limits.get("min"), limits.get("max")
        if value is None:
            if high is not None:
                problems.append(f"Timebox: {part} is left out but the brief sets spike.{part}.max "
                                f"= {high} (no limit exceeds the maximum)")
        elif (low is not None and value < low) or (high is not None and value > high):
            bounds = f"{low if low is not None else '-'} to {high if high is not None else '-'}"
            problems.append(f"Timebox: {part} {value} {unit} is outside the brief's range ({bounds})")
    return problems
