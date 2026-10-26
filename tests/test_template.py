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

    def test_develop_skill_has_scratch_file_rule(self):
        text = (ROOT / "skills/develop/SKILL.md").read_text()
        self.assertIn("\n## Scratch files\n", text)
        self.assertIn("root-owned", text)
        self.assertLess(text.index("## Scratch files"), text.index("\n## Return\n"))

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

    def test_refiner_runs_in_own_worktree(self):
        meta, _ = frontmatter.split((ROOT / "agents" / "baton-refiner.md").read_text())
        self.assertIn("isolation: worktree", meta.splitlines())

    def test_refine_skill_allows_running_code(self):
        text = (ROOT / "skills" / "refine" / "SKILL.md").read_text()
        self.assertIn("throwaway", text)
        self.assertIn("log file", text)

    def test_refine_skill_and_template_cover_bug_diagnosis(self):
        skill = (ROOT / "skills" / "refine" / "SKILL.md").read_text()
        self.assertIn("## Diagnosing a bug", skill)
        issue = (ROOT / "templates" / "issue.md").read_text()
        self.assertIn("## Diagnosis", issue)

    def test_refine_skill_and_agent_cover_re_refine(self):
        skill = (ROOT / "skills" / "refine" / "SKILL.md").read_text()
        self.assertIn("## Re-refining after a review", skill)
        self.assertIn("Re-refine issue", skill)
        self.assertIn("baton plan against", skill)
        agent = (ROOT / "agents" / "baton-refiner.md").read_text()
        self.assertIn("Re-refine issue", agent)

    def test_agents_declare_restricted_tools(self):
        expected = {
            "baton-developer": "tools: Read, Grep, Glob, Bash, Edit, Write, WebSearch, WebFetch",
            "baton-reviewer": "tools: Read, Grep, Glob, Bash",
            "baton-refiner": "tools: Read, Grep, Glob, Bash, Write, WebSearch, WebFetch",
        }
        for name, line in expected.items():
            meta, _ = frontmatter.split((ROOT / "agents" / f"{name}.md").read_text())
            self.assertIn(line, meta.splitlines(), name)
            tools = [t.strip() for t in line.split(":", 1)[1].split(",")]
            for banned in ("Agent", "Skill"):
                self.assertNotIn(banned, tools, name)
            self.assertFalse([t for t in tools if t.startswith("mcp__")], name)
            for web in ("WebSearch", "WebFetch"):
                if name == "baton-reviewer":
                    self.assertNotIn(web, tools, name)
                else:
                    self.assertIn(web, tools, name)

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

    def test_sprint_skill_redrafts_on_changes(self):
        # The skill wraps lines, so compare with whitespace collapsed.
        text = " ".join((ROOT / "skills/sprint/SKILL.md").read_text().split())
        self.assertIn("gh pr ready <PR> --undo", text)

    def test_review_skill_reads_state_from_pr_state(self):
        text = (ROOT / "skills/review/SKILL.md").read_text()
        self.assertIn("baton pr-state", text)
