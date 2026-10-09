"""Task checklists: an issue's plan and a review's fix list.

Design spec, sections 4 ("The plan"), 6 and 7a. Only Baton's commands tick a box
or replace open tasks; agents never edit a checklist by hand.
"""
import json
import os
import re
import tempfile

from . import scan

_HEADING = re.compile(r"(#{1,6})\s+(.+?)\s*$")
_TASK = re.compile(r"^[-*] \[([ xX])\] \*\*(\d+(?:\.\d+)?)\.?\s+(.+?)\*\*\s*$")
_AGAINST = re.compile(r"^\s*Written against:\s*([0-9a-f]{40})\b", re.M)


class TaskError(Exception):
    pass


def _headings(text):
    """(level, lower-cased name, line start, line end) for each heading outside code fences."""
    found, fenced, offset = [], False, 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fenced = not fenced
        elif not fenced:
            match = _HEADING.match(line.rstrip("\r\n"))
            if match:
                found.append((len(match.group(1)), match.group(2).lower(),
                              offset, offset + len(line)))
        offset += len(line)
    return found


def span(text, heading):
    """(start, end) of the text under `heading`, up to the next heading of its level or higher."""
    text = text or ""
    found = _headings(text)
    for index, (level, name, _, line_end) in enumerate(found):
        if name == heading.lower():
            end = next((start for lvl, _, start, _ in found[index + 1:] if lvl <= level),
                       len(text))
            return line_end, end
    return None


def _match(line):
    return _TASK.match(line.rstrip("\r\n"))


def parse(section):
    tasks = []
    for line in (section or "").splitlines():
        match = _match(line)
        if match:
            tasks.append({"id": match.group(2), "title": match.group(3),
                          "done": match.group(1) != " "})
    return tasks


def tasks_under(text, heading):
    where = span(text, heading)
    return parse(text[where[0]:where[1]]) if where else []


def progress(tasks):
    return {"done": sum(1 for task in tasks if task["done"]), "total": len(tasks),
            "next": next((task["id"] for task in tasks if not task["done"]), None)}


def plan(body):
    where = span(body, "plan")
    if where is None:
        return None
    section = body[where[0]:where[1]]
    against = _AGAINST.search(section)
    tasks = parse(section)
    return {"written_against": against.group(1) if against else None, "tasks": tasks,
            "next": progress(tasks)["next"]}


def tick(text, heading, task_id):
    """`text` with task `task_id` ticked under `heading`, and whether anything changed."""
    where = span(text, heading)
    if where is None:
        raise TaskError(f"no '{heading}' section")
    start, end = where
    lines = text[start:end].splitlines(keepends=True)
    for index, line in enumerate(lines):
        match = _match(line)
        if match and match.group(2) == task_id:
            if match.group(1) != " ":
                return text, False
            lines[index] = line.replace("[ ]", "[x]", 1)
            return text[:start] + "".join(lines) + text[end:], True
    raise TaskError(f"no task {task_id} under '{heading}'")


def _blocks(section):
    head, blocks = [], []
    for line in section.splitlines(keepends=True):
        match = _match(line)
        if match:
            blocks.append({"id": match.group(2), "done": match.group(1) != " ", "lines": [line]})
        elif blocks:
            blocks[-1]["lines"].append(line)
        else:
            head.append(line)
    return head, blocks


def replace_plan(body, from_id, new_text):
    """`body` with the plan's tasks from `from_id` on replaced by `new_text`'s tasks."""
    where = span(body, "plan")
    if where is None:
        raise TaskError("the issue has no '## Plan' section")
    if not str(from_id).isdigit():
        raise TaskError("plan tasks are numbered 1, 2, 3 ...")
    first = int(from_id)
    start, end = where
    head, blocks = _blocks(body[start:end])
    if any(not block["id"].isdigit() for block in blocks):
        raise TaskError("the plan has a task that is not numbered 1, 2, 3 ...")
    done = [block["id"] for block in blocks if int(block["id"]) >= first and block["done"]]
    if done:
        raise TaskError(f"task {done[0]} is done; replace from a later task")
    newline = "\r\n" if "\r\n" in body else "\n"
    new_text = new_text.replace("\r\n", "\n").strip("\n").replace("\n", newline) + newline
    new_head, new_blocks = _blocks(new_text)
    if any(line.strip() for line in new_head) or not new_blocks:
        raise TaskError("the new text must start with a task line: - [ ] **<n>. <title>**")
    for offset, block in enumerate(new_blocks):
        if block["done"] or block["id"] != str(first + offset):
            raise TaskError(f"the new tasks must be open and numbered from {first}")
    kept = [line for block in blocks if int(block["id"]) < first for line in block["lines"]]
    if kept and not kept[-1].strip():
        kept = kept[:-1]
    section = "".join(head + kept + [line for block in new_blocks for line in block["lines"]])
    if end < len(body):
        section += newline
    return body[:start] + section + body[end:]


def issue_body(runner, number):
    return json.loads(runner(["gh", "issue", "view", str(number), "--json", "body"]))["body"] or ""


def issue_plan(runner, number):
    found = plan(issue_body(runner, number))
    if found is None:
        raise TaskError(f"issue #{number} has no '## Plan' section")
    return found


def _save_issue(runner, number, body):
    if not body.strip():
        raise TaskError("refusing to write an empty description")
    handle, path = tempfile.mkstemp(suffix=".md")
    try:
        with os.fdopen(handle, "w", newline="") as out:
            out.write(body)
        runner(["gh", "issue", "edit", str(number), "--body-file", path])
    finally:
        os.unlink(path)


def tick_issue(runner, number, task_id):
    """Tick a plan task. False when it was already ticked (nothing is written)."""
    new, changed = tick(issue_body(runner, number), "plan", str(task_id))
    if changed:
        _save_issue(runner, number, new)
    return changed


def replace_issue_plan(cfg, runner, number, from_id, new_text, terms):
    """Replace the plan's open tasks from `from_id` on; the new text is scanned first."""
    findings = scan.scan_text(new_text, terms, cfg.data["privacy_allow"])
    if findings:
        raise TaskError("plan not changed; privacy scan found: "
                        + "; ".join(scan.describe(findings)))
    new = replace_plan(issue_body(runner, number), str(from_id), new_text)
    _save_issue(runner, number, new)
    return plan(new)
