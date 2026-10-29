"""Throwaway prototype of the proposed gate: scan the text a forge command would send
(quoted strings, heredocs, body files), refuse only privacy findings, unscannable
bodies and hook bypass. No per-CLI parsing."""
import os, re, sys
sys.path.insert(0, "lib")
from baton import scan

POS = r"""(?:^|[;&|(`'"\n{]|\bsudo\b|\benv\b|\bxargs\b|\bexec\b|\bcommand\b|\btime\b|\bnohup\b)\s*(?:[A-Za-z_]\w*=\S*\s+)*"""
FORGE = re.compile(POS + r"(?:\S*/)?(gh|glab|tea|fj|curl|wget|http|https|xh)\b")
GITPUB = re.compile(POS + r"(?:\S*/)?git\b[^;&|\n]*?\b(commit|push|tag)\b")
BYPASS = re.compile(r"--no-verify\b|-c\s*core\.hooksPath|config\s+(?:--\S+\s+)*core\.hooksPath\s+\S|(?:^|[\s;&|])(?:HOME|XDG_CONFIG_HOME|BATON_PRIVATE_TERMS|GIT_CONFIG\w*)=")
SHORT_N = re.compile(POS + r"git\b[^;&|\n]*?\b(?:commit|push)\b[^;&|\n]*?\s-[a-zA-Z]*n[a-zA-Z]*(?:\s|$)")
STDIN_BODY = re.compile(r"""(?:--body-file|-F|--input|--data(?:-binary|-raw)?|-d)(?:\s+|=)(?:-|@-)(?:\s|$)""")
FILE_ARG = re.compile(r"""(?:--body-file|-F|--input|--data(?:-binary|-raw)?|-d)(?:\s+|=)@?([^\s'"$`*?<>|;&-][^\s'"$`*?<>|;&]*)""")
SUBST_BODY = re.compile(r"""(?:--body|-b|--title|-t|--message|-m|--raw-field|-f|--field|-d|--data(?:-raw)?)(?:\s+|=)"[^"]*(?:\$\((?!cat <<)|`)""")
HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?\n(.*?)\n\s*\1\b", re.S)
QUOTED = re.compile(r"""'[^']*'|"[^"]*\"""")
DATA_FLAG = re.compile(r"""(?:--data(?:-raw|-binary|-urlencode)?|--json|--form|-[dF])(?:\s+|=)(\S+|'[^']*'|"[^"]*")""")


def texts(cmd, cli):
    out = [m.group(2) for m in HEREDOC.finditer(cmd)]
    rest = HEREDOC.sub("", cmd)
    if cli in ("curl", "wget", "http", "https", "xh"):
        out += [m.group(1) for m in DATA_FLAG.finditer(rest)]
    else:
        out += QUOTED.findall(rest)
    return out


def decide(cmd, cwd, terms, allow=(), check_files=False):
    problems = []
    full_cmd, cmd = cmd, HEREDOC.sub("<<HD", cmd)
    forge = FORGE.search(cmd)
    git = GITPUB.search(cmd)
    if not (forge or git):
        return problems
    if (BYPASS.search(cmd) and git) or SHORT_N.search(cmd):
        problems.append("hook bypass")
    body = ""
    if forge:
        if STDIN_BODY.search(cmd) and "<<" not in cmd and "|" not in cmd:
            problems.append("stdin body")
        if SUBST_BODY.search(cmd):
            problems.append("substituted body")
        for m in FILE_ARG.finditer(cmd):
            path = m.group(1)
            full = path if os.path.isabs(path) else os.path.join(cwd, path)
            if check_files and not os.path.exists(full) and not re.search(r">\s*" + re.escape(path), cmd):
                problems.append("missing body file")
        body = "\n".join(texts(full_cmd, forge.group(1)))
    for kind in sorted({f.kind for f in scan.scan_text(body, terms, list(allow))}):
        problems.append("privacy:" + kind)
    return problems
