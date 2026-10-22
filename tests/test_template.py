import pathlib
import unittest
from baton import config, frontmatter

ROOT = pathlib.Path(__file__).resolve().parent.parent


class TemplateTest(unittest.TestCase):
    def test_template_front_matter_equals_defaults(self):
        meta, body = frontmatter.split((ROOT / "templates" / "brief.md").read_text())
        self.assertEqual(frontmatter.parse(meta), config.DEFAULTS)
        self.assertIn("## Verification", body)

    def test_plan_template_describes_as_built_line(self):
        text = (ROOT / "templates" / "plan.md").read_text()
        self.assertIn("Provides (as built):", text)
        self.assertLess(text.index("  - Provides:"), text.index("  - Provides (as built):"))
        self.assertLess(text.index("  - Provides (as built):"), text.index("  - Check:"))

    def test_plugin_files_exist(self):
        for path in (".claude-plugin/plugin.json", "hooks/hooks.json",
                     "skills/init/SKILL.md", "skills/review/SKILL.md", "agents/baton-developer.md",
                     "agents/baton-reviewer.md",
                     "agents/baton-refiner.md", "bin/baton"):
            self.assertTrue((ROOT / path).is_file(), path)

    def test_agents_run_at_medium_effort(self):
        for name in ("baton-developer", "baton-reviewer", "baton-refiner"):
            meta, _ = frontmatter.split((ROOT / "agents" / f"{name}.md").read_text())
            self.assertIn("\neffort: medium\n", f"\n{meta}\n", name)

    def test_developer_instructions_forbid_ticking_review_tasks(self):
        for path in ("skills/develop/SKILL.md", "agents/baton-developer.md"):
            text = (ROOT / path).read_text().lower()
            self.assertIn("never run `baton task done pr`", text, path)

    def test_local_brief_template_has_local_data_section(self):
        text = (ROOT / "templates" / "brief.local.md").read_text()
        self.assertIn("## Local data", text)
        self.assertIn("## Credentials", text)

    def test_developer_instructions_forbid_copying_secret_files(self):
        for path in ("skills/develop/SKILL.md", "agents/baton-developer.md"):
            text = (ROOT / path).read_text()
            self.assertIn("never copy or link `.env`", text.lower(), path)
            self.assertIn("## Local data", text, path)

    def test_sprint_skill_names_orchestrator_role_variable(self):
        text = (ROOT / "skills/sprint/SKILL.md").read_text()
        self.assertIn("BATON_ROLE", text)

    def test_review_skill_reads_state_from_pr_state(self):
        text = (ROOT / "skills/review/SKILL.md").read_text()
        self.assertIn("baton pr-state", text)
