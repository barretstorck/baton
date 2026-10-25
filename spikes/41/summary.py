"""One summary line per orchestrator session: python3 -I summary.py <projects dir> <session prefix>..."""
import glob, json, re, subprocess, sys

here = __file__.rsplit("/", 1)[0]
for prefix in sys.argv[2:]:
    path = glob.glob(f"{sys.argv[1]}/*/{prefix}*.jsonl")[0]
    out = subprocess.run([sys.executable, "-I", f"{here}/breakdown.py", path], capture_output=True, text=True).stdout
    share = {}
    for line in out.splitlines():
        m = re.match(r"\s*([\d.]+)%\s+n=\s*\d+\s+size=\s*\d+ tok\s+reread-tokens=\s*\d+\s+(.*)", line)
        if m:
            share[m.group(2)] = float(m.group(1))
    gh = sum(v for k, v in share.items() if k.startswith("gh issue view") or k.startswith("gh pr view") or k.startswith("gh other"))
    agent = sum(v for k, v in share.items() if k.startswith("subagent result") or k.startswith("notification:"))
    report = sum(v for k, v in share.items() if k.startswith("notification: agent report"))
    base = sum(v for k, v in share.items() if k.startswith("baseline"))
    outp = sum(v for k, v in share.items() if k.startswith("assistant output"))
    print(prefix, out.splitlines()[0][:62], f"| baseline {base:.1f}% agent traffic {agent:.1f}% (reports {report:.1f}%) own output {outp:.1f}% gh {gh:.1f}%")
