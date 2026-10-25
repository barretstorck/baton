"""Agent report sizes (the <result> of task notifications) by report form, and tool-call input sizes by tool."""
import json, re, sys, collections, statistics
forms = collections.defaultdict(list)
tools = collections.defaultdict(list)
for line in open(sys.argv[1], errors="replace"):
    try:
        r = json.loads(line)
    except ValueError:
        continue
    if r.get("type") == "assistant":
        for p in (r.get("message") or {}).get("content") or []:
            if p.get("type") == "tool_use":
                tools[p["name"]].append(len(json.dumps(p.get("input"))))
    if r.get("type") == "user":
        c = (r.get("message") or {}).get("content")
        texts = [c] if isinstance(c, str) else [p.get("text", "") for p in c or [] if isinstance(p, dict)]
        for s in texts:
            if "<task-notification" in s:
                m = re.search(r"<result>(.*?)</result>", s, re.S)
                if not m:
                    forms["(no result)"].append(0); continue
                body = m.group(1).lstrip()
                form = body.split(None, 1)[0].strip("`:") if body.split() else "(empty)"
                form = form if form in ("DONE", "FIXED", "DECLINED", "REPLAN", "QUESTION", "BLOCKED", "REVIEWED") else "other"
                forms[form].append(len(m.group(1)))
print("report forms: n, mean chars, median, max, total chars")
for k, v in sorted(forms.items(), key=lambda kv: -sum(kv[1])):
    print(f"{k:12s} n={len(v):3d} mean={statistics.mean(v):6.0f} median={statistics.median(v):6.0f} max={max(v):6d} total={sum(v)}")
print("tool-call input chars by tool: n, mean, total")
for k, v in sorted(tools.items(), key=lambda kv: -sum(kv[1]))[:8]:
    print(f"{k:12s} n={len(v):4d} mean={statistics.mean(v):6.0f} total={sum(v)}")
