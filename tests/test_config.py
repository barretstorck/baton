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

    def test_forbid_added_default_and_valid(self):
        self.assertEqual(config.DEFAULTS["forbid_added"], {})
        brief = "---\nforbid_added:\n  \"pyproject.toml\": '^\\s*\"src/[^\"]*\"\\s*=\\s*\\['\n---\n"
        cfg = config.load(str(make_repo(brief)))
        self.assertEqual(list(cfg.data["forbid_added"]), ["pyproject.toml"])

    def test_forbid_added_invalid_raises(self):
        for meta in ('forbid_added: []', 'forbid_added:\n  "a": 5', 'forbid_added:\n  "a": "("'):
            with self.subTest(meta=meta):
                with self.assertRaises(config.ConfigError):
                    config.load(str(make_repo(f"---\n{meta}\n---\n")))

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
        self.assertEqual(config.DEFAULTS["reviewer"], {"model": "sonnet", "effort": "medium"})
        self.assertEqual(config.DEFAULTS["review"], {"required": True, "max_rounds": 2})

    def test_developer_default_is_one_at_a_time(self):
        self.assertEqual(config.DEFAULTS["developer"]["max_parallel"], 1)
        self.assertEqual(config.load(str(make_repo("# none\n"))).data["developer"]["max_parallel"], 1)

    def test_developer_effort_default_override_and_rejection(self):
        self.assertEqual(config.load(str(make_repo("# none\n"))).data["developer"]["effort"], "medium")
        cfg = config.load(str(make_repo("---\ndeveloper:\n  effort: high\n---\n")))
        self.assertEqual(cfg.data["developer"]["effort"], "high")
        with self.assertRaisesRegex(config.ConfigError, "developer.effort"):
            config.load(str(make_repo("---\ndeveloper:\n  effort: turbo\n---\n")))

    def test_refiner_defaults(self):
        self.assertEqual(config.DEFAULTS["refiner"], {"model": "sonnet", "effort": "medium"})
        self.assertEqual(config.load(str(make_repo("# none\n"))).data["refiner"],
                         {"model": "sonnet", "effort": "medium"})

    def test_reviewer_and_refiner_effort_and_model_override_and_rejection(self):
        for role in ("developer", "reviewer", "refiner"):
            with self.subTest(role=role):
                cfg = config.load(str(make_repo(f"---\n{role}:\n  model: opus\n  effort: high\n---\n")))
                self.assertEqual(cfg.data[role]["effort"], "high")
                self.assertEqual(cfg.data[role]["model"], "opus")
                with self.assertRaisesRegex(config.ConfigError, f"{role}.effort"):
                    config.load(str(make_repo(f"---\n{role}:\n  effort: turbo\n---\n")))

    def test_developer_effort_alone_leaves_others_medium(self):
        cfg = config.load(str(make_repo("---\ndeveloper:\n  effort: high\n---\n")))
        self.assertEqual(cfg.data["reviewer"]["effort"], "medium")
        self.assertEqual(cfg.data["refiner"]["effort"], "medium")

    def test_review_rounds_must_be_positive(self):
        with self.assertRaises(config.ConfigError):
            config.load(str(make_repo("---\nreview:\n  max_rounds: 0\n---\n")))

    def test_verify_defaults_and_override(self):
        self.assertEqual(config.DEFAULTS["verify"], {"command": "", "timeout_minutes": 10})
        self.assertEqual(config.load(str(make_repo("# none\n"))).data["verify"],
                         {"command": "", "timeout_minutes": 10})
        cfg = config.load(str(make_repo('---\nverify:\n  command: "make test {worktree}"\n  timeout_minutes: 3\n---\n')))
        self.assertEqual(cfg.data["verify"], {"command": "make test {worktree}", "timeout_minutes": 3})

    def test_verify_empty_command_line(self):
        cfg = config.load(str(make_repo('---\nverify:\n  command: ""\n---\n')))
        self.assertEqual(cfg.data["verify"]["command"], "")

    def test_verify_wrong_types_and_small_timeout(self):
        for meta in ('verify:\n  command: 5', 'verify:\n  command:', 'verify:\n  timeout_minutes: "10"',
                     'verify:\n  timeout_minutes: 1.5', 'verify:\n  timeout_minutes: 0',
                     'verify:\n  timeout_minutes: -2'):
            with self.subTest(meta=meta):
                with self.assertRaises(config.ConfigError):
                    config.load(str(make_repo(f"---\n{meta}\n---\n")))

    def test_work_mix_default_and_valid(self):
        self.assertEqual(config.DEFAULTS["work_mix"], {})
        self.assertEqual(config.load(str(make_repo("# none\n"))).data["work_mix"], {})
        brief = "---\nwork_mix:\n  feature: 60\n  maintenance: 30\n  bug: 10\n---\n"
        cfg = config.load(str(make_repo(brief)))
        self.assertEqual(cfg.data["work_mix"], {"feature": 60, "maintenance": 30, "bug": 10})

    def test_work_mix_invalid_raises(self):
        for meta in ("feature: 60", "feature: 50\n  chore: 50", "feature: 120\n  bug: -20", "feature: lots"):
            with self.assertRaises(config.ConfigError, msg=meta):
                config.load(str(make_repo(f"---\nwork_mix:\n  {meta}\n---\n")))

    def test_docs_dir_default_and_override(self):
        self.assertEqual(config.DEFAULTS["docs"], {"dir": "docs/baton"})
        self.assertEqual(config.load(str(make_repo("# none\n"))).data["docs"]["dir"], "docs/baton")
        cfg = config.load(str(make_repo("---\ndocs:\n  dir: notes/plans\n---\n")))
        self.assertEqual(cfg.data["docs"]["dir"], "notes/plans")

    def test_docs_dir_is_normalised(self):
        for given in ("docs//baton", "./docs/baton/", "docs/./baton"):
            brief = f'---\ndocs:\n  dir: "{given}"\n---\n'
            self.assertEqual(config.load(str(make_repo(brief))).data["docs"]["dir"], "docs/baton")

    def test_docs_dir_refused(self):
        for bad in (".", "./", "/abs/docs", "../docs", "docs/../x", ".github/docs", ".baton", "./.claude/x", "hooks/docs"):
            brief = f'---\ndocs:\n  dir: "{bad}"\nprotected_paths: [".github/", ".baton/", ".claude/", "hooks/"]\n---\n'
            with self.assertRaises(config.ConfigError, msg=bad):
                config.load(str(make_repo(brief)))
        ok = '---\ndocs:\n  dir: ".github-docs"\n---\n'
        self.assertEqual(config.load(str(make_repo(ok))).data["docs"]["dir"], ".github-docs")

    def test_load_from_subdirectory(self):
        root = make_repo("x\n")
        (root / "src").mkdir()
        self.assertEqual(config.load(str(root / "src")).root.resolve(), root.resolve())
