import ast
import collections
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent


class DuplicateClassTest(unittest.TestCase):
    def test_no_duplicate_top_level_classes(self):
        found = {}
        for path in sorted(TESTS.glob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            names = collections.Counter(
                node.name for node in tree.body if isinstance(node, ast.ClassDef))
            dupes = sorted(name for name, count in names.items() if count > 1)
            if dupes:
                found[path.name] = dupes
        self.assertEqual(found, {}, "a later class definition hides the earlier one")
