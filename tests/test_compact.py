import contextlib, io, json, pathlib, unittest
from baton import compact

ROOT = pathlib.Path(__file__).resolve().parent.parent


def run(text):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = compact.main(text)
    return rc, buf.getvalue()


class CompactTest(unittest.TestCase):
    def test_compact_prints_reminder(self):
        rc, out = run(json.dumps({"source": "compact", "hook_event_name": "SessionStart"}))
        self.assertEqual(rc, 0)
        self.assertEqual(out, json.dumps({"hookSpecificOutput": {
            "hookEventName": "SessionStart", "additionalContext": compact.REMINDER}}) + "\n")

    def test_other_inputs_print_nothing(self):
        for text in ['{"source": "startup"}', "not json", "[]"]:
            self.assertEqual(run(text), (0, ""))

    def test_reminder_text(self):
        self.assertIn("baton sprint-status", compact.REMINDER)
        self.assertIn("sprint orchestrator", compact.REMINDER)

    def test_hooks_json_has_compact_reminder(self):
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
        entries = hooks["SessionStart"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["matcher"], "compact")
        self.assertTrue(entries[0]["hooks"][0]["command"].endswith('baton" compact-reminder'))
        self.assertNotIn("SubagentStop", hooks)

    def test_reminder_matches_orchestrator_rule(self):
        for name in ("agents/baton-orchestrator.md", "skills/sprint/SKILL.md"):
            text = " ".join((ROOT / name).read_text().split())
            self.assertIn("baton sprint-status", text, name)
            self.assertIn("compaction", text, name)
        self.assertIn("baton sprint-status", compact.REMINDER)
        self.assertIn("compaction", compact.REMINDER)


if __name__ == "__main__":
    unittest.main()
