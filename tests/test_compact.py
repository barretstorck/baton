import contextlib, io, json, unittest
from baton import compact


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


if __name__ == "__main__":
    unittest.main()
