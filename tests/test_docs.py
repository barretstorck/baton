"""The docs must not tell an agent to run a `gh` command the gate refuses."""

import glob
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


if __name__ == "__main__":
    unittest.main()
