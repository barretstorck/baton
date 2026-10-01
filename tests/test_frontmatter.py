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

    def test_empty_front_matter(self):
        self.assertEqual(frontmatter.split("---\n---\nbody\n"), ("", "body\n"))

    def test_unterminated_front_matter_raises(self):
        with self.assertRaises(ValueError):
            frontmatter.split("---\nprivacy_allow: []\n# no closing line\n")

    def test_tab_indentation_raises(self):
        with self.assertRaises(ValueError):
            frontmatter.parse("labels:\n\tauto_create: false\n")

    def test_parse_rejects_garbage(self):
        with self.assertRaises(ValueError):
            frontmatter.parse("this is not a mapping")
