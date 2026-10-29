import pathlib
import unittest
from baton import config, frontmatter

ROOT = pathlib.Path(__file__).resolve().parent.parent


class TemplateTest(unittest.TestCase):
    def test_template_front_matter_equals_defaults(self):
        meta, body = frontmatter.split((ROOT / "templates" / "brief.md").read_text())
        self.assertEqual(frontmatter.parse(meta), config.DEFAULTS)
        self.assertIn("## Verification", body)
        table = {"triager": "haiku", "refiner": "opus", "researcher": "opus",
                 "developer": "sonnet", "reviewer": "opus"}
        for role, model in table.items():
            self.assertEqual(config.DEFAULTS[role]["model"], model)
            self.assertEqual(config.DEFAULTS[role]["effort"], "medium")

    def test_plan_template_describes_as_built_line(self):
        text = (ROOT / "templates" / "plan.md").read_text()
        self.assertIn("Provides (as built):", text)
        self.assertLess(text.index("  - Provides:"), text.index("  - Provides (as built):"))
        self.assertLess(text.index("  - Provides (as built):"), text.index("  - Check:"))

    def test_issue_template_has_no_plan_line_or_checkbox_criteria(self):
        text = (ROOT / "templates" / "issue.md").read_text()
        self.assertNotIn("Plan:", text)
        self.assertNotIn("[ ]", text)
        self.assertIn("- A result that can be checked objectively", text)

    def test_plan_template_is_developer_plan_section(self):
        text = (ROOT / "templates" / "plan.md").read_text()
        for needle in ("## Developer Plan", "- [ ] Task 1:", "Click to see plan details",
                       "baton plan set", "### Verification", "### Pattern",
                       "### Design notes", "Written against:"):
            self.assertIn(needle, text)
        self.assertNotIn("baton plan commit", text)

    def test_develop_skill_limits_outgrows_decline(self):
        text = " ".join((ROOT / "skills/develop/SKILL.md").read_text().split())
        self.assertIn("cannot be declined as \"outgrows the issue\"", text)
        self.assertIn("name the evidence", text)

    def test_develop_skill_has_scratch_file_rule(self):
        text = (ROOT / "skills/develop/SKILL.md").read_text()
        self.assertIn("\n## Scratch files\n", text)
        self.assertIn("root-owned", text)
        self.assertLess(text.index("## Scratch files"), text.index("\n## Return\n"))

    def test_plugin_files_exist(self):
        for path in (".claude-plugin/plugin.json", "hooks/hooks.json",
                     "skills/init/SKILL.md", "skills/review/SKILL.md",
                     "skills/open-questions/SKILL.md", "agents/baton-developer.md",
                     "agents/baton-reviewer.md",
                     "agents/baton-refiner.md", "agents/baton-researcher.md",
                     "skills/research/SKILL.md", "bin/baton"):
            self.assertTrue((ROOT / path).is_file(), path)

    def test_agents_run_at_medium_effort(self):
        for name in ("baton-developer", "baton-reviewer", "baton-refiner",
                     "baton-researcher"):
            meta, _ = frontmatter.split((ROOT / "agents" / f"{name}.md").read_text())
            self.assertIn("\neffort: medium\n", f"\n{meta}\n", name)

    def test_researcher_agent_and_skill(self):
        meta, _ = frontmatter.split((ROOT / "agents" / "baton-researcher.md").read_text())
        lines = meta.splitlines()
        self.assertIn("isolation: worktree", lines)
        self.assertIn("model: opus", lines)
        self.assertIn("  - baton:research", lines)
        skill = (ROOT / "skills" / "research" / "SKILL.md").read_text()
        self.assertIn("refs/spikes/", skill)
        self.assertIn("Prototype:", skill)

    def test_refine_skill_sets_timebox(self):
        text = " ".join((ROOT / "skills" / "refine" / "SKILL.md").read_text().split())
        for needle in ("Timebox: <tokens> tokens, <minutes> min", "spike.tokens",
                       "spike.minutes"):
            self.assertIn(needle, text)

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
        self.assertIn("baton plan replace", skill)
        self.assertIn("Developer Plan", skill)

    def test_refine_skill_writes_plan_into_body(self):
        skill = (ROOT / "skills" / "refine" / "SKILL.md").read_text()
        self.assertIn("baton plan set", skill)
        self.assertIn("Click to see plan details", skill)
        self.assertIn("baton plan migrate", skill)
        for gone in ("baton plan commit", "baton plan link"):
            self.assertNotIn(gone, skill)
        self.assertEqual(skill.count("refs/plans"), 1)
        agent = (ROOT / "agents" / "baton-refiner.md").read_text()
        self.assertIn("baton plan set", agent)
        self.assertNotIn("baton plan commit", agent)
        self.assertNotIn("refs/plans", agent)

    def test_agents_declare_restricted_tools(self):
        expected = {
            "baton-developer": "tools: Read, Grep, Glob, Bash, Edit, Write",
            "baton-reviewer": "tools: Read, Grep, Glob, Bash",
            "baton-refiner": "tools: Read, Grep, Glob, Bash, Write, WebSearch, WebFetch",
            "baton-researcher": "tools: Read, Grep, Glob, Bash, Edit, Write, WebSearch, WebFetch",
        }
        for name, line in expected.items():
            meta, _ = frontmatter.split((ROOT / "agents" / f"{name}.md").read_text())
            self.assertIn(line, meta.splitlines(), name)
            tools = [t.strip() for t in line.split(":", 1)[1].split(",")]
            for banned in ("Agent", "Skill"):
                self.assertNotIn(banned, tools, name)
            self.assertFalse([t for t in tools if t.startswith("mcp__")], name)
            for web in ("WebSearch", "WebFetch"):
                if name in ("baton-refiner", "baton-researcher"):
                    self.assertIn(web, tools, name)
                else:
                    self.assertNotIn(web, tools, name)

    def test_developer_has_no_spikes(self):
        skill = (ROOT / "skills/develop/SKILL.md").read_text()
        self.assertNotIn("## Spikes", skill)
        agent = (ROOT / "agents/baton-developer.md").read_text()
        self.assertNotIn("spike", agent.lower())

    def test_developer_instructions_forbid_ticking_review_tasks(self):
        for path in ("skills/develop/SKILL.md", "agents/baton-developer.md"):
            text = (ROOT / path).read_text().lower()
            self.assertIn("never run `baton task done pr`", text, path)

    def test_local_brief_template_has_local_data_section(self):
        text = (ROOT / "templates" / "brief.local.md").read_text()
        self.assertIn("## Local data", text)
        self.assertIn("## Credentials", text)

    def test_local_brief_template_has_sandbox_section(self):
        text = (ROOT / "templates" / "brief.local.md").read_text()
        self.assertIn("## Sandbox", text)

    def test_developer_instructions_forbid_copying_secret_files(self):
        for path in ("skills/develop/SKILL.md", "agents/baton-developer.md"):
            text = (ROOT / path).read_text()
            self.assertIn("never copy or link `.env`", text.lower(), path)
            self.assertIn("## Local data", text, path)

    def test_refine_skill_creates_linked_blocking_spikes(self):
        text = " ".join((ROOT / "skills/refine/SKILL.md").read_text().split())
        for needle in ("[SPIKE]", "Spike for: #", "Origin: outside", "Blocked by: #<spike>"):
            self.assertIn(needle, text)
        tmpl = (ROOT / "templates" / "issue.md").read_text()
        self.assertIn("Spike for:", tmpl)
        self.assertIn("Origin: outside", tmpl)

    def test_sprint_skill_names_orchestrator_role_variable(self):
        text = (ROOT / "skills/sprint/SKILL.md").read_text()
        self.assertIn("BATON_ROLE", text)

    def test_sprint_skill_redrafts_on_changes(self):
        # The skill wraps lines, so compare with whitespace collapsed.
        text = " ".join((ROOT / "skills/sprint/SKILL.md").read_text().split())
        self.assertIn("baton pr ready <PR> --undo", text)

    def test_sprint_skill_files_follow_ups_by_severity(self):
        text = " ".join((ROOT / "skills/sprint/SKILL.md").read_text().split())
        self.assertIn("Place each by its severity", text)
        self.assertIn("`follow_ups` from `baton config`", text)
        self.assertIn("`follow_ups.scheduled` in a scheduled run", text)
        for placement in ("deliverable", "stretch", "goal", "backlog", "skip"):
            self.assertIn(f"`{placement}`", text)
        self.assertIn("`needs-maintainer` label", text)

    def test_review_skill_reads_state_from_pr_state(self):
        text = (ROOT / "skills/review/SKILL.md").read_text()
        self.assertIn("baton pr-state", text)

    def test_review_skill_fixes_goal_gaps_here(self):
        text = " ".join((ROOT / "skills/review/SKILL.md").read_text().split())
        self.assertIn("defeats the issue's goal", text)
        self.assertIn("never proposed as a follow-up", text)
        self.assertIn("`critical` security and privacy problems", text)
        self.assertIn("does not test what it claims", text)

    def test_review_skill_sets_aside_minor(self):
        text = " ".join((ROOT / "skills/review/SKILL.md").read_text().split())
        self.assertIn("**Set aside** any other `minor` problem", text)
        self.assertIn("one line under `## Set aside`", text)
        self.assertIn("**[critical|important]** <title>", text)
        self.assertIn("- [<severity>] <title>", text)
        self.assertNotIn("**[minor]**", text)

    def test_retro_counts_follow_ups_by_severity(self):
        retro = (ROOT / "templates/retro.md").read_text()
        self.assertIn("Follow-up issues filed (critical / important)", retro)
        self.assertIn("Problems fixed in review instead of filed", retro)
        text = " ".join((ROOT / "skills/sprint/SKILL.md").read_text().split())
        self.assertIn("Fill the two follow-up rows from the sprint's review comments", text)

    def test_develop_skill_uses_body_plan(self):
        text = (ROOT / "skills/develop/SKILL.md").read_text()
        self.assertIn("## Developer Plan", text)
        self.assertIn("Task <n>:", text)
        self.assertIn("Click to see plan details", text)
        older = text.index("## Older issues")
        self.assertEqual(text[:older].count("refs/plans"), 0)
        self.assertNotIn("refs/plans", (ROOT / "agents/baton-developer.md").read_text())

    def test_review_and_sprint_skills_use_body_plan(self):
        sprint = (ROOT / "skills/sprint/SKILL.md").read_text()
        review = (ROOT / "skills/review/SKILL.md").read_text()
        self.assertIn("baton plan migrate", sprint)
        self.assertIn("## Developer Plan", sprint)
        for text in (sprint, review):
            for gone in ("baton plan commit", "baton plan link", "baton plan pin"):
                self.assertNotIn(gone, text)

    def test_sprint_dispatches_spikes_to_researcher(self):
        sprint = " ".join((ROOT / "skills/sprint/SKILL.md").read_text().split())
        self.assertIn("baton:baton-researcher", sprint)
        self.assertIn("researcher.model", sprint)
        self.assertIn("/baton:research", (ROOT / "README.md").read_text())
