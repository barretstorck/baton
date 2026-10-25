import json, sys
path = sys.argv[1]
nres = nnot = 0
seen = set()
calls = 0
pre = []
for line in open(path, errors="replace"):
    r = json.loads(line)
    t = r.get("type")
    if t == "assistant" and r.get("requestId") and r["requestId"] not in seen and (r.get("message") or {}).get("usage"):
        seen.add(r["requestId"]); calls += 1
        if calls == 1:
            print("first call usage", r["message"]["usage"])
    if calls == 0 and t in ("user", "attachment"):
        pre.append((t, len(json.dumps(r)), json.dumps(r)[:150]))
    if t == "user":
        c = (r.get("message") or {}).get("content")
        if isinstance(c, list):
            for p in c:
                if p.get("type") == "tool_result" and nres < 2 and "agent" in json.dumps(p)[:600].lower():
                    print("RESULT", json.dumps(p)[:1500]); nres += 1
                if p.get("type") == "text" and "<task-notification" in p.get("text", "") and nnot < 1:
                    print("NOTIF", p["text"][:3000]); nnot += 1
        elif isinstance(c, str) and "<task-notification" in c and nnot < 1:
            print("NOTIF", c[:3000]); nnot += 1
print("pre-call items", [(a, b) for a, b, _ in pre])
