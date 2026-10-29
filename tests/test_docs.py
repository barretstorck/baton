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
