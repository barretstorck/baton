import json, sys
seen = set(); calls = 0
for line in open(sys.argv[1], errors="replace"):
    r = json.loads(line)
    t = r.get("type")
    if t == "assistant" and r.get("requestId") and (r.get("message") or {}).get("usage"):
        calls += 1
        break
    if t in ("user", "attachment"):
        a = r.get("attachment") or {}
        body = json.dumps(a.get("content") or a.get("stdout") or r.get("message") or "")
        print(t, a.get("type"), len(body), body[:110].replace("\\n", " "))
