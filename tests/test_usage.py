import json, pathlib, tempfile, unittest
from baton import usage


def call(req, ts, read=0, write=0, inp=10, out=100):
    return {"type": "assistant", "requestId": req, "timestamp": ts,
            "message": {"usage": {"input_tokens": inp, "cache_read_input_tokens": read,
                                  "cache_creation_input_tokens": write, "output_tokens": out}}}


def prompt(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def write(path, records, extra_lines=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r) for r in records] + list(extra_lines)
    path.write_text("\n".join(lines) + "\n")


class ClassifyTest(unittest.TestCase):
    def test_prompts(self):
        self.assertEqual(usage.classify("Do task 3 of issue 12."), ("task", "issue 12"))
        self.assertEqual(usage.classify("Do review task 1.2 of pull request 40 (issue 12)."),
                         ("fix", "issue 12"))
        self.assertEqual(usage.classify("Do review task 1.2 of pull request 40."),
                         ("fix", "pr 40"))
        self.assertEqual(usage.classify("Do spike issue 9."), ("spike", "issue 9"))
        self.assertEqual(usage.classify("Fix CI on pull request 40 (issue 12)."), ("fix", "issue 12"))
        self.assertEqual(usage.classify("Update pull request 40 (issue 12)."), ("fix", "issue 12"))
        self.assertEqual(usage.classify("Review pull request 40 (issue 12)."),
                         ("review", "issue 12"))
        self.assertEqual(usage.classify("Refine issues 5, 6, 7."), ("refine", "issues 5, 6, 7"))
        self.assertEqual(usage.classify("hello", "baton:baton-developer"),
                         ("developer", "unknown"))
        self.assertEqual(usage.classify("hello"), ("other", "other"))

    def test_earlier_prompts_and_agent_types(self):
        self.assertEqual(usage.classify("Implement issue #85 in o/r following the develop skill."),
                         ("developer", "issue 85"))
        self.assertEqual(usage.classify("hello", "baton-developer-medium"),
                         ("developer", "unknown"))
        self.assertEqual(usage.classify("hello", "baton:baton-reviewer"), ("review", "unknown"))


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.dir.name)
        session = self.root / "-repo" / "s1.jsonl"
        write(session, [prompt("/baton:sprint"), call("r1", "2026-10-09T10:00:00Z", read=1000)])
        sub = self.root / "-repo" / "s1" / "subagents"
        write(sub / "agent-a.jsonl", [
            prompt("Do task 1 of issue 12."),
            call("r2", "2026-10-09T10:01:00Z", read=300_000, write=1000),
            call("r2", "2026-10-09T10:01:00Z", read=300_000, write=1000),   # same request
            call("r3", "2026-10-09T10:02:00Z", read=1000),
        ], extra_lines=["{not json"])
        write(self.root / "-other" / "s2.jsonl",
              [prompt("hi"), call("r4", "2026-10-09T16:00:00Z", read=10)])

    def tearDown(self):
        self.dir.cleanup()

    def rows(self, **kwargs):
        found = usage.report(self.root, **kwargs)
        return found, {(row["key"], row["role"]): row for row in found["rows"]}

    def test_rows(self):
        found, rows = self.rows()
        task = rows[("issue 12", "task")]
        self.assertEqual((task["agents"], task["calls"]), (1, 2))
        self.assertEqual(task["peak"], 301_010)
        big = 10 + 30_000 + 1250 + 500
        small = 10 + 100 + 500
        self.assertAlmostEqual(task["weighted"], big + small)
        self.assertAlmostEqual(task["large_share"], big / (big + small))
        self.assertIn(("session s1", "orchestrator"), rows)
        self.assertIn(("session s2", "other"), rows)
        self.assertEqual(found["skipped"], 1)

    def test_windows_and_since(self):
        found, _ = self.rows()
        self.assertEqual(len(found["windows"]), 2)
        found, rows = self.rows(since="2026-10-09T12:00:00+00:00")
        self.assertEqual(list(rows), [("session s2", "other")])

    def test_table_mentions_rows(self):
        text = usage.table(usage.report(self.root))
        self.assertIn("issue 12", text)
        self.assertIn("task", text)
