"""Quota use per issue and role, read from the local Claude Code transcripts.

Design spec, section 7b. Every call re-sends the agent's whole conversation, so
the cost of an agent is its calls times its conversation size. Weights follow the
price ratios: cache read 0.1, cache write 1.25 and output 5 times an input token.
"""
import datetime
import json
import pathlib
import re

WEIGHTS = {"input_tokens": 1.0, "cache_read_input_tokens": 0.1,
           "cache_creation_input_tokens": 1.25, "output_tokens": 5.0}
LARGE = 250_000
WINDOW = datetime.timedelta(hours=5)
_PROMPTS = [
    ("fix", re.compile(r"Do review task \d+\.\d+ of pull request #?(\d+)(?: \(issue #?(\d+)\))?")),
    ("task", re.compile(r"Do task \d+ of issue #?(\d+)")),
    ("spike", re.compile(r"Do spike issue #?(\d+)")),
    ("review", re.compile(r"Review pull request #?(\d+)(?: \(issue #?(\d+)\))?")),
    ("refine", re.compile(r"Refine issues? (#?\d+(?:(?:,\s*|\s+and\s+)#?\d+)*)")),
    ("fix", re.compile(r"(?:Fix CI on|Update) pull request #?(\d+)(?: \(issue #?(\d+)\))?")),
    ("developer", re.compile(r"Implement issue #?(\d+)")),   # before task plans (0.2.0)
]
_TYPES = re.compile(r"baton-(developer|reviewer|refiner)")
_TYPE_ROLES = {"developer": "developer", "reviewer": "review", "refiner": "refine"}


def classify(prompt, agent_type=None):
    for role, pattern in _PROMPTS:
        match = pattern.search(prompt or "")
        if not match:
            continue
        if role in ("task", "spike", "developer"):
            return role, f"issue {match.group(1)}"
        if role in ("fix", "review"):
            return role, (f"issue {match.group(2)}" if match.group(2) else f"pr {match.group(1)}")
        numbers = re.findall(r"\d+", match.group(1))
        return role, "issues " + ", ".join(numbers)
    typed = _TYPES.search(agent_type or "")
    if typed:
        return _TYPE_ROLES[typed.group(1)], "unknown"
    return "other", "other"


def _time(text):
    when = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    return when if when.tzinfo else when.replace(tzinfo=datetime.timezone.utc)


def _prompt_text(record):
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def _read(path, seen):
    """(first prompt, calls) of one transcript; calls are de-duplicated by request id."""
    prompt, calls, skipped, by_request = None, [], 0, {}
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        if record.get("type") == "user" and prompt is None:
            prompt = _prompt_text(record)
        usage = (record.get("message") or {}).get("usage") if record.get("type") == "assistant" else None
        request = record.get("requestId")
        if not usage or not request or request in seen:
            continue
        try:
            by_request[request] = {"time": _time(record["timestamp"]),
                                   **{key: int(usage.get(key) or 0) for key in WEIGHTS}}
        except (KeyError, ValueError, TypeError):
            skipped += 1
    seen.update(by_request)
    for item in by_request.values():
        item["context"] = (item["input_tokens"] + item["cache_read_input_tokens"]
                           + item["cache_creation_input_tokens"])
        item["weighted"] = sum(item[key] * weight for key, weight in WEIGHTS.items())
        calls.append(item)
    return prompt, calls, skipped


def _agent_type(path):
    meta = path.with_name(path.name[:-len(".jsonl")] + ".meta.json")
    try:
        return json.loads(meta.read_text()).get("agentType")
    except (OSError, ValueError):
        return None


def _for_repo(prompt, repo):
    """True when a dispatch prompt names `repo` ("Repository: o/r." or the older "in o/r")."""
    return bool(re.search(r"(?:Repository:\s*|\bin\s+)" + re.escape(repo) + r"(?![\w.-]*\w)",
                          prompt or ""))


def report(root, since=None, repo=None):
    """Rows per (work, role) and totals per five-hour window.

    With `repo`, only agents whose dispatch prompt names that repository count,
    plus the sessions that dispatched them; every other session is left out.
    Without it, sessions that dispatched no Baton agent are one "other sessions" row.
    """
    root = pathlib.Path(root).expanduser()
    since = _time(since) if since else None
    seen, skipped, agents, sessions = set(), 0, [], []
    for path in sorted(root.rglob("*.jsonl")):
        prompt, calls, bad = _read(path, seen)
        skipped += bad
        calls = [call for call in calls if since is None or call["time"] >= since]
        if path.parent.name == "subagents":
            if repo is None or _for_repo(prompt, repo):
                agents.append((path, classify(prompt, _agent_type(path)), calls))
        else:
            sessions.append((path, calls))
    baton_dirs = {path.parent.parent for path, (role, _), _ in agents if role != "other"}
    entries = [(role_key, calls) for _, role_key, calls in agents]
    for path, calls in sessions:
        if path.with_suffix("") in baton_dirs:
            entries.append((("orchestrator", f"session {path.stem[:8]}"), calls))
        elif repo is None:
            entries.append((("other", "other sessions"), calls))

    rows, every = {}, []
    for (role, key), calls in entries:
        if not calls:
            continue
        row = rows.setdefault((key, role), {"key": key, "role": role, "agents": 0, "calls": 0,
                                            "weighted": 0.0, "peak": 0, "large": 0.0,
                                            "context": 0})
        row["agents"] += 1
        for call in calls:
            row["calls"] += 1
            row["weighted"] += call["weighted"]
            row["peak"] = max(row["peak"], call["context"])
            row["context"] += call["context"]
            if call["context"] > LARGE:
                row["large"] += call["weighted"]
            every.append((call["time"], role, call["weighted"]))
    for row in rows.values():
        row["avg_context"] = round(row.pop("context") / row["calls"]) if row["calls"] else 0
        row["large_share"] = row.pop("large") / row["weighted"] if row["weighted"] else 0.0

    windows = []
    for when, role, weighted in sorted(every):
        if not windows or when >= windows[-1]["_start"] + WINDOW:
            windows.append({"_start": when, "weighted": 0.0, "by_role": {}})
        window = windows[-1]
        window["weighted"] += weighted
        window["by_role"][role] = window["by_role"].get(role, 0.0) + weighted
    for window in windows:
        start = window.pop("_start")
        window["start"] = start.isoformat()
        window["end"] = (start + WINDOW).isoformat()
    ordered = sorted(rows.values(), key=lambda row: -row["weighted"])
    return {"rows": ordered, "windows": windows, "skipped": skipped}


def table(found):
    lines = ["| Work | Role | Agents | Calls | Weighted (M) | Peak (K) | Avg ctx (K) | Above 250K |",
             "|---|---|---|---|---|---|---|---|"]
    for row in found["rows"]:
        lines.append(f"| {row['key']} | {row['role']} | {row['agents']} | {row['calls']} | "
                     f"{row['weighted'] / 1e6:.1f} | {row['peak'] / 1e3:.0f} | "
                     f"{row['avg_context'] / 1e3:.0f} | "
                     f"{row['large_share']:.0%} |")
    lines += ["", "| Window (UTC) | Weighted (M) | By role |", "|---|---|---|"]
    for window in found["windows"]:
        shares = ", ".join(f"{role} {value / window['weighted']:.0%}"
                           for role, value in sorted(window["by_role"].items(),
                                                     key=lambda item: -item[1]))
        lines.append(f"| {window['start'][:16]} | {window['weighted'] / 1e6:.1f} | {shares} |")
    if found["skipped"]:
        lines += ["", f"{found['skipped']} unreadable records skipped."]
    return "\n".join(lines)


def total_weighted(found):
    """The weighted total of a report, as an integer."""
    return int(sum(row["weighted"] for row in found["rows"]))
