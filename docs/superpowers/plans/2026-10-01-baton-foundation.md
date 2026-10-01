# Baton Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the tested, deterministic base of the Baton plugin: plugin skeleton, repo-brief settings, the privacy publish gate, git hooks, label setup, the `merge-pr` command and the `init` skill.

**Architecture:** One Python package (`lib/baton`, standard library only) exposed as a `baton` command in the plugin's `bin/`. A Claude Code `PreToolUse` hook calls `baton gate` before every Bash command; git `pre-commit` and `pre-push` hooks call `baton git-hook`. All forge access goes through an injectable runner so tests never call the network.

**Tech Stack:** Python 3.10+ standard library, `unittest`, `gh` CLI 2.94+, git, Claude Code plugin format.

**Spec:** `docs/superpowers/specs/2026-10-01-baton-design.md` (sections 2, 3, 6 "Check finished work", 8, 9, 10).

**Executed 2026-10-01.** The final review changed the gate (now word-based, not regex over the raw command), the approval rule (`/baton approve <sha>`) and several checks; the code and README are authoritative where they differ from the task text below.

**This is plan 1 of 4.** Later plans: (2) `develop` and `refine` skills plus the `baton-developer` subagent; (3) `sprint-plan` and `sprint` skills; (4) scheduled runs and the pilot sprint.

## Global Constraints

- No third-party Python packages. PyYAML is not available on the target machine; the brief's front matter is read by a small parser in this plan.
- Tests: `PYTHONPATH=lib python3 -m unittest discover -s tests -v` must pass after every task.
- Nothing personal in this repository: no real names, hostnames, IP addresses or paths in code, tests, fixtures or commit messages. Test fixtures use `192.168.7.7`, `alice@corp-mail.test`, `/mnt/tank/photos` and the private term `Zebrowski`.
- Commit author: the repository's existing no-reply author (`git log --format='%an <%ae>' -1`). End commit messages with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- The gate acts only in repositories that contain `.baton/brief.md`. In any other repository it must do nothing.
- Scanner output never prints the matched text, only the kind and line number.
- Baton label names are fixed (spec section 3); the repository's actual names come from `labels.map`.
- Agent comment marker: `<!-- baton:<role>:<kind> -->`.

**One refinement of the spec:** the spec says the `PreToolUse` hook scans diffs for `git commit` and `git push`. A `PreToolUse` hook runs before the command, so in `git add x && git commit` nothing is staged yet. In this plan the hook scans the command text and any files it references, refuses hook bypasses, and requires the git hooks to be installed; the git `pre-commit` and `pre-push` hooks scan the actual diffs.

## Review Focus

1. A comment body passed by `--body-file` or a heredoc, not inline: the file contents and the whole command text must be scanned. (Task 3)
2. `git commit --no-verify`, `git push --no-verify` or `-c core.hooksPath=`: must be refused, or the diff scan is skipped. (Task 3)
3. A commit that *removes* a private string: must not be blocked, or leaks could never be cleaned up. (Task 4)
4. `merge-pr` called seconds after a push, before CI has registered any check: must refuse, not merge on an empty check list. (Task 6)
5. A `.baton/brief.md` whose front matter cannot be parsed: publishing must be blocked with a clear message, not silently allowed. (Task 3)

## File Structure

| File | Responsibility |
|---|---|
| `.claude-plugin/plugin.json` | Plugin manifest |
| `bin/baton` | Shell launcher for `python3 -m baton` |
| `lib/baton/cli.py`, `__main__.py` | Command dispatch |
| `lib/baton/run.py` | Run a subprocess, raise on failure |
| `lib/baton/frontmatter.py` | Parse the YAML subset used in the brief |
| `lib/baton/config.py` | Defaults, brief loading, label mapping |
| `lib/baton/scan.py` | Privacy patterns and private terms |
| `lib/baton/gate.py` | `PreToolUse` decision for a Bash command |
| `lib/baton/githooks.py` | `pre-commit`, `pre-push`, installer |
| `lib/baton/labels.py` | Ensure labels exist, set a status |
| `lib/baton/merge.py` | Acceptance checks and merge |
| `hooks/hooks.json` | Registers the gate |
| `templates/brief.md` | Starting repo brief |
| `skills/init/SKILL.md` | `/baton:init` |
| `tests/` | One test file per module, plus `fakes.py` |
| `.github/workflows/ci.yml` | Runs the tests on pull requests |

---

### Task 1: Skeleton, front matter and config

**Files:**
- Create: `.claude-plugin/plugin.json`, `bin/baton`, `lib/baton/__init__.py`, `lib/baton/__main__.py`, `lib/baton/cli.py`, `lib/baton/run.py`, `lib/baton/frontmatter.py`, `lib/baton/config.py`
- Test: `tests/test_frontmatter.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `run.run(argv: list[str], cwd: str | None = None) -> str`; raises `run.RunError`
  - `frontmatter.split(text: str) -> tuple[str, str]` (meta, body); `frontmatter.parse(meta: str) -> dict`
  - `config.load(path: str) -> Config | None`; raises `config.ConfigError`
  - `Config.root: pathlib.Path`, `Config.data: dict`, `Config.label(name: str) -> str`, `Config.status_labels() -> list[str]`
  - `config.STATUSES: list[str]`, `config.DEFAULTS: dict`
  - `cli.main(argv: list[str]) -> int` with a `COMMANDS` dict that later tasks extend

- [ ] **Step 1: Write the failing tests**

`tests/test_frontmatter.py`:

```python
import unittest
from baton import frontmatter

BRIEF = """---
developer:
  model: sonnet
  max_parallel: 4   # a comment
refine:
  may_mark_ready: false
labels:
  map:
    status:in-dev: "In Progress"
protected_paths: [".github/", ".baton/"]
fixtures_paths: []
budgets: {}
---
# Rules

Body text.
"""


class FrontmatterTest(unittest.TestCase):
    def test_split_returns_meta_and_body(self):
        meta, body = frontmatter.split(BRIEF)
        self.assertIn("developer:", meta)
        self.assertTrue(body.startswith("# Rules"))

    def test_split_without_front_matter(self):
        self.assertEqual(frontmatter.split("just text"), ("", "just text"))

    def test_parse_nested_scalars_lists_and_comments(self):
        data = frontmatter.parse(frontmatter.split(BRIEF)[0])
        self.assertEqual(data["developer"], {"model": "sonnet", "max_parallel": 4})
        self.assertIs(data["refine"]["may_mark_ready"], False)
        self.assertEqual(data["labels"]["map"], {"status:in-dev": "In Progress"})
        self.assertEqual(data["protected_paths"], [".github/", ".baton/"])
        self.assertEqual(data["fixtures_paths"], [])
        self.assertEqual(data["budgets"], {})

    def test_parse_rejects_garbage(self):
        with self.assertRaises(ValueError):
            frontmatter.parse("this is not a mapping")
```

`tests/test_config.py`:

```python
import pathlib
import subprocess
import tempfile
import unittest
from baton import config


def make_repo(brief=None):
    root = pathlib.Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    if brief is not None:
        (root / ".baton").mkdir()
        (root / ".baton" / "brief.md").write_text(brief)
    return root


class ConfigTest(unittest.TestCase):
    def test_no_brief_means_not_a_baton_repo(self):
        self.assertIsNone(config.load(str(make_repo())))

    def test_outside_git_means_none(self):
        self.assertIsNone(config.load(tempfile.mkdtemp()))

    def test_defaults_apply(self):
        cfg = config.load(str(make_repo("# no front matter\n")))
        self.assertEqual(cfg.data["developer"]["model"], "sonnet")
        self.assertEqual(cfg.label("status:ready"), "status:ready")

    def test_brief_overrides_and_keeps_other_defaults(self):
        brief = '---\nlabels:\n  map:\n    status:in-dev: "In Progress"\n---\n'
        cfg = config.load(str(make_repo(brief)))
        self.assertEqual(cfg.label("status:in-dev"), "In Progress")
        self.assertEqual(cfg.label("status:blocked"), "status:blocked")
        self.assertIn("In Progress", cfg.status_labels())
        self.assertEqual(len(cfg.status_labels()), 7)

    def test_bad_front_matter_raises(self):
        with self.assertRaises(config.ConfigError):
            config.load(str(make_repo("---\nnot a mapping\n---\n")))

    def test_load_from_subdirectory(self):
        root = make_repo("x\n")
        (root / "src").mkdir()
        self.assertEqual(config.load(str(root / "src")).root.resolve(), root.resolve())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=lib python3 -m unittest discover -s tests -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'baton'`

- [ ] **Step 3: Write the implementation**

`.claude-plugin/plugin.json`:

```json
{
  "name": "baton",
  "version": "0.1.0",
  "description": "Run software sprints on a forge repository with an orchestrator and parallel developer agents."
}
```

`bin/baton` (then `chmod +x bin/baton`):

```sh
#!/bin/sh
here="$(cd "$(dirname "$0")/.." && pwd)"
PYTHONPATH="$here/lib${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m baton "$@"
```

`lib/baton/__init__.py`: empty file.

`lib/baton/__main__.py`:

```python
import sys

from . import cli

sys.exit(cli.main(sys.argv[1:]))
```

`lib/baton/run.py`:

```python
import subprocess


class RunError(Exception):
    pass


def run(argv, cwd=None):
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RunError(f"{' '.join(argv[:3])} failed: {proc.stderr.strip()}")
    return proc.stdout
```

`lib/baton/frontmatter.py`:

```python
"""Parser for the YAML subset used in .baton/brief.md front matter.

Supports nested mappings by indentation, quoted or bare scalars, booleans,
integers, inline lists ([a, b]), empty mappings ({}) and # comments.
"""
import re

_LINE = re.compile(r"""^("[^"]+"|'[^']+'|.+?):(?:\s+(.*))?$""")


def split(text):
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---", 4)
    if end == -1:
        return "", text
    body = text[end + 4:]
    return text[4:end + 1], body.lstrip("\n")


def _strip_comment(line):
    quote = None
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#" and (index == 0 or line[index - 1] in " \t"):
            return line[:index]
    return line


def _scalar(text):
    text = text.strip()
    if text == "{}":
        return {}
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [_scalar(part) for part in inner.split(",")] if inner else []
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    if text in ("true", "false"):
        return text == "true"
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return text


def parse(meta):
    root = {}
    stack = [(-1, root)]
    for raw in meta.splitlines():
        line = _strip_comment(raw).rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        match = _LINE.match(line.strip())
        if not match:
            raise ValueError(f"cannot parse front matter line: {raw.strip()!r}")
        key = match.group(1).strip("\"'")
        value = match.group(2)
        while stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        if value is None or value.strip() == "":
            parent[key] = {}
            stack.append((indent, parent[key]))
        else:
            parent[key] = _scalar(value)
    return root
```

`lib/baton/config.py`:

```python
import copy
import pathlib
from dataclasses import dataclass

from . import frontmatter, run

STATUSES = [
    "status:refinement", "status:ready", "status:in-dev", "status:blocked",
    "status:in-review", "status:verification", "status:complete",
]
OTHER_LABELS = [
    "type:spike", "deliverable", "stretch", "broken-default-branch",
    "needs-maintainer",
]
DEFAULTS = {
    "developer": {"model": "sonnet", "max_parallel": 2},
    "refine": {"may_mark_ready": True},
    "labels": {
        "auto_create": True,
        "map": {name: name for name in STATUSES + OTHER_LABELS},
    },
    "merge": {"allow_no_ci": False},
    "protected_paths": [".github/", ".baton/", ".claude/"],
    "fixtures_paths": [],
    "privacy_allow": [],
    "budgets": {},
}


class ConfigError(Exception):
    pass


@dataclass
class Config:
    root: pathlib.Path
    data: dict

    def label(self, name):
        return self.data["labels"]["map"].get(name, name)

    def status_labels(self):
        return [self.label(name) for name in STATUSES]


def _merge(base, extra):
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def repo_root(path):
    try:
        out = run.run(["git", "-C", path, "rev-parse", "--show-toplevel"])
    except (run.RunError, OSError):
        return None
    return pathlib.Path(out.strip())


def load(path):
    root = repo_root(path)
    if root is None:
        return None
    brief = root / ".baton" / "brief.md"
    if not brief.is_file():
        return None
    meta, _ = frontmatter.split(brief.read_text())
    try:
        data = frontmatter.parse(meta)
    except ValueError as error:
        raise ConfigError(f".baton/brief.md: {error}") from error
    return Config(root=root, data=_merge(copy.deepcopy(DEFAULTS), data))
```

`lib/baton/cli.py`:

```python
import json
import os
import sys

from . import config


def cmd_config(args):
    cfg = config.load(os.getcwd())
    if cfg is None:
        print("not a Baton repository: .baton/brief.md not found", file=sys.stderr)
        return 1
    print(json.dumps(cfg.data, indent=2))
    return 0


COMMANDS = {"config": cmd_config}


def main(argv):
    if not argv or argv[0] not in COMMANDS:
        print("usage: baton <" + "|".join(sorted(COMMANDS)) + "> [args]", file=sys.stderr)
        return 64
    try:
        return COMMANDS[argv[0]](argv[1:])
    except config.ConfigError as error:
        print(f"baton: {error}", file=sys.stderr)
        return 2
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=lib python3 -m unittest discover -s tests -v`
Expected: 10 tests, OK. Also run `bin/baton config` in this repository; expected exit 1 with "not a Baton repository".

- [ ] **Step 5: Commit**

```bash
git add .claude-plugin bin lib tests
git commit -m "Add plugin skeleton, front matter parser and config"
```

---

### Task 2: Privacy scanner

**Files:**
- Create: `lib/baton/scan.py`
- Test: `tests/test_scan.py`

**Interfaces:**
- Produces:
  - `scan.Finding(kind: str, line: int)`
  - `scan.scan_text(text: str, terms: list[str], allow: list[str]) -> list[Finding]`
  - `scan.load_terms(path: str | None = None) -> list[str]` (default path `~/.baton/private-terms.txt`, overridden by env `BATON_PRIVATE_TERMS`; a missing file gives `[]`)
  - `scan.describe(findings: list[Finding]) -> list[str]`
  - `scan.IMAGE_SUFFIXES: set[str]`

- [ ] **Step 1: Write the failing test**

`tests/test_scan.py`:

```python
import os
import tempfile
import unittest
from baton import scan


def kinds(text, terms=(), allow=()):
    return [f.kind for f in scan.scan_text(text, list(terms), list(allow))]


class ScanTest(unittest.TestCase):
    def test_clean_text_passes(self):
        self.assertEqual(kinds("Refactor the resample loop.\nCloses #12"), [])

    def test_private_ip(self):
        self.assertEqual(kinds("server at 192.168.7.7 today"), ["private-ip"])
        self.assertEqual(kinds("10.0.0.5"), ["private-ip"])
        self.assertEqual(kinds("172.20.1.1"), ["private-ip"])

    def test_public_ip_and_versions_pass(self):
        self.assertEqual(kinds("8.8.8.8 and version 10.2.1"), [])

    def test_email(self):
        self.assertEqual(kinds("mail alice@corp-mail.test"), ["email"])

    def test_noreply_and_example_emails_pass(self):
        text = ("Co-Authored-By: Claude <noreply@anthropic.com>\n"
                "1+someone@users.noreply.github.com\nuser@example.com")
        self.assertEqual(kinds(text), [])

    def test_host_path(self):
        self.assertEqual(kinds("stored in /mnt/tank/photos"), ["host-path"])
        self.assertEqual(kinds("see /home/someone/.config"), ["host-path"])

    def test_internal_domain(self):
        self.assertEqual(kinds("http://nas.local:8080"), ["internal-domain"])
        self.assertEqual(kinds("box.home.arpa"), ["internal-domain"])

    def test_code_that_looks_like_a_domain_passes(self):
        self.assertEqual(kinds("edit settings.local.json; x = threading.local()"), [])

    def test_private_term_case_insensitive(self):
        self.assertEqual(kinds("the zebrowski album", terms=["Zebrowski"]), ["private-term"])

    def test_allow_list_exempts_literal(self):
        self.assertEqual(kinds("WORKDIR /home/app", allow=["/home/app"]), [])

    def test_line_numbers_and_no_match_text(self):
        findings = scan.scan_text("ok\nok\nZebrowski", ["zebrowski"], [])
        self.assertEqual(findings[0].line, 3)
        self.assertNotIn("ebrowski", " ".join(scan.describe(findings)))

    def test_load_terms_skips_comments_and_blanks(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write("# names\nZebrowski\n\n  tank  \n")
        self.assertEqual(scan.load_terms(fh.name), ["Zebrowski", "tank"])
        os.unlink(fh.name)

    def test_load_terms_missing_file(self):
        self.assertEqual(scan.load_terms("/nonexistent/terms.txt"), [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=lib python3 -m unittest tests.test_scan -v`
Expected: FAIL with `ImportError: cannot import name 'scan'`

- [ ] **Step 3: Write the implementation**

`lib/baton/scan.py`:

```python
import os
import re
from dataclasses import dataclass

IMAGE_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".heic",
    ".bmp", ".dng",
}

_PATTERNS = [
    ("private-ip", re.compile(
        r"(?<![\d.])(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))"
        r"\.\d{1,3}\.\d{1,3}(?![\d.])")),
    ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("host-path", re.compile(
        r"(?<![\w.])/(?:home|mnt|Users|root|srv|media|volume\d*)/[^\s\"'`)]+")),
    ("internal-domain", re.compile(
        r"\b[\w-]+(?:\.[\w-]+)*\.(?:local|lan|internal|corp|home\.arpa)\b(?![\w.(-])")),
]
_EMAIL_OK = re.compile(
    r"@(?:users\.noreply\.github\.com|anthropic\.com|example\.(?:com|org|net))$")


@dataclass(frozen=True)
class Finding:
    kind: str
    line: int


def load_terms(path=None):
    path = path or os.environ.get("BATON_PRIVATE_TERMS") or os.path.expanduser(
        "~/.baton/private-terms.txt")
    try:
        with open(path) as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines
            if line.strip() and not line.strip().startswith("#")]


def _matches(kind, pattern, line):
    for match in pattern.finditer(line):
        text = match.group(0)
        if kind == "email" and _EMAIL_OK.search(text):
            continue
        yield text


def scan_text(text, terms, allow):
    findings = []
    lowered_terms = [term.lower() for term in terms]
    for number, line in enumerate(text.splitlines(), start=1):
        for literal in allow:
            line = line.replace(literal, "")
        for kind, pattern in _PATTERNS:
            if any(True for _ in _matches(kind, pattern, line)):
                findings.append(Finding(kind, number))
        lowered = line.lower()
        if any(term in lowered for term in lowered_terms):
            findings.append(Finding("private-term", number))
    return findings


def describe(findings):
    return [f"{finding.kind} on line {finding.line}" for finding in findings]
```

Notes: the IP pattern requires four dotted numbers, so `version 10.2.1` does not match. The internal-domain pattern refuses a match followed by `.`, `(`, `-` or a word character, so `settings.local.json` and `threading.local()` pass.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=lib python3 -m unittest tests.test_scan -v`
Expected: 13 tests, OK

- [ ] **Step 5: Commit**

```bash
git add lib/baton/scan.py tests/test_scan.py
git commit -m "Add privacy scanner"
```

---

### Task 3: Publish gate (`PreToolUse` hook)

**Files:**
- Create: `lib/baton/gate.py`, `hooks/hooks.json`
- Modify: `lib/baton/cli.py` (register `gate`)
- Test: `tests/test_gate.py`

**Interfaces:**
- Consumes: `config.load`, `config.ConfigError`, `scan.scan_text`, `scan.load_terms`, `scan.describe`, `run.run`
- Produces:
  - `gate.MARKER = "<!-- baton:"`
  - `gate.HOOK_TAG = "baton git-hook"` (Task 4 writes this string into the git hook shims)
  - `gate.decide(command: str, cwd: str, terms: list[str]) -> list[str]` (problems; empty means allow)
  - `gate.main(stdin_text: str) -> int` (0 allow, 2 block; problems on stderr)

- [ ] **Step 1: Write the failing test**

`tests/test_gate.py`:

```python
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from baton import gate

TERMS = ["Zebrowski"]


def make_repo(brief="---\n---\n", hooks=True):
    root = pathlib.Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    if brief is not None:
        (root / ".baton").mkdir()
        (root / ".baton" / "brief.md").write_text(brief)
    if hooks:
        for name in ("pre-commit", "pre-push"):
            (root / ".git" / "hooks" / name).write_text(f"#!/bin/sh\n# {gate.HOOK_TAG}\n")
    return str(root)


class GateTest(unittest.TestCase):
    def test_non_publishing_command_is_ignored(self):
        self.assertEqual(gate.decide("ls 192.168.7.7", make_repo(), TERMS), [])

    def test_non_baton_repo_is_ignored(self):
        repo = make_repo(brief=None)
        self.assertEqual(gate.decide('gh issue create --body "192.168.7.7"', repo, TERMS), [])

    def test_inline_body_is_scanned(self):
        problems = gate.decide('gh pr create --title x --body "see 192.168.7.7"', make_repo(), TERMS)
        self.assertTrue(any("private-ip" in p for p in problems))

    def test_body_file_is_scanned(self):
        repo = make_repo()
        pathlib.Path(repo, "body.md").write_text("<!-- baton:developer:status -->\nthe Zebrowski set\n")
        problems = gate.decide("gh issue comment 5 --body-file body.md", repo, TERMS)
        self.assertTrue(any("private-term" in p for p in problems))

    def test_heredoc_body_is_scanned(self):
        cmd = "gh issue create --title t --body \"$(cat <<'EOF'\nmail alice@corp-mail.test\nEOF\n)\""
        self.assertTrue(any("email" in p for p in gate.decide(cmd, make_repo(), TERMS)))

    def test_clean_issue_passes(self):
        self.assertEqual(gate.decide('gh issue create --title t --body "Add a flag"', make_repo(), TERMS), [])

    def test_comment_without_marker_is_blocked(self):
        problems = gate.decide('gh issue comment 5 --body "done"', make_repo(), TERMS)
        self.assertTrue(any("marker" in p for p in problems))

    def test_comment_with_marker_passes(self):
        cmd = 'gh pr comment 5 --body "<!-- baton:developer:status -->\nBaton developer: done"'
        self.assertEqual(gate.decide(cmd, make_repo(), TERMS), [])

    def test_no_verify_is_blocked(self):
        for cmd in ("git commit --no-verify -m x", "git push --no-verify",
                    "git commit -nm x", "git -c core.hooksPath=/dev/null commit -m x"):
            with self.subTest(cmd=cmd):
                self.assertTrue(any("bypass" in p for p in gate.decide(cmd, make_repo(), TERMS)))

    def test_push_dry_run_flag_is_not_a_bypass(self):
        self.assertEqual(gate.decide("git push -n origin main", make_repo(), TERMS), [])

    def test_git_publish_requires_installed_hooks(self):
        problems = gate.decide("git commit -m x", make_repo(hooks=False), TERMS)
        self.assertTrue(any("install-hooks" in p for p in problems))

    def test_commit_message_is_scanned(self):
        problems = gate.decide('git commit -m "fix Zebrowski import"', make_repo(), TERMS)
        self.assertTrue(any("private-term" in p for p in problems))

    def test_cd_into_baton_repo_from_elsewhere(self):
        repo = make_repo()
        cmd = f'cd {repo} && gh issue create --title t --body "192.168.7.7"'
        self.assertTrue(gate.decide(cmd, tempfile.mkdtemp(), TERMS))

    def test_unparsable_brief_blocks(self):
        repo = make_repo(brief="---\nnot a mapping\n---\n")
        problems = gate.decide('gh issue create --title t --body ok', repo, TERMS)
        self.assertTrue(any("brief.md" in p for p in problems))

    def test_allow_list_from_brief(self):
        repo = make_repo(brief='---\nprivacy_allow: ["/home/app"]\n---\n')
        self.assertEqual(gate.decide('gh issue create --title t --body "WORKDIR /home/app"', repo, TERMS), [])

    def test_main_blocks_with_exit_2_and_ignores_other_tools(self):
        repo = make_repo()
        payload = {"tool_name": "Bash", "cwd": repo,
                   "tool_input": {"command": 'gh issue create --title t --body "192.168.7.7"'}}
        os.environ["BATON_PRIVATE_TERMS"] = "/nonexistent"
        self.assertEqual(gate.main(json.dumps(payload)), 2)
        payload["tool_name"] = "Read"
        self.assertEqual(gate.main(json.dumps(payload)), 0)
        self.assertEqual(gate.main("not json"), 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=lib python3 -m unittest tests.test_gate -v`
Expected: FAIL with `ImportError: cannot import name 'gate'`

- [ ] **Step 3: Write the implementation**

`lib/baton/gate.py`:

```python
"""Decide whether a Bash command may publish to the forge.

Runs as a Claude Code PreToolUse hook. It scans the command text and any
files the command references. Diffs are scanned by the git hooks
(githooks.py), which this gate requires to be installed and not bypassed.
"""
import json
import os
import re
import sys

from . import config, run, scan

MARKER = "<!-- baton:"
HOOK_TAG = "baton git-hook"

_GH_PUBLISH = re.compile(
    r"\bgh\s+(?:(?:issue|pr)\s+(?:create|comment|edit|review|close)|api|"
    r"release\s+(?:create|edit)|label\s+(?:create|edit)|milestone)\b")
_GH_COMMENT = re.compile(r"\bgh\s+(?:issue|pr)\s+(?:comment|review)\b")
_GIT_COMMIT = re.compile(r"\bgit\b[^|;&\n]*?\bcommit\b")
_GIT_PUSH = re.compile(r"\bgit\b[^|;&\n]*?\bpush\b")
_BYPASS = re.compile(r"--no-verify\b|core\.hooksPath")
_COMMIT_SHORT_N = re.compile(r"\bgit\b[^|;&\n]*?\bcommit\b[^|;&\n]*?\s-[a-mo-zA-Z]*n[a-zA-Z]*\b")
_DIR = re.compile(r"""(?:\bcd\s+|\s-C\s+)("[^"]+"|'[^']+'|[^\s;&|]+)""")
_FILE_ARG = re.compile(
    r"""(?:--body-file|--file|--input|--template|-F)(?:=|\s+)("[^"]+"|'[^']+'|[^\s;&|]+)""")
_MAX_FILE = 1_000_000


def _candidate_dirs(command, cwd):
    dirs = [cwd]
    for match in _DIR.finditer(command):
        path = os.path.expanduser(match.group(1).strip("\"'"))
        dirs.append(os.path.join(cwd, path))
    return dirs


def _find_config(command, cwd):
    for directory in _candidate_dirs(command, cwd):
        if not os.path.isdir(directory):
            continue
        cfg = config.load(directory)
        if cfg is not None:
            return cfg
    return None


def _referenced_text(command, bases):
    chunks = []
    for match in _FILE_ARG.finditer(command):
        value = match.group(1).strip("\"'")
        if "@" in value:
            value = value.split("@", 1)[1]
        for base in bases:
            path = os.path.join(base, os.path.expanduser(value))
            if os.path.isfile(path) and os.path.getsize(path) <= _MAX_FILE:
                with open(path, errors="replace") as handle:
                    chunks.append(handle.read())
                break
    return "\n".join(chunks)


def _hooks_installed(root):
    try:
        common = run.run(["git", "-C", str(root), "rev-parse", "--git-common-dir"]).strip()
    except run.RunError:
        return False
    hooks = os.path.join(root, common, "hooks")
    for name in ("pre-commit", "pre-push"):
        try:
            with open(os.path.join(hooks, name)) as handle:
                if HOOK_TAG not in handle.read():
                    return False
        except OSError:
            return False
    return True


def decide(command, cwd, terms):
    is_commit = bool(_GIT_COMMIT.search(command))
    is_push = bool(_GIT_PUSH.search(command))
    if not (_GH_PUBLISH.search(command) or is_commit or is_push):
        return []
    try:
        cfg = _find_config(command, cwd)
    except config.ConfigError as error:
        return [f"{error}. Fix the brief before publishing."]
    if cfg is None:
        return []

    problems = []
    if is_commit or is_push:
        if _BYPASS.search(command) or (is_commit and _COMMIT_SHORT_N.search(command)):
            problems.append("git hook bypass (--no-verify, -n or core.hooksPath) is not allowed here")
        if not _hooks_installed(cfg.root):
            problems.append("Baton git hooks are not installed; run `baton install-hooks`")

    text = command + "\n" + _referenced_text(command, [cwd, str(cfg.root)])
    findings = scan.scan_text(text, terms, cfg.data["privacy_allow"])
    problems.extend(f"privacy: {line} of the command or its body file"
                    for line in scan.describe(findings))

    if _GH_COMMENT.search(command) and MARKER not in text:
        problems.append("agent comments must start with the Baton marker "
                        "<!-- baton:<role>:<kind> -->")
    return problems


def main(stdin_text):
    try:
        payload = json.loads(stdin_text)
    except ValueError:
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    command = payload.get("tool_input", {}).get("command", "")
    cwd = payload.get("cwd") or os.getcwd()
    problems = decide(command, cwd, scan.load_terms())
    if not problems:
        return 0
    print("Baton publish gate blocked this command:", file=sys.stderr)
    for problem in problems:
        print(f"- {problem}", file=sys.stderr)
    print("Replace private details with placeholders and retry.", file=sys.stderr)
    return 2
```

`hooks/hooks.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "\"${CLAUDE_PLUGIN_ROOT}/bin/baton\" gate" }
        ]
      }
    ]
  }
}
```

In `lib/baton/cli.py`, add the import and command:

```python
from . import config, gate


def cmd_gate(args):
    return gate.main(sys.stdin.read())
```

and change the dict to `COMMANDS = {"config": cmd_config, "gate": cmd_gate}`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=lib python3 -m unittest discover -s tests -v`
Expected: all tests OK (39 so far). Then check the launcher end to end:

```bash
echo '{"tool_name":"Bash","cwd":"/tmp","tool_input":{"command":"ls"}}' | bin/baton gate; echo "exit $?"
```
Expected: `exit 0`

- [ ] **Step 5: Commit**

```bash
git add lib/baton/gate.py lib/baton/cli.py hooks tests/test_gate.py
git commit -m "Add publish gate hook"
```

---

### Task 4: Git hooks (`pre-commit`, `pre-push`) and installer

**Files:**
- Create: `lib/baton/githooks.py`
- Modify: `lib/baton/cli.py` (register `git-hook`, `install-hooks`)
- Test: `tests/test_githooks.py`

**Interfaces:**
- Consumes: `gate.HOOK_TAG`, `config.load`, `scan.scan_text`, `scan.load_terms`, `scan.describe`, `scan.IMAGE_SUFFIXES`, `run.run`
- Produces:
  - `githooks.added_text(patch: str) -> str`
  - `githooks.install(repo: str, baton_bin: str) -> list[str]` (paths written; raises `githooks.HookError` if a foreign hook exists)
  - `githooks.pre_commit(cwd: str, terms: list[str]) -> list[str]`
  - `githooks.pre_push(cwd: str, stdin_text: str, terms: list[str]) -> list[str]`

- [ ] **Step 1: Write the failing test**

`tests/test_githooks.py`:

```python
import pathlib
import subprocess
import tempfile
import unittest
from baton import gate, githooks

TERMS = ["Zebrowski"]
ZEROS = "0" * 40


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def make_repo(brief='---\nfixtures_paths: ["tests/fixtures/"]\n---\n'):
    repo = tempfile.mkdtemp()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "dev@example.com")
    git(repo, "config", "user.name", "Dev")
    if brief is not None:
        pathlib.Path(repo, ".baton").mkdir()
        pathlib.Path(repo, ".baton", "brief.md").write_text(brief)
    pathlib.Path(repo, "a.txt").write_text("hello\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    return repo


def write(repo, name, content):
    path = pathlib.Path(repo, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode())


class AddedTextTest(unittest.TestCase):
    def test_keeps_added_and_message_lines_drops_removed_and_context(self):
        patch = "msg line\n--- a/x\n+++ b/x\n-removed 192.168.7.7\n context\n+added\n"
        text = githooks.added_text(patch)
        self.assertIn("msg line", text)
        self.assertIn("+added", text)
        self.assertNotIn("removed", text)
        self.assertNotIn("context", text)


class PreCommitTest(unittest.TestCase):
    def test_clean_change_passes(self):
        repo = make_repo()
        write(repo, "a.txt", "hello\nworld\n")
        git(repo, "add", "-A")
        self.assertEqual(githooks.pre_commit(repo, TERMS), [])

    def test_added_private_term_blocks(self):
        repo = make_repo()
        write(repo, "a.txt", "hello\nZebrowski\n")
        git(repo, "add", "-A")
        self.assertTrue(any("private-term" in p for p in githooks.pre_commit(repo, TERMS)))

    def test_removing_private_text_passes(self):
        repo = make_repo()
        write(repo, "a.txt", "hello\nhost 192.168.7.7\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "leak")
        write(repo, "a.txt", "hello\n")
        git(repo, "add", "-A")
        self.assertEqual(githooks.pre_commit(repo, TERMS), [])

    def test_new_image_outside_fixtures_blocks(self):
        repo = make_repo()
        write(repo, "docs/face.JPG", b"\xff\xd8")
        git(repo, "add", "-A")
        self.assertTrue(any("image" in p for p in githooks.pre_commit(repo, TERMS)))

    def test_new_image_in_fixtures_passes(self):
        repo = make_repo()
        write(repo, "tests/fixtures/chart.png", b"\x89PNG")
        git(repo, "add", "-A")
        self.assertEqual(githooks.pre_commit(repo, TERMS), [])

    def test_non_baton_repo_is_ignored(self):
        repo = make_repo(brief=None)
        write(repo, "a.txt", "Zebrowski\n")
        git(repo, "add", "-A")
        self.assertEqual(githooks.pre_commit(repo, TERMS), [])


class PrePushTest(unittest.TestCase):
    def test_new_branch_scans_commits_not_on_remote(self):
        repo = make_repo()
        write(repo, "a.txt", "hello\nmail alice@corp-mail.test\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "add contact")
        head = git(repo, "rev-parse", "HEAD")
        line = f"refs/heads/main {head} refs/heads/main {ZEROS}\n"
        self.assertTrue(any("email" in p for p in githooks.pre_push(repo, line, TERMS)))

    def test_commit_message_is_scanned(self):
        repo = make_repo()
        base = git(repo, "rev-parse", "HEAD")
        write(repo, "a.txt", "hello\nmore\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "import scans\n\n- the Zebrowski set")
        head = git(repo, "rev-parse", "HEAD")
        line = f"refs/heads/main {head} refs/heads/main {base}\n"
        self.assertTrue(any("private-term" in p for p in githooks.pre_push(repo, line, TERMS)))

    def test_clean_range_and_branch_delete_pass(self):
        repo = make_repo()
        base = git(repo, "rev-parse", "HEAD")
        write(repo, "a.txt", "hello\nmore\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "more")
        head = git(repo, "rev-parse", "HEAD")
        self.assertEqual(githooks.pre_push(repo, f"refs/heads/main {head} refs/heads/main {base}\n", TERMS), [])
        self.assertEqual(githooks.pre_push(repo, f"(delete) {ZEROS} refs/heads/x {base}\n", TERMS), [])


class InstallTest(unittest.TestCase):
    def test_install_writes_tagged_executable_shims(self):
        repo = make_repo()
        written = githooks.install(repo, "/opt/baton/bin/baton")
        self.assertEqual(len(written), 2)
        for path in written:
            content = pathlib.Path(path).read_text()
            self.assertIn(gate.HOOK_TAG, content)
            self.assertIn("/opt/baton/bin/baton", content)
            self.assertTrue(pathlib.Path(path).stat().st_mode & 0o111)

    def test_install_is_repeatable(self):
        repo = make_repo()
        githooks.install(repo, "/opt/baton/bin/baton")
        githooks.install(repo, "/other/bin/baton")
        self.assertIn("/other/bin/baton", pathlib.Path(repo, ".git/hooks/pre-push").read_text())

    def test_install_refuses_foreign_hook(self):
        repo = make_repo()
        pathlib.Path(repo, ".git/hooks/pre-commit").write_text("#!/bin/sh\necho mine\n")
        with self.assertRaises(githooks.HookError):
            githooks.install(repo, "/opt/baton/bin/baton")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=lib python3 -m unittest tests.test_githooks -v`
Expected: FAIL with `ImportError: cannot import name 'githooks'`

- [ ] **Step 3: Write the implementation**

`lib/baton/githooks.py`:

```python
import os
import pathlib

from . import config, gate, run, scan

ZEROS = "0" * 40
HOOKS = ("pre-commit", "pre-push")


class HookError(Exception):
    pass


def added_text(patch):
    """Lines a patch would add: everything except removed and context lines.

    Commit messages are scanned whole and separately, because a message line
    may itself start with "-" (a bullet) and must not be dropped.
    """
    return "\n".join(line for line in patch.splitlines()
                     if not line.startswith("-") and not line.startswith(" "))


def _image_problems(cfg, paths):
    allowed = tuple(cfg.data["fixtures_paths"])
    problems = []
    for path in paths:
        if pathlib.PurePosixPath(path).suffix.lower() not in scan.IMAGE_SUFFIXES:
            continue
        if allowed and path.startswith(allowed):
            continue
        problems.append(f"new image file outside fixtures_paths: {path}")
    return problems


def _check(cfg, patch, new_files, terms, messages=""):
    text = added_text(patch) + "\n" + messages
    findings = scan.scan_text(text, terms, cfg.data["privacy_allow"])
    kinds = sorted({finding.kind for finding in findings})
    problems = [f"privacy: {kind} in added lines or commit message" for kind in kinds]
    return problems + _image_problems(cfg, new_files)


def pre_commit(cwd, terms):
    cfg = config.load(cwd)
    if cfg is None:
        return []
    git = ["git", "-C", cwd]
    patch = run.run(git + ["diff", "--cached", "-U0", "--no-color"])
    new_files = run.run(git + ["diff", "--cached", "--name-only", "--diff-filter=A"]).split("\n")
    return _check(cfg, patch, [name for name in new_files if name], terms)


def pre_push(cwd, stdin_text, terms):
    cfg = config.load(cwd)
    if cfg is None:
        return []
    git = ["git", "-C", cwd]
    problems = []
    for line in stdin_text.splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        _, local_sha, _, remote_sha = parts
        if local_sha == ZEROS:
            continue
        revs = [local_sha, "--not", "--remotes"] if remote_sha == ZEROS else [f"{remote_sha}..{local_sha}"]
        patch = run.run(git + ["log", "-p", "-U0", "--no-color", "--format="] + revs)
        messages = run.run(git + ["log", "--format=%B"] + revs)
        new_files = run.run(git + ["log", "--diff-filter=A", "--name-only", "--format="] + revs).split("\n")
        problems.extend(_check(cfg, patch, [name for name in new_files if name], terms, messages))
    return sorted(set(problems))


def install(repo, baton_bin):
    common = run.run(["git", "-C", repo, "rev-parse", "--git-common-dir"]).strip()
    hooks_dir = pathlib.Path(repo, common, "hooks")
    hooks_dir.mkdir(parents=True, exist_ok=True)
    targets = [hooks_dir / name for name in HOOKS]
    for target in targets:
        if target.exists() and gate.HOOK_TAG not in target.read_text():
            raise HookError(f"{target} exists and is not a Baton hook; merge it by hand")
    written = []
    for target in targets:
        target.write_text(
            "#!/bin/sh\n"
            f"# {gate.HOOK_TAG}: installed by `baton install-hooks`\n"
            f'exec "{baton_bin}" git-hook {target.name} "$@"\n')
        target.chmod(0o755)
        written.append(str(target))
    return written
```

In `lib/baton/cli.py`, add `githooks, scan` to the imports and these commands:

```python
def _report(problems, what):
    if not problems:
        return 0
    print(f"Baton blocked this {what}:", file=sys.stderr)
    for problem in problems:
        print(f"- {problem}", file=sys.stderr)
    return 1


def cmd_git_hook(args):
    name = args[0] if args else ""
    terms = scan.load_terms()
    if name == "pre-commit":
        return _report(githooks.pre_commit(os.getcwd(), terms), "commit")
    if name == "pre-push":
        return _report(githooks.pre_push(os.getcwd(), sys.stdin.read(), terms), "push")
    print(f"baton: unknown git hook {name!r}", file=sys.stderr)
    return 64


def cmd_install_hooks(args):
    baton_bin = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "bin", "baton"))
    try:
        for path in githooks.install(os.getcwd(), baton_bin):
            print(f"installed {path}")
    except githooks.HookError as error:
        print(f"baton: {error}", file=sys.stderr)
        return 1
    return 0
```

and extend the dict: `COMMANDS = {"config": cmd_config, "gate": cmd_gate, "git-hook": cmd_git_hook, "install-hooks": cmd_install_hooks}`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=lib python3 -m unittest discover -s tests -v`
Expected: all tests OK (52 so far)

- [ ] **Step 5: Commit**

```bash
git add lib/baton/githooks.py lib/baton/cli.py tests/test_githooks.py
git commit -m "Add git pre-commit and pre-push privacy hooks"
```

---

### Task 5: Labels

**Files:**
- Create: `lib/baton/labels.py`, `tests/fakes.py`
- Modify: `lib/baton/cli.py` (register `labels`, `set-status`)
- Test: `tests/test_labels.py`

**Interfaces:**
- Consumes: `Config.label`, `Config.status_labels`, `config.STATUSES`, `config.OTHER_LABELS`, `run.run`
- Produces:
  - `Runner = Callable[[list[str]], str]`; production code passes `run.run`
  - `labels.missing(cfg, runner) -> list[str]`
  - `labels.ensure(cfg, runner, create: bool) -> tuple[list[str], list[str]]` (created, still_missing)
  - `labels.set_status(cfg, runner, number: int, status: str) -> None` (`status` is a Baton name such as `status:verification`)
  - `tests/fakes.py`: `FakeRunner(replies: dict[str, str])` with `.calls: list[list[str]]`

- [ ] **Step 1: Write the failing test**

`tests/fakes.py`:

```python
class FakeRunner:
    """Returns the reply whose key is a prefix of the joined command line."""

    def __init__(self, replies=None):
        self.replies = replies or {}
        self.calls = []

    def __call__(self, argv, cwd=None):
        self.calls.append(argv)
        line = " ".join(argv)
        for prefix in sorted(self.replies, key=len, reverse=True):
            if line.startswith(prefix):
                return self.replies[prefix]
        return ""

    def lines(self):
        return [" ".join(call) for call in self.calls]
```

`tests/test_labels.py`:

```python
import copy
import json
import pathlib
import unittest
from baton import config, labels
from tests.fakes import FakeRunner


def make_config(overrides=None):
    data = copy.deepcopy(config.DEFAULTS)
    data["labels"]["map"].update(overrides or {})
    return config.Config(root=pathlib.Path("."), data=data)


def label_list(names):
    return json.dumps([{"name": name} for name in names])


class LabelsTest(unittest.TestCase):
    def test_missing_uses_mapped_names(self):
        cfg = make_config({"status:in-dev": "In Progress"})
        runner = FakeRunner({"gh label list": label_list(["In Progress", "status:ready"])})
        missing = labels.missing(cfg, runner)
        self.assertNotIn("In Progress", missing)
        self.assertNotIn("status:in-dev", missing)
        self.assertIn("status:blocked", missing)
        self.assertEqual(len(missing), 10)

    def test_ensure_creates_missing_labels(self):
        cfg = make_config()
        all_but_one = [n for n in cfg.data["labels"]["map"].values() if n != "stretch"]
        runner = FakeRunner({"gh label list": label_list(all_but_one)})
        created, still = labels.ensure(cfg, runner, create=True)
        self.assertEqual((created, still), (["stretch"], []))
        self.assertTrue(any(line.startswith("gh label create stretch") for line in runner.lines()))

    def test_ensure_without_create_reports_only(self):
        cfg = make_config()
        runner = FakeRunner({"gh label list": label_list([])})
        created, still = labels.ensure(cfg, runner, create=False)
        self.assertEqual(created, [])
        self.assertEqual(len(still), 12)
        self.assertFalse(any("label create" in line for line in runner.lines()))

    def test_set_status_swaps_only_present_status_labels(self):
        cfg = make_config({"status:in-dev": "In Progress"})
        issue = json.dumps({"labels": [{"name": "In Progress"}, {"name": "deliverable"}]})
        runner = FakeRunner({"gh issue view 7": issue})
        labels.set_status(cfg, runner, 7, "status:in-review")
        self.assertEqual(runner.calls[-1], [
            "gh", "issue", "edit", "7", "--add-label", "status:in-review",
            "--remove-label", "In Progress"])

    def test_set_status_rejects_unknown_status(self):
        with self.assertRaises(ValueError):
            labels.set_status(make_config(), FakeRunner(), 7, "status:nope")
```

Create an empty `tests/__init__.py` so `from tests.fakes import FakeRunner` resolves, and run the suite from the repository root.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=lib python3 -m unittest tests.test_labels -v`
Expected: FAIL with `ImportError: cannot import name 'labels'`

- [ ] **Step 3: Write the implementation**

`lib/baton/labels.py`:

```python
import json

from . import config

_STYLE = {
    "status:refinement": ("d4c5f9", "Needs work before it is ready"),
    "status:ready": ("0e8a16", "Meets the definition of ready"),
    "status:in-dev": ("1d76db", "Claimed by a developer"),
    "status:blocked": ("b60205", "Waiting on an answer or dependency"),
    "status:in-review": ("fbca04", "Pull request ready for the acceptance check"),
    "status:verification": ("c2e0c6", "Merged; waiting for the default branch check"),
    "status:complete": ("ededed", "Verified and closed"),
    "type:spike": ("5319e7", "Research; a pull request is optional"),
    "deliverable": ("0052cc", "Accepted into the sprint"),
    "stretch": ("bfd4f2", "In the sprint if time allows"),
    "broken-default-branch": ("b60205", "The default branch is failing; top priority"),
    "needs-maintainer": ("d93f0b", "Waiting for the maintainer"),
}


def _existing(runner):
    out = runner(["gh", "label", "list", "--limit", "500", "--json", "name"])
    return {item["name"] for item in json.loads(out or "[]")}


def missing(cfg, runner):
    existing = _existing(runner)
    return [cfg.label(name) for name in _STYLE if cfg.label(name) not in existing]


def ensure(cfg, runner, create):
    existing = _existing(runner)
    created, still = [], []
    for name, (color, description) in _STYLE.items():
        actual = cfg.label(name)
        if actual in existing:
            continue
        if create:
            runner(["gh", "label", "create", actual, "--color", color,
                    "--description", description])
            created.append(actual)
        else:
            still.append(actual)
    return created, still


def set_status(cfg, runner, number, status):
    if status not in config.STATUSES:
        raise ValueError(f"unknown status {status!r}")
    out = runner(["gh", "issue", "view", str(number), "--json", "labels"])
    present = {item["name"] for item in json.loads(out)["labels"]}
    target = cfg.label(status)
    argv = ["gh", "issue", "edit", str(number), "--add-label", target]
    for other in cfg.status_labels():
        if other != target and other in present:
            argv += ["--remove-label", other]
    runner(argv)
```

In `lib/baton/cli.py`, add `labels, run` to the imports and:

```python
def _require_config():
    cfg = config.load(os.getcwd())
    if cfg is None:
        raise config.ConfigError("not a Baton repository: .baton/brief.md not found")
    return cfg


def cmd_labels(args):
    cfg = _require_config()
    create = cfg.data["labels"]["auto_create"] or "--create" in args
    created, still = labels.ensure(cfg, run.run, create=create)
    for name in created:
        print(f"created {name}")
    for name in still:
        print(f"missing {name}", file=sys.stderr)
    return 1 if still else 0


def cmd_set_status(args):
    if len(args) != 2:
        print("usage: baton set-status <issue-number> <status:name>", file=sys.stderr)
        return 64
    labels.set_status(_require_config(), run.run, int(args[0]), args[1])
    return 0
```

Register both: `"labels": cmd_labels, "set-status": cmd_set_status`. In `main`, also catch `run.RunError` and `ValueError` alongside `config.ConfigError`, printing the message and returning 2.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=lib python3 -m unittest discover -s tests -v`
Expected: all tests OK (57 so far)

- [ ] **Step 5: Commit**

```bash
git add lib/baton/labels.py lib/baton/cli.py tests/fakes.py tests/__init__.py tests/test_labels.py
git commit -m "Add label setup and status switching"
```

---

### Task 6: `merge-pr`

**Files:**
- Create: `lib/baton/merge.py`
- Modify: `lib/baton/cli.py` (register `merge-pr`)
- Test: `tests/test_merge.py`

**Interfaces:**
- Consumes: `Config`, `labels.set_status`, `gate.MARKER`, `Runner`, `tests.fakes.FakeRunner`
- Produces:
  - `merge.PR_FIELDS: str` (the `--json` field list)
  - `merge.check(pr: dict, cfg: Config, owner: str) -> list[str]` (pure; problems)
  - `merge.merge(number: int, cfg: Config, runner) -> list[str]` (problems; empty means merged)
  - Maintainer approval of protected paths: a pull request comment by the repository owner, without the Baton marker, whose body starts with `/baton approve`, created after the head commit.

- [ ] **Step 1: Write the failing test**

`tests/test_merge.py`:

```python
import copy
import json
import pathlib
import unittest
from baton import config, merge
from tests.fakes import FakeRunner

OWNER = "octo"


def make_config(**overrides):
    data = copy.deepcopy(config.DEFAULTS)
    data.update(overrides)
    return config.Config(root=pathlib.Path("."), data=data)


def make_pr(**overrides):
    pr = {
        "number": 9, "state": "OPEN", "isDraft": False, "mergeable": "MERGEABLE",
        "headRefOid": "abc123", "headRefName": "issue-4-flag",
        "body": "Adds the flag.\n\nCloses #4",
        "files": [{"path": "src/tool.py"}],
        "statusCheckRollup": [{"__typename": "CheckRun", "name": "test",
                               "status": "COMPLETED", "conclusion": "SUCCESS"}],
        "closingIssuesReferences": [{"number": 4}],
        "commits": [{"oid": "abc123", "committedDate": "2026-10-01T10:00:00Z"}],
        "comments": [],
    }
    pr.update(overrides)
    return pr


def problems(**overrides):
    return merge.check(make_pr(**overrides), make_config(), OWNER)


class CheckTest(unittest.TestCase):
    def test_good_pr_has_no_problems(self):
        self.assertEqual(problems(), [])

    def test_draft_closed_and_conflicted_are_refused(self):
        self.assertTrue(any("draft" in p for p in problems(isDraft=True)))
        self.assertTrue(any("open" in p for p in problems(state="MERGED")))
        self.assertTrue(any("mergeable" in p for p in problems(mergeable="CONFLICTING")))
        self.assertTrue(any("mergeable" in p for p in problems(mergeable="UNKNOWN")))

    def test_no_checks_registered_is_refused(self):
        self.assertTrue(any("no CI checks" in p for p in problems(statusCheckRollup=[])))

    def test_no_checks_allowed_when_configured(self):
        cfg = make_config(merge={"allow_no_ci": True})
        self.assertEqual(merge.check(make_pr(statusCheckRollup=[]), cfg, OWNER), [])

    def test_pending_and_failed_checks_are_refused(self):
        pending = [{"__typename": "CheckRun", "name": "test", "status": "IN_PROGRESS", "conclusion": ""}]
        failed = [{"__typename": "CheckRun", "name": "test", "status": "COMPLETED", "conclusion": "FAILURE"}]
        status = [{"__typename": "StatusContext", "context": "ext", "state": "PENDING"}]
        for rollup in (pending, failed, status):
            with self.subTest(rollup=rollup):
                self.assertTrue(any("check" in p for p in problems(statusCheckRollup=rollup)))

    def test_skipped_check_and_success_status_pass(self):
        rollup = [{"__typename": "CheckRun", "name": "a", "status": "COMPLETED", "conclusion": "SKIPPED"},
                  {"__typename": "StatusContext", "context": "ext", "state": "SUCCESS"}]
        self.assertEqual(problems(statusCheckRollup=rollup), [])

    def test_must_close_exactly_one_issue(self):
        self.assertTrue(any("exactly one" in p for p in problems(closingIssuesReferences=[])))
        self.assertTrue(any("exactly one" in p for p in problems(
            closingIssuesReferences=[{"number": 4}, {"number": 5}])))

    def test_stray_closing_keyword_is_refused(self):
        body = "Closes #4\n\nDeferred rather than a fix: #55"
        self.assertTrue(any("#55" in p for p in problems(body=body)))

    def test_protected_path_needs_approval(self):
        files = [{"path": ".github/workflows/ci.yml"}]
        self.assertTrue(any("protected" in p for p in problems(files=files)))

    def test_owner_approval_after_head_commit_unlocks(self):
        files = [{"path": ".github/workflows/ci.yml"}]
        ok = [{"author": {"login": OWNER}, "body": "/baton approve", "createdAt": "2026-10-01T11:00:00Z"}]
        self.assertEqual(problems(files=files, comments=ok), [])

    def test_stale_marked_or_foreign_approval_does_not_unlock(self):
        files = [{"path": ".baton/brief.md"}]
        stale = [{"author": {"login": OWNER}, "body": "/baton approve", "createdAt": "2026-10-01T09:00:00Z"}]
        marked = [{"author": {"login": OWNER}, "createdAt": "2026-10-01T11:00:00Z",
                   "body": "<!-- baton:developer:status -->\n/baton approve"}]
        foreign = [{"author": {"login": "mallory"}, "body": "/baton approve", "createdAt": "2026-10-01T11:00:00Z"}]
        for comments in (stale, marked, foreign):
            with self.subTest(comments=comments):
                self.assertTrue(any("protected" in p for p in problems(files=files, comments=comments)))


class MergeTest(unittest.TestCase):
    def replies(self, pr, issue_state="OPEN"):
        return {
            "gh repo view": json.dumps({"owner": {"login": OWNER}}),
            "gh pr view 9": json.dumps(pr),
            "gh issue view 4 --json state": json.dumps({"state": issue_state}),
            "gh issue view 4 --json labels": json.dumps({"labels": [{"name": "status:in-review"}]}),
            "git worktree list": "worktree /repo\nbranch refs/heads/main\n\n"
                                 "worktree /wt/issue-4\nbranch refs/heads/issue-4-flag\n",
        }

    def test_refused_pr_is_not_merged(self):
        runner = FakeRunner(self.replies(make_pr(isDraft=True)))
        self.assertTrue(merge.merge(9, make_config(), runner))
        self.assertFalse(any("pr merge" in line for line in runner.lines()))

    def test_merge_pins_head_closes_issue_sets_status_and_cleans_own_branch(self):
        runner = FakeRunner(self.replies(make_pr()))
        self.assertEqual(merge.merge(9, make_config(), runner), [])
        lines = runner.lines()
        self.assertIn("gh pr merge 9 --merge --match-head-commit abc123", lines)
        self.assertIn("gh issue close 4", lines)
        self.assertIn("gh issue edit 4 --add-label status:verification --remove-label status:in-review", lines)
        self.assertIn("git worktree remove --force /wt/issue-4", lines)
        self.assertIn("git branch -D issue-4-flag", lines)
        self.assertIn("git push origin --delete issue-4-flag", lines)
        self.assertFalse(any("/repo" in line and "remove" in line for line in lines))

    def test_already_closed_issue_is_not_closed_again(self):
        runner = FakeRunner(self.replies(make_pr(), issue_state="CLOSED"))
        merge.merge(9, make_config(), runner)
        self.assertNotIn("gh issue close 4", runner.lines())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=lib python3 -m unittest tests.test_merge -v`
Expected: FAIL with `ImportError: cannot import name 'merge'`

- [ ] **Step 3: Write the implementation**

`lib/baton/merge.py`:

```python
"""The only way a Baton pull request is merged.

Adapted from the acceptance rules in the design spec, section 6, and the
prototype sprint's lesson: never merge until CI has concluded SUCCESS on
the head commit.
"""
import json
import re

from . import gate, labels

PR_FIELDS = ("number,state,isDraft,mergeable,headRefOid,headRefName,body,files,"
             "statusCheckRollup,closingIssuesReferences,commits,comments")
_CLOSING = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b:?\s+#(\d+)", re.I)
_PASSING = {"SUCCESS", "SKIPPED", "NEUTRAL"}


def _check_ci(pr, cfg):
    rollup = pr.get("statusCheckRollup") or []
    if not rollup:
        if cfg.data["merge"]["allow_no_ci"]:
            return []
        return ["no CI checks are registered on the head commit yet"]
    problems = []
    for item in rollup:
        if item.get("__typename") == "StatusContext":
            name, done, result = item.get("context"), item.get("state") != "PENDING", item.get("state")
        else:
            name, done, result = item.get("name"), item.get("status") == "COMPLETED", item.get("conclusion")
        if not done:
            problems.append(f"check {name!r} has not finished")
        elif result not in _PASSING:
            problems.append(f"check {name!r} concluded {result}")
    return problems


def _approved(pr, owner):
    head_time = pr["commits"][-1]["committedDate"] if pr.get("commits") else ""
    for comment in pr.get("comments") or []:
        body = comment.get("body", "")
        if (comment.get("author", {}).get("login") == owner
                and gate.MARKER not in body
                and body.strip().startswith("/baton approve")
                and comment.get("createdAt", "") > head_time):
            return True
    return False


def check(pr, cfg, owner):
    problems = []
    if pr.get("state") != "OPEN":
        problems.append(f"pull request is not open (state {pr.get('state')})")
    if pr.get("isDraft"):
        problems.append("pull request is still a draft")
    if pr.get("mergeable") != "MERGEABLE":
        problems.append(f"pull request is not mergeable ({pr.get('mergeable')})")
    problems.extend(_check_ci(pr, cfg))

    closing = {ref["number"] for ref in pr.get("closingIssuesReferences") or []}
    if len(closing) != 1:
        problems.append(f"pull request must close exactly one issue, found {sorted(closing)}")
    for number in sorted({int(n) for n in _CLOSING.findall(pr.get("body") or "")} - closing):
        problems.append(f"stray closing keyword would close #{number}")

    protected = tuple(cfg.data["protected_paths"])
    touched = [f["path"] for f in pr.get("files") or [] if protected and f["path"].startswith(protected)]
    if touched and not _approved(pr, owner):
        problems.append("protected paths changed without maintainer approval "
                        f"(`/baton approve` comment after the last commit): {', '.join(touched)}")
    return problems


def _worktree_for(branch, runner):
    path = None
    for line in runner(["git", "worktree", "list", "--porcelain"]).splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line == f"branch refs/heads/{branch}":
            return path
    return None


def merge(number, cfg, runner):
    owner = json.loads(runner(["gh", "repo", "view", "--json", "owner"]))["owner"]["login"]
    pr = json.loads(runner(["gh", "pr", "view", str(number), "--json", PR_FIELDS]))
    problems = check(pr, cfg, owner)
    if problems:
        return problems

    runner(["gh", "pr", "merge", str(number), "--merge", "--match-head-commit", pr["headRefOid"]])

    issue = pr["closingIssuesReferences"][0]["number"]
    state = json.loads(runner(["gh", "issue", "view", str(issue), "--json", "state"]))["state"]
    if state == "OPEN":
        runner(["gh", "issue", "close", str(issue)])
    labels.set_status(cfg, runner, issue, "status:verification")

    branch = pr["headRefName"]
    worktree = _worktree_for(branch, runner)
    if worktree:
        runner(["git", "worktree", "remove", "--force", worktree])
    runner(["git", "branch", "-D", branch])
    runner(["git", "push", "origin", "--delete", branch])
    return []
```

In `lib/baton/cli.py`, add `merge` to the imports and:

```python
def cmd_merge_pr(args):
    if len(args) != 1 or not args[0].isdigit():
        print("usage: baton merge-pr <pull-request-number>", file=sys.stderr)
        return 64

    def tolerant(argv, cwd=None):
        cleanup = argv[:3] in (["git", "branch", "-D"], ["git", "push", "origin"],
                               ["git", "worktree", "remove"])
        try:
            return run.run(argv, cwd)
        except run.RunError as error:
            if not cleanup:
                raise
            print(f"baton: cleanup skipped: {error}", file=sys.stderr)
            return ""

    problems = merge.merge(int(args[0]), _require_config(), tolerant)
    if problems:
        print(f"Baton refused to merge #{args[0]}:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    print(f"merged #{args[0]}")
    return 0
```

Register `"merge-pr": cmd_merge_pr`. Cleanup failures (for example a branch GitHub already deleted) are reported but do not undo a completed merge.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=lib python3 -m unittest discover -s tests -v`
Expected: all tests OK (71)

- [ ] **Step 5: Commit**

```bash
git add lib/baton/merge.py lib/baton/cli.py tests/test_merge.py
git commit -m "Add merge-pr with acceptance checks"
```

---

### Task 7: `init` skill, brief template and CI

**Files:**
- Create: `templates/brief.md`, `skills/init/SKILL.md`, `.github/workflows/ci.yml`
- Modify: `README.md`
- Test: `tests/test_template.py`

**Interfaces:**
- Consumes: `frontmatter.split`, `frontmatter.parse`, `config.DEFAULTS`, the `baton` commands `config`, `labels`, `install-hooks`
- Produces: `/baton:init`; `templates/brief.md` whose front matter parses to exactly `config.DEFAULTS`

- [ ] **Step 1: Write the failing test**

`tests/test_template.py`:

```python
import pathlib
import unittest
from baton import config, frontmatter

ROOT = pathlib.Path(__file__).resolve().parent.parent


class TemplateTest(unittest.TestCase):
    def test_template_front_matter_equals_defaults(self):
        meta, body = frontmatter.split((ROOT / "templates" / "brief.md").read_text())
        self.assertEqual(frontmatter.parse(meta), config.DEFAULTS)
        self.assertIn("## Verification", body)

    def test_plugin_files_exist(self):
        for path in (".claude-plugin/plugin.json", "hooks/hooks.json",
                     "skills/init/SKILL.md", "bin/baton"):
            self.assertTrue((ROOT / path).is_file(), path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=lib python3 -m unittest tests.test_template -v`
Expected: FAIL with `FileNotFoundError` for `templates/brief.md`

- [ ] **Step 3: Write the files**

`templates/brief.md`:

```markdown
---
developer:
  model: sonnet
  max_parallel: 2
refine:
  may_mark_ready: true        # false: refine proposes, the maintainer applies the label
labels:
  auto_create: true           # false: a missing label stops the run
  map:                        # Baton's name: the label this repository uses
    status:refinement: "status:refinement"
    status:ready: "status:ready"
    status:in-dev: "status:in-dev"
    status:blocked: "status:blocked"
    status:in-review: "status:in-review"
    status:verification: "status:verification"
    status:complete: "status:complete"
    type:spike: "type:spike"
    deliverable: "deliverable"
    stretch: "stretch"
    broken-default-branch: "broken-default-branch"
    needs-maintainer: "needs-maintainer"
merge:
  allow_no_ci: false          # true only for repositories with no CI at all
protected_paths: [".github/", ".baton/", ".claude/"]
fixtures_paths: []            # the only places new image files may be added
privacy_allow: []             # literal strings the privacy scan should ignore
budgets: {}
---
# Baton brief

Rules for agents working in this repository. Keep private details (hosts, paths,
credentials) out of this file; they belong in the local brief outside the repository.

## Verification

The exact command that must pass before a pull request is opened:

    (fill in, for example: scripts/test.sh)

## Definition of done

- The verification command passes.
- New behaviour has tests.
- (add project rules)

## Conventions

- (branching, commit style, documentation rules)
```

`skills/init/SKILL.md`:

```markdown
---
name: init
description: Use when setting up a repository to work with Baton sprints, when .baton/brief.md is missing, or when Baton reports missing labels or git hooks.
---

# Baton init

Prepare the current repository for Baton. Work through every step; do not skip one because it
looks done. Workflow adapted from the
[GitLab Engineering Workflow](https://handbook.gitlab.com/handbook/engineering/workflow/).

## Steps

1. **Confirm the repository.** Run `gh repo view --json nameWithOwner,visibility,defaultBranchRef`.
   Tell the maintainer which repository will be set up and stop if it is not the one they meant.

2. **Write the repo brief.** If `.baton/brief.md` does not exist, copy
   `${CLAUDE_PLUGIN_ROOT}/templates/brief.md` to `.baton/brief.md`. Then fill in the body from the
   repository itself: find the test command (CI workflow, `Makefile`, `package.json`,
   `scripts/`), and the conventions in `AGENTS.md`, `CLAUDE.md` or `CONTRIBUTING.md`. Ask the
   maintainer only for what the repository does not show.

3. **Map existing labels.** Run `gh label list --limit 500 --json name,description`. If the
   repository already has labels that mean the same as a Baton label (for example `In Progress`
   for `status:in-dev`), propose the mapping to the maintainer and write the agreed names into
   `labels.map`. Never rename or delete an existing label.

4. **Create missing labels.** Run `baton labels`. With `labels.auto_create: false`, show the
   maintainer the missing list and run `baton labels --create` only after they agree.

5. **Create the local brief.** Create `~/.baton/<owner>-<repo>/brief.local.md` if it is missing,
   with headings `Hosts`, `Paths` and `Credentials` for the maintainer to fill in. Create
   `~/.baton/private-terms.txt` if it is missing, with one comment line explaining that each line
   is a name, hostname or folder name that must never be published. Ask the maintainer to add
   their terms. Do not suggest terms yourself and never print the file's contents.

6. **Install the git hooks.** Run `baton install-hooks`. If it reports an existing hook that is
   not Baton's, show the maintainer and stop; do not overwrite it.

7. **Verify.** Run `baton config` and confirm it prints the settings. Run `baton labels` and
   confirm it exits 0.

8. **Commit.** Commit `.baton/brief.md` on a branch and open a pull request. `.baton/` is a
   protected path, so the maintainer merges this one.

## Rules

- Nothing private goes in `.baton/brief.md`: no hostnames, IP addresses, absolute paths or names.
- If `baton` is not found, the plugin's `bin/` is not on `PATH`; call
  `${CLAUDE_PLUGIN_ROOT}/bin/baton` instead.
```

`.github/workflows/ci.yml`:

```yaml
name: ci
on:
  pull_request:
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: PYTHONPATH=lib python3 -m unittest discover -s tests -v
```

Replace `README.md` with:

```markdown
# Baton

A Claude Code plugin that runs software sprints on a forge repository: an orchestrator agent
dispatches developer agents in parallel, checks and merges their pull requests, and writes a
retrospective. The forge holds all state, so any session can resume a sprint.

The workflow is adapted from the GitLab handbook's
[Engineering Workflow](https://handbook.gitlab.com/handbook/engineering/workflow/) and
[Product Development Flow](https://handbook.gitlab.com/handbook/product-development/how-we-work/product-development-flow/).

- Design: [docs/superpowers/specs/2026-10-01-baton-design.md](docs/superpowers/specs/2026-10-01-baton-design.md)
- Plans: [docs/superpowers/plans/](docs/superpowers/plans/)

## Status

Foundation only: `/baton:init`, the privacy publish gate, git hooks, label setup and `baton merge-pr`.
The `develop`, `refine`, `sprint-plan` and `sprint` skills come in later plans.

## Commands

| Command | Purpose |
|---|---|
| `baton config` | Print the resolved settings from `.baton/brief.md` |
| `baton labels [--create]` | Check or create the labels Baton uses |
| `baton set-status <issue> <status:name>` | Switch an issue's status label |
| `baton install-hooks` | Install the `pre-commit` and `pre-push` privacy hooks |
| `baton merge-pr <number>` | Run the acceptance checks and merge |

## Tests

    PYTHONPATH=lib python3 -m unittest discover -s tests -v
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=lib python3 -m unittest discover -s tests -v`
Expected: all tests OK (73)

- [ ] **Step 5: Try the plugin in a scratch repository**

```bash
tmp=$(mktemp -d) && git -C "$tmp" init -q -b main && mkdir "$tmp/.baton" \
  && cp templates/brief.md "$tmp/.baton/brief.md" \
  && (cd "$tmp" && "$OLDPWD/bin/baton" config >/dev/null && "$OLDPWD/bin/baton" install-hooks)
printf '{"tool_name":"Bash","cwd":"%s","tool_input":{"command":"gh issue create --title t --body 192.168.7.7"}}' "$tmp" | bin/baton gate; echo "exit $?"
```
Expected: two `installed ...` lines, then the gate message naming `private-ip` and `exit 2`.

- [ ] **Step 6: Commit**

```bash
git add templates skills .github README.md tests/test_template.py
git commit -m "Add init skill, brief template and CI"
```

---

## Self-review notes

- Spec section 2 (components): `init`, publish gate, `merge-pr` and both briefs are covered here; `refine`, `develop`, `sprint-plan`, `sprint`, the subagent definition and `references/github.md` belong to plans 2 and 3.
- Spec section 3 (settings): every key is in `config.DEFAULTS` and the template. Two keys were added that the spec does not list: `merge.allow_no_ci` and `privacy_allow`.
- Spec section 9.1: command text, body files, commit diffs, commit messages, new images and the no-reply rule (a brief convention) are covered. Section 9.2's `protected_paths` rule is in `merge.check`. Section 9.3's marker rule is in the gate.
- Known gaps, accepted for this plan: the gate cannot see a body built by a script at run time (the `pre-push` hook still covers git content); `gh api` reads are scanned as if they were writes, which can only produce a false block.
