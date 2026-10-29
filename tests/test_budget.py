import contextlib, io, json, pathlib, tempfile, unittest
from baton import budget

ROOT = pathlib.Path(__file__).resolve().parent.parent


def call(req, ts="2026-10-09T10:00:00Z", read=0, write=0, inp=10, out=100):
    return {"type": "assistant", "requestId": req, "timestamp": ts,
            "message": {"usage": {"input_tokens": inp, "cache_read_input_tokens": read,
                                  "cache_creation_input_tokens": write, "output_tokens": out}}}


def prompt(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def write(path, records, extra_lines=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r) for r in records] + list(extra_lines)
    path.write_text("\n".join(lines) + "\n")


class BudgetTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = pathlib.Path(self.dir.name)

    def run_hook(self, records, tool="Read", command=None, agent="a1"):
        write(self.root / "s1" / "subagents" / "agent-a1.jsonl", records)
        payload = {"agent_id": agent, "transcript_path": str(self.root / "s1.jsonl"),
                   "tool_name": tool, "tool_input": {"command": command} if command else {}}
        with contextlib.redirect_stderr(io.StringIO()):
            return budget.main(json.dumps(payload))

    def spike(self, line="Timebox: 1000 tokens, 90 min", n=1):
        # each call weighs 10 + 100 * 5 = 510
        return [prompt("Do spike issue 5.\n" + line)] + [call(f"r{i}") for i in range(n)]

    def test_under_limit(self):
        self.assertEqual(self.run_hook(self.spike(n=1)), 0)

    def test_over_limit_blocks_with_message(self):
        write(self.root / "s1" / "subagents" / "agent-a1.jsonl", self.spike(n=2))
        payload = {"agent_id": "a1", "transcript_path": str(self.root / "s1.jsonl"), "tool_name": "Read"}
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(budget.main(json.dumps(payload)), 2)
        self.assertIn("Token budget reached (1020 of 1000 weighted tokens)", err.getvalue())

    def test_comment_allowed_others_blocked(self):
        records = self.spike(n=2)
        self.assertEqual(self.run_hook(records, "Bash", "baton comment issue 5 researcher findings -"), 0)
        self.assertEqual(self.run_hook(records, "Bash", "  baton comment issue 5 researcher findings -"), 0)
        self.assertEqual(self.run_hook(records, "Bash", "git status"), 2)
        self.assertEqual(self.run_hook(records, "Read"), 2)

    def test_fail_open(self):
        self.assertEqual(self.run_hook(self.spike(n=2), agent=""), 0)
        self.assertEqual(self.run_hook(self.spike(line="", n=2)), 0)
        self.assertEqual(self.run_hook(self.spike(line="Timebox: 90 min", n=2)), 0)
        self.assertEqual(self.run_hook(self.spike(line="Timebox: lots", n=2)), 0)
        self.assertEqual(self.run_hook(self.spike(n=2), agent="missing"), 0)
        self.assertEqual(budget.main(json.dumps({"transcript_path": str(self.root / "s1.jsonl")})), 0)
        self.assertEqual(budget.main("not json"), 0)
        self.assertEqual(budget.main("[]"), 0)

    def test_repeated_request_counts_once(self):
        records = [prompt("Do spike issue 5.\nTimebox: 1000 tokens")] + [call("r0")] * 3
        self.assertEqual(self.run_hook(records), 0)

    def test_hooks_json_has_budget_entry(self):
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"]
        entries = [h for h in hooks if "matcher" not in h]
        self.assertTrue(any(c["command"].endswith('baton" budget') for e in entries for c in e["hooks"]))
        self.assertEqual(sum("matcher" in h for h in hooks), 2)


if __name__ == "__main__":
    unittest.main()
