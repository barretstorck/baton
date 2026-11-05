"""The docs must not tell an agent to run a `gh` command the gate refuses."""

import glob
import json
import os
import re
import unittest

from baton import gate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPAN = re.compile(r"`(gh [^`]*)`")


def doc_files():
    patterns = ["skills/*/SKILL.md", "agents/*.md", "references/*.md", "README.md"]
    for pattern in patterns:
        yield from sorted(glob.glob(os.path.join(ROOT, pattern)))


class DocsMatchGate(unittest.TestCase):
    def test_no_documented_gh_command_is_refused(self):
        bad = []
        files = list(doc_files())
        self.assertTrue(files)
        for path in files:
            with open(path, encoding="utf-8") as f:
                for number, line in enumerate(f, 1):
                    if "refused" in line:
                        continue
                    for span in SPAN.findall(line):
                        if gate.decide(span, ROOT, []):
                            bad.append(f"{os.path.relpath(path, ROOT)}:{number}: {span}")
        self.assertEqual(bad, [], "docs name gh commands the gate refuses")


class DocsGhStateValues(unittest.TestCase):
    """Each documented `gh <command> --state <value>` uses a value that command accepts."""

    STATES = {
        "issue list": {"open", "closed", "all"},
        "pr list": {"open", "closed", "merged", "all"},
        "search issues": {"open", "closed"},
        "search prs": {"open", "closed"},
    }
    STATE = re.compile(r"--state[ =](\S+)")

    def test_documented_state_values_are_accepted(self):
        bad = []
        for path in doc_files():
            with open(path, encoding="utf-8") as f:
                for number, line in enumerate(f, 1):
                    for span in SPAN.findall(line):
                        command = " ".join(span.split()[1:3])
                        for value in self.STATE.findall(span):
                            if value not in self.STATES.get(command, ()):
                                bad.append(f"{os.path.relpath(path, ROOT)}:{number}: {span}")
        self.assertEqual(bad, [], "docs pass a --state value the gh command refuses")


class ReferencedPaths(unittest.TestCase):
    PATH = re.compile(
        r"(?:\$\{CLAUDE_PLUGIN_ROOT\}/)?((?:references|templates)/[A-Za-z0-9_.-]+\.md)")

    def test_referenced_files_exist(self):
        bad = []
        for path in doc_files():
            with open(path, encoding="utf-8") as f:
                for number, line in enumerate(f, 1):
                    for ref in self.PATH.findall(line):
                        if not os.path.exists(os.path.join(ROOT, ref)):
                            bad.append(f"{os.path.relpath(path, ROOT)}:{number}: {ref}")
        self.assertEqual(bad, [], "docs name files that do not exist")

    def test_command_rows_are_written_once(self):
        seen = {}
        dup = []
        for path in sorted(glob.glob(os.path.join(ROOT, "references/github*.md"))):
            name = os.path.basename(path)
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if not line.startswith("|"):
                        continue
                    cell = line.split("|")[1].strip()
                    if cell in ("Step", "") or set(cell) <= set("-: "):
                        continue
                    if cell in seen and seen[cell] != name:
                        dup.append(f"{cell}: {seen[cell]} and {name}")
                    seen.setdefault(cell, name)
        self.assertEqual(dup, [], "rows written in more than one file")

    def test_skills_name_their_command_files(self):
        core, shared = "references/github.md", "references/github-shared.md"
        roles = {
            "develop": [core],
            "review": [core, shared, "references/github-reviewer.md"],
            "refine": [core, shared, "references/github-refiner.md"],
            "research": [core, shared],
            "triage": [core, shared, "references/github-triager.md"],
            "sprint": [core, shared, "references/github-orchestrator.md"],
            "sprint-plan": [core, shared, "references/github-orchestrator.md"],
            "open-questions": [core, shared, "references/github-orchestrator.md"],
        }
        every = sorted(
            os.path.relpath(p, ROOT)
            for p in glob.glob(os.path.join(ROOT, "references/github*.md"))
        )
        for skill, want in roles.items():
            with open(os.path.join(ROOT, "skills", skill, "SKILL.md"), encoding="utf-8") as f:
                text = f.read()
            named = [
                r for r in every
                if re.search(r"references/" + re.escape(os.path.basename(r)) + r"(?![A-Za-z0-9_-])", text)
            ]
            self.assertEqual(named, sorted(want), f"{skill} names the wrong command files")

    def test_sprint_references_are_named(self):
        with open(os.path.join(ROOT, "skills/sprint/SKILL.md"), encoding="utf-8") as f:
            skill = f.read()
        for path in sorted(glob.glob(os.path.join(ROOT, "references/sprint-*.md"))):
            name = os.path.basename(path)
            self.assertIn(name, skill, f"{name} is not named in the sprint skill")


class DenyRules(unittest.TestCase):
    """The README's `permissions.deny` rules for the identity keys."""

    def _readme_deny(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
            lines = f.read().splitlines()
        blocks, cur = [], []
        for line in lines:
            if line.startswith("    "):
                cur.append(line[4:])
            elif cur and not line.strip():
                cur.append("")
            elif cur:
                blocks.append("\n".join(cur))
                cur = []
        if cur:
            blocks.append("\n".join(cur))
        found = [b for b in blocks if '"permissions"' in b]
        self.assertTrue(found, "no indented code block with permissions in README.md")
        data = json.loads(found[0])
        return data["permissions"]["deny"]

    def _glob_regex(self, pattern):
        out, i = "", 0
        while i < len(pattern):
            if pattern.startswith("**/", i):
                out += "(?:[^/]+/)*"
                i += 3
            elif pattern[i] == "*":
                out += "[^/]*"
                i += 1
            else:
                out += re.escape(pattern[i])
                i += 1
        return re.compile("^" + out + "$")

    def _matches(self, path):
        rules = self._readme_deny()
        regexes = []
        for rule in rules:
            m = re.match(r"^Read\((.+)\)$", rule)
            self.assertTrue(m, rule)
            regexes.append(self._glob_regex(m.group(1)))
        return any(r.match(path) for r in regexes)

    def test_rules_are_read_rules_anchored_at_home(self):
        rules = self._readme_deny()
        self.assertEqual(len(rules), 2)
        for rule in rules:
            self.assertRegex(rule, r"^Read\(~/[^\s()]+\)$")

    def test_rules_cover_keys_and_identity(self):
        for path in (
            "~/.baton/keys/baton.pem",
            "~/.baton/baton.pem",
            "~/.baton/owner-repo/identity.toml",
            "~/.baton/identity.toml",
        ):
            self.assertTrue(self._matches(path), path)

    def test_rules_leave_logs_readable(self):
        for path in (
            "~/.baton/sprint.log",
            "~/.baton/triage.log",
            "~/.baton/owner-repo/brief.md",
        ):
            self.assertFalse(self._matches(path), path)


if __name__ == "__main__":
    unittest.main()
