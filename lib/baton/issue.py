"""Read the parts of an issue description that Baton relies on.

The description is the single source of truth (design spec, section 4):
headed sections, an `Expected files:` line and `Blocked by:` lines.
"""
import re

from . import checklist, planfile

_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.M)
_BLOCKED = re.compile(r"^\s*Blocked by:(.*)$", re.M | re.I)
_NO_BLOCKERS = re.compile(r"^\(?none", re.I)
_FILES = re.compile(r"^\s*Expected files:(.*)$", re.M | re.I)
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


def ready_problems(issue, cfg, owner, runner=None):
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
    if issue.get("author", {}).get("login") != owner:
        problems.append("written by someone other than the maintainer; "
                        "only the maintainer may mark it ready")
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
    if not any(name.startswith("type:") or name == cfg.label("type:spike") for name in labels):
        problems.append("a type label is missing (type:feature, type:bug, "
                        "type:maintenance or type:spike)")
    return problems
