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

    def test_unterminated_front_matter_raises(self):
        with self.assertRaises(config.ConfigError):
            config.load(str(make_repo("---\nprivacy_allow: []\n")))

    def test_wrong_types_raise(self):
        for meta in ('privacy_allow: 5', 'privacy_allow: "0123456789."',
                     'labels:\n  auto_create: "no"', 'protected_paths: [1, 2]',
                     'labels:\n  map:\n    stretch: 5', 'merge: []'):
            with self.subTest(meta=meta):
                with self.assertRaises(config.ConfigError):
                    config.load(str(make_repo(f"---\n{meta}\n---\n")))

    def test_reviewer_and_review_defaults(self):
        self.assertEqual(config.DEFAULTS["reviewer"], {"model": "sonnet"})
        self.assertEqual(config.DEFAULTS["review"], {"required": True, "max_rounds": 2})

    def test_review_rounds_must_be_positive(self):
        with self.assertRaises(config.ConfigError):
            config.load(str(make_repo("---\nreview:\n  max_rounds: 0\n---\n")))

    def test_load_from_subdirectory(self):
        root = make_repo("x\n")
        (root / "src").mkdir()
        self.assertEqual(config.load(str(root / "src")).root.resolve(), root.resolve())
