"""Task checklists: an issue's plan and a review's fix list.

Design spec, sections 4 ("The plan"), 6 and 7a. Only Baton's commands tick a box
or replace open tasks; agents never edit a checklist by hand.
"""
import json
import os
import re
import shutil
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


def add_as_built(text, heading, task_id, note):
    """`text` with a `Provides (as built): <note>` line under task `task_id`'s `Provides:` line.

    An existing as-built line of that task is replaced; the original `Provides:` line stays.
    """
    where = span(text, heading)
    if where is None:
        raise TaskError(f"no '{heading}' section")
    start, end = where
    lines = text[start:end].splitlines(keepends=True)
    found = False
    for index, line in enumerate(lines):
        match = _match(line)
        if match:
            if found:
                break
            found = match.group(2) == task_id
        elif found and "Provides:" in line and "(as built)" not in line:
            indent = line[:len(line) - len(line.lstrip())]
            bullet = "- " if line.lstrip().startswith("- ") else ""
            new = f"{indent}{bullet}Provides (as built): {note.strip()}\n"
            after = index + 1
            if after < len(lines) and "Provides (as built):" in lines[after]:
                lines[after] = new
            else:
                lines.insert(after, new)
            return text[:start] + "".join(lines) + text[end:]
    if not found:
        raise TaskError(f"no task {task_id} under '{heading}'")
    raise TaskError(f"task {task_id} has no 'Provides:' line to add an as-built line under")


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


def _title(block):
    match = _match(block["lines"][0])
    return match.group(3) if match else ""


def plan_changes(text, from_id, new_text):
    """(replaced, written): `(id, title)` of the tasks from `from_id` on, and of the new ones."""
    where = span(text, "plan")
    if where is None:
        raise TaskError("the issue has no '## Plan' section")
    if not str(from_id).isdigit():
        raise TaskError("plan tasks are numbered 1, 2, 3 ...")
    _, blocks = _blocks(text[where[0]:where[1]])
    _, new_blocks = _blocks(new_text.replace("\r\n", "\n"))
    replaced = [(b["id"], _title(b)) for b in blocks
                if b["id"].isdigit() and int(b["id"]) >= int(from_id)]
    return replaced, [(b["id"], _title(b)) for b in new_blocks]


def _describe(tasks):
    return ", ".join(f"{task_id} ({title})" for task_id, title in tasks)


def replace_plan(body, from_id, new_text, drop=False):
    """`body` with the plan's tasks from `from_id` on replaced by `new_text`'s tasks.

    Refused when the new text has fewer tasks than it replaces, unless `drop`: the tasks
    that no longer exist would otherwise vanish silently.
    """
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
    replaced, _ = plan_changes(body, from_id, new_text)
    dropped = replaced[len(new_blocks):]
    if dropped and not drop:
        raise TaskError(f"the new text has {len(new_blocks)} task(s) for {len(replaced)} replaced; "
                        f"it would drop task {_describe(dropped)}; "
                        "write them again or pass --drop to drop them")
    kept = [line for block in blocks if int(block["id"]) < first for line in block["lines"]]
    if kept and not kept[-1].strip():
        kept = kept[:-1]
    section = "".join(head + kept + [line for block in new_blocks for line in block["lines"]])
    if end < len(body):
        section += newline
    return body[:start] + section + body[end:]


def issue_body(runner, number):
    return json.loads(runner(["gh", "issue", "view", str(number), "--json", "body"]))["body"] or ""


def plan_source(runner, body, cache=None, docs_dir=None):
    """(text holding the plan, link or None): the linked file's text, else the body itself.

    `cache` maps (ref, path) to text so one branch is fetched once.
    """
    from . import planfile  # planfile imports this module
    link = planfile.parse_link(body, docs_dir)
    if link is None:
        return body, None
    key = (link["ref"], link["path"])
    if cache is not None and key in cache:
        return cache[key], link
    text = planfile.read(runner, link["ref"], link["path"])
    if cache is not None:
        cache[key] = text
    return text, link


def issue_plan(runner, number, docs_dir=None):
    from . import planfile
    text, link = plan_source(runner, issue_body(runner, number), docs_dir=docs_dir)
    found = plan(text)
    if found is None:
        raise TaskError(f"issue #{number} has no '## Plan' section"
                        + (f" in {link['path']}" if link else ""))
    found["source"] = "file" if link else "body"
    if link:
        found["ref"], found["path"] = link["ref"], link["path"]
        if link["kind"] == "ref":
            ticked = planfile.tick_commits(runner, link["ref"], link["path"])
            for task in found["tasks"]:
                if task["done"] and task["id"] in ticked:
                    task["ticked_by"] = ticked[task["id"]]
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


def _linked(runner, number, docs_dir=None):
    """(body, link or None) for the issue; a pinned link has no file left to edit."""
    from . import planfile
    body = issue_body(runner, number)
    link = planfile.parse_link(body, docs_dir)
    if link is not None and link["pinned"]:
        raise TaskError(f"issue #{number}'s plan is pinned to a commit; its file was retired")
    return body, link


def tick_issue(runner, number, task_id, cwd=".", docs_dir=None, as_built=None):
    """Tick a plan task. False when it was already ticked (nothing is written).

    A plan at `refs/plans/<N>` is ticked by a new commit on that ref (with `as_built`, a
    `Provides (as built):` line is added in the same commit); the commit is not pushed, the
    developer pushes it atomically with the task's code. A plan file on a branch is ticked in
    its file under `cwd` (the developer's checkout) and the issue is not edited; the developer
    commits the file with the task's code. A plan in the issue body is ticked in the issue.
    """
    body, link = _linked(runner, number, docs_dir)
    if link is not None and link["kind"] == "ref":
        from . import planfile
        text = planfile.read(runner, link["ref"], link["path"])
        new, changed = tick(text, "plan", str(task_id))
        if not changed:
            return False
        if as_built:
            new = add_as_built(new, "plan", str(task_id), as_built)
        sha = planfile.write(runner, number, link["path"], new, f"Task {task_id}")
        print(f"plan commit {sha}")
        print(f"push with: git push --atomic origin HEAD:refs/heads/<branch> "
              f"{sha}:{link['ref']}")
        return True
    if as_built:
        raise TaskError("--as-built needs a plan kept at refs/plans/<N>; "
                        "edit the plan file or the issue by hand for this plan")
    if link is None:
        new, changed = tick(body, "plan", str(task_id))
        if changed:
            _save_issue(runner, number, new)
        return changed
    from . import planfile
    planfile.check_write(cwd, link["path"])
    path = os.path.join(cwd, link["path"])
    try:
        with open(path, newline="") as handle:
            text = handle.read()
    except OSError as error:
        raise TaskError(f"plan file {link['path']} not found in {cwd}: {error}")
    new, changed = tick(text, "plan", str(task_id))
    if changed:
        with open(path, "w", newline="") as handle:
            handle.write(new)
        print(f"ticked in {link['path']}; commit it with the task's code")
    return changed


def _replace_in_file(runner, number, link, from_id, new_text, drop=False):
    """Commit the replaced tasks to the plan ref, or to the linked branch from a temporary
    detached worktree."""
    from . import planfile
    branch, path = link["ref"], link["path"]
    if link["kind"] == "ref":
        text = planfile.read(runner, branch, path)
        changes = plan_changes(text, from_id, new_text)
        new = replace_plan(text, str(from_id), new_text, drop)
        dropped = changes[0][len(changes[1]):]
        message = f"Replan from task {from_id}"
        if dropped:
            message += f"\n\nDropped: {_describe(dropped)}"
        planfile.push(runner, number, planfile.write(runner, number, path, new, message))
        return new, changes
    planfile.check_branch(number, branch)
    runner(["git", "fetch", "--quiet", "--end-of-options", "origin", branch])
    tree = tempfile.mkdtemp(prefix="baton-plan-")
    try:
        runner(["git", "worktree", "add", "--detach", tree, f"origin/{branch}"])
        try:
            target = os.path.join(tree, path)
            planfile.check_write(tree, path)
            with open(target, newline="") as handle:
                text = handle.read()
            changes = plan_changes(text, from_id, new_text)
            new = replace_plan(text, str(from_id), new_text, drop)
            dropped = changes[0][len(changes[1]):]
            message = f"Replan from task {from_id}"
            if dropped:
                message += f"\n\nDropped: {_describe(dropped)}"
            with open(target, "w", newline="") as handle:
                handle.write(new)
            runner(["git", "add", "--", path], cwd=tree)
            runner(["git", "commit", "-q", "-m", message], cwd=tree)
            runner(["git", "push", "origin", f"HEAD:{branch}"], cwd=tree)
        finally:
            runner(["git", "worktree", "remove", "--force", tree])
    finally:
        if os.path.isdir(tree):
            shutil.rmtree(tree, ignore_errors=True)
    return new, changes


def replace_issue_plan(cfg, runner, number, from_id, new_text, terms, drop=False):
    """Replace the plan's open tasks from `from_id` on; the new text is scanned first."""
    findings = scan.scan_text(new_text, terms, cfg.data["privacy_allow"])
    if findings:
        raise TaskError("plan not changed; privacy scan found: "
                        + "; ".join(scan.describe(findings)))
    body, link = _linked(runner, number, cfg.data["docs"]["dir"])
    if link is not None:
        new, changes = _replace_in_file(runner, number, link, from_id, new_text, drop)
    else:
        changes = plan_changes(body, from_id, new_text)
        new = replace_plan(body, str(from_id), new_text, drop)
        _save_issue(runner, number, new)
    found = plan(new)
    found["replaced"], found["written"] = changes
    found["dropped"] = changes[0][len(changes[1]):]
    return found
