import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from baton import hook, budget
from tests.test_budget import call, prompt, write
from tests.test_gate import make_repo, IP

ROOT = pathlib.Path(__file__).resolve().parent.parent


class HookTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = pathlib.Path(self.dir.name)
        self.tmp = self.root / "tmp"
        self.tmp.mkdir()
        patcher = mock.patch("tempfile.gettempdir", return_value=str(self.tmp))
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {"CLAUDE_CODE_PLUGIN_CACHE_DIR": tempfile.mkdtemp(dir=self.dir.name)})
        env.start()
        self.addCleanup(env.stop)
        self.repo = make_repo()

    def run_hook(self, tool="Bash", command="echo hi", spike=0):
        payload = {"tool_name": tool, "cwd": self.repo, "tool_input": {"command": command}}
        if spike:
            write(self.root / "s1" / "subagents" / "agent-a1.jsonl",
                  [prompt("Do spike issue 5.\nTimebox: 1000 tokens, 90 min")]
                  + [call(f"r{i}") for i in range(spike)])
            payload.update(agent_id="a1", transcript_path=str(self.root / "s1.jsonl"))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = hook.main(json.dumps(payload))
        return code, out.getvalue(), err.getvalue()

    def test_privacy_problem_blocks_with_gate_message(self):
        code, _, err = self.run_hook(command=f"gh issue comment 5 --body \"{IP}\"")
        self.assertEqual(code, 2)
        self.assertTrue(err)

    def test_merge_hint_still_printed(self):
        payload = {"tool_name": "Bash", "cwd": self.repo, "session_id": "hook-test-s1",
                   "tool_input": {"command": "gh pr merge 5 --merge"}}
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = hook.main(json.dumps(payload))
        self.assertEqual(code, 0)
        json.loads(out.getvalue())

    def test_over_budget_blocks_other_tool(self):
        code, _, err = self.run_hook(tool="Read", spike=2)
        self.assertEqual(code, 2)
        self.assertIn("Token budget reached", err)

    def test_blocked_by_both_prints_both(self):
        code, _, err = self.run_hook(command=f"gh issue comment 5 --body \"{IP}\"", spike=2)
        self.assertEqual(code, 2)
        self.assertIn("Token budget reached", err)
        self.assertGreater(len(err.replace("Token budget reached", "")), 60)

    def test_not_json_allowed(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(hook.main("not json"), 0)

    def test_budget_crash_keeps_gate_result(self):
        with mock.patch.object(budget, "main", side_effect=RuntimeError("boom")):
            self.assertEqual(self.run_hook()[0], 0)
            self.assertEqual(self.run_hook(command=f"gh issue comment 5 --body \"{IP}\"")[0], 2)

    def test_module_entry_point(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "lib"), TMPDIR=str(self.tmp))
        payload = json.dumps({"tool_name": "Read", "tool_input": {}})
        done = subprocess.run([sys.executable, "-m", "baton", "hook"], input=payload,
                              text=True, capture_output=True, env=env)
        self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == "__main__":
    unittest.main()
