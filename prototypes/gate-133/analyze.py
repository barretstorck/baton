import collections, json, pathlib, re, sys, signal
sys.path.insert(0, "lib")
sys.path.insert(0, ".scratch")
from baton import usage, gate
import proposed


class TO(Exception):
    pass


def _h(*a):
    raise TO()


signal.signal(signal.SIGALRM, _h)
root = pathlib.Path.home() / ".claude/projects"
CWD = str(pathlib.Path.cwd())
GHW = re.compile(
    r"(?:^|[;&|(`'\"\n]|\bsudo\b|\benv\b)\s*(?:\w+=\S*\s+)*gh\s+(?:-R\s+\S+\s+)?"
    r"(?:issue|pr|label|release|repo|milestone|workflow|run)\s+"
    r"(?:comment|create|edit|close|reopen|merge|review|ready|delete|rerun|cancel)"
    r"|\bgh\s+api\b[^\n]*(?:-X\s*(?:POST|PATCH|PUT|DELETE)|--method\s*(?:POST|PATCH|PUT|DELETE)"
    r"|\s-f\s|\s-F\s|--field|--raw-field|--input)")
GITP = proposed.GITPUB


def is_publish(cmd):
    return bool(GHW.search(cmd) or GITP.search(cmd))


def shape(cmd):
    s = re.sub(r"'[^']*'", "'S'", cmd)
    s = re.sub(r'"[^"]*"', '"S"', s)
    s = re.sub(r"<<-?\s*'?(\w+)'?.*", "<<HD", s, flags=re.S)
    return re.sub(r"\s+", " ", s)[:110]


def cat(m):
    return re.sub(r"`[^`]*`", "`X`", m)[:40]


from baton import scan
TERMS = scan.load_terms()
cache = {}
cur = collections.Counter()
prop = collections.Counter()
curmsg = collections.Counter()
propmsg = collections.Counter()
samples = collections.defaultdict(collections.Counter)
tot = tot_pub = 0
for p in root.rglob("*.jsonl"):
    try:
        lines = p.read_text(errors="replace").splitlines()
    except OSError:
        continue
    prompt = None
    uses = {}
    for line in lines:
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("type") == "user" and prompt is None:
            prompt = usage._prompt_text(r)
        c = (r.get("message") or {}).get("content")
        if isinstance(c, list):
            for part in c:
                if isinstance(part, dict) and part.get("type") == "tool_use" and part.get("name") == "Bash":
                    uses[part["id"]] = part["input"].get("command", "")
    role, _ = usage.classify(prompt, None)
    if role == "other":
        continue
    for cmd in uses.values():
        tot += 1
        if cmd not in cache:
            signal.alarm(2)
            try:
                a = gate.decide(cmd, CWD, [])
            except Exception:
                a = []
            finally:
                signal.alarm(0)
            cache[cmd] = (a, proposed.decide(cmd, CWD, TERMS), is_publish(cmd))
        a, b, pub = cache[cmd]
        tot_pub += pub
        private = any(x.startswith("privacy") for x in b)
        cls = "publish-private" if (pub and private) else ("publish-clean" if pub else "harmless")
        if a:
            cur[cls] += 1
            m = cat(a[0])
            curmsg[(m, cls)] += 1
            samples[(m, cls)][shape(cmd)] += 1
        if b:
            prop[cls] += 1
            propmsg[(b[0], cls)] += 1
            samples[("P:" + b[0], cls)][shape(cmd)] += 1
print("total", tot, "publish-shaped", tot_pub, "unique", len(cache))
print("current", dict(cur))
print("proposed", dict(prop))
print("-- current by message")
for (m, c), n in sorted(curmsg.items(), key=lambda x: -x[1]):
    print(n, c, m)
print("-- proposed by message")
for (m, c), n in sorted(propmsg.items(), key=lambda x: -x[1]):
    print(n, c, m)
json.dump({f"{k[0]}|{k[1]}": v.most_common(25) for k, v in samples.items()}, open(".scratch/samples.json", "w"))
