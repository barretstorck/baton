"""Context breakdown of one orchestrator transcript.

For every item that enters the conversation (tool result, notification, assistant output, user text)
estimate its size in tokens, multiply by the number of API calls that re-read it, and sum per kind.
Usage: python3 -I breakdown.py <transcript.jsonl> [--top N] [--json] [--detail KIND]
Sizes: output tokens are exact; other items are characters scaled so the total matches the measured context growth (see tokens/char).
"""
import json, re, sys, collections

CHARS_PER_TOKEN = 3.5


def text_of(content):
    if isinstance(content, str):
        return content
    out = []
    for p in content or []:
        if isinstance(p, dict):
            if p.get("type") == "text":
                out.append(p.get("text", ""))
            elif p.get("type") == "tool_result":
                out.append(text_of(p.get("content")))
    return "\n".join(out)


def kind_of_tool(name, inp):
    if name == "Bash":
        cmd = (inp.get("command") or "").strip()
        cmd = re.sub(r"^(cd [^;&|]+(&&|;)\s*)+", "", cmd)
        m = re.match(r"(?:[A-Z_]+=\S+\s+)*(gh|baton|git|python3?)\s+(?:-m\s+baton\s+)?([^\s;|&]+)(?:\s+([^\s;|&]+))?", cmd)
        if m:
            w, a, b = m.groups()
            if w == "gh":
                if (a, b) in (("issue", "view"), ("pr", "view")):
                    return f"gh {a} view"
                return "gh other (" + " ".join(x for x in (a, b) if x and not x.startswith("-")) + ")"
            if w == "baton" or (w.startswith("python") and "baton" in cmd[:60]):
                return "baton " + a
            return w + " (bash)"
        return "bash other"
    if name in ("Agent", "Task"):
        return "subagent result (Agent tool)"
    return "tool " + name


def add_text(items, n, s):
    """A user-side text block: a subagent notification is split into the agent's report and its wrapper."""
    if "<task-notification" in s:
        m = re.search(r"<result>(.*?)</result>", s, re.S)
        report = len(m.group(1)) if m else 0
        label = (m.group(1)[:60] if m else s[:60]).replace("\n", " ")
        items.append((n, "notification: agent report (<result>)", report, label))
        items.append((n, "notification: wrapper (ids, paths, usage, worktree)", len(s) - report, ""))
    else:
        items.append((n, "user text / reminders", len(s), s[:80]))


def main():
    path = sys.argv[1]
    top = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 10
    detail = sys.argv[sys.argv.index("--detail") + 1] if "--detail" in sys.argv else None
    as_json = "--json" in sys.argv
    tool_names = {}
    items = []  # (calls so far, kind, chars, label)
    ctx = []
    out = {}
    order = {}
    seen = set()
    for line in open(path, errors="replace"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        t = r.get("type")
        n = len(ctx)
        if t == "assistant":
            msg = r.get("message") or {}
            rid = r.get("requestId")
            u = msg.get("usage")
            if rid and u and rid not in seen and (u.get('input_tokens', 0) + u.get('cache_read_input_tokens', 0) + u.get('cache_creation_input_tokens', 0)) > 0:
                seen.add(rid)
                ctx.append(u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0))
                n = len(ctx)
            if rid and u:
                out[rid] = max(out.get(rid, 0), u.get("output_tokens", 0))
                order.setdefault(rid, len(ctx))
            for p in msg.get("content") or []:
                if p.get("type") == "tool_use":
                    tool_names[p["id"]] = (p["name"], p.get("input") or {})
        elif t == "user":
            c = (r.get("message") or {}).get("content")
            if isinstance(c, list):
                for p in c:
                    if not isinstance(p, dict):
                        continue
                    if p.get("type") == "tool_result":
                        name, inp = tool_names.get(p.get("tool_use_id"), ("?", {}))
                        items.append((n, kind_of_tool(name, inp), len(text_of(p.get("content"))),
                                      (inp.get("command") or inp.get("description") or "")[:110]))
                    elif p.get("type") == "text":
                        s = p.get("text", "")
                        add_text(items, n, s)
            else:
                add_text(items, n, c or "")
        elif t == "attachment":
            a = r.get("attachment") or {}
            items.append((n, "attachment " + str(a.get("type")), len(json.dumps(a.get("content") or a.get("stdout") or "")), ""))
    drops = [(i, ctx[i - 1], ctx[i]) for i in range(1, len(ctx)) if ctx[i] < ctx[i - 1] * 0.7]
    base = ctx[0]
    # exact output tokens of each call (text, tool calls, thinking), first read by the next call
    outs = [0] * (len(ctx) + 1)
    for rid, k in order.items():
        outs[k] += out[rid]
    # scale: context growth that is not the model's own output, per character of tool/user/attachment text
    growth = ctx[-1] - base - sum(outs[1:len(ctx)])
    chars = sum(c for call, _, c, _ in items if 0 < call < len(ctx))
    scale = max(growth, 0) / chars if chars else 1 / CHARS_PER_TOKEN
    agg = collections.defaultdict(lambda: [0, 0.0, 0.0])
    rows = []
    for call, kind, nchars, label in items:
        if call == 0:
            continue  # part of the baseline (system prompt, skills, hooks) counted below
        tokens = nchars * scale
        rereads = len(ctx) - call  # first read by call number call+1, then every later one
        a = agg[kind]
        a[0] += 1; a[1] += tokens; a[2] += tokens * rereads
        rows.append((tokens * rereads, kind, label, tokens, rereads))
    o = agg["assistant output (text, tool calls, thinking)"]
    for k in range(1, len(ctx)):
        o[0] += 1; o[1] += outs[k]; o[2] += outs[k] * (len(ctx) - k)
    base_rereads = base * len(ctx)
    grand = sum(v[2] for v in agg.values()) + base_rereads
    kinds = sorted(([k, v[0], round(v[1]), round(v[2]), v[2] / grand] for k, v in agg.items()), key=lambda x: -x[3])
    kinds.append(["baseline: system prompt, skills, hooks, first prompt", 1, base, base_rereads, base_rereads / grand])
    kinds.sort(key=lambda x: -x[3])
    if as_json:
        print(json.dumps({"calls": len(ctx), "avg_context": sum(ctx) / len(ctx), "kinds": kinds}, indent=1))
        return
    print(f"calls {len(ctx)}  avg context {sum(ctx)/len(ctx):.0f}  first {base}  last {ctx[-1]}  peak {max(ctx)}")
    print("compaction-like drops (call, before, after):", drops, " tokens/char", round(scale, 3))
    for k, n, tok, rr, share in kinds[:25]:
        print(f"{share*100:5.1f}%  n={n:4d}  size={tok:8d} tok  reread-tokens={rr:10d}  {k}")
    if detail:
        rows = [x for x in rows if x[1] == detail]
    print("\nTop single items by size x re-reads:")
    for w, kind, label, tok, rr in sorted(rows, reverse=True)[:25]:
        print(f"{w/grand*100:5.2f}%  {tok:6.0f} tok x{rr:3d}  {kind}: {label!r}")


main()
