import json
import os
import pathlib
import tempfile
import unittest

from baton import orchestrator


def agent(root, session, agent_id, agent_type, mtime=None, raw=None, transcript=True):
    sub = root / "-repo" / session / "subagents"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / f"agent-{agent_id}.meta.json").write_text(
        raw if raw is not None else json.dumps({"agentType": agent_type}))
    if transcript:
        path = sub / f"agent-{agent_id}.jsonl"
        path.write_text("{}\n")
        if mtime:
            os.utime(path, (mtime, mtime))


class FindAgentTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_newest_orchestrator_wins(self):
        agent(self.root, "s1", "old", orchestrator.AGENT_TYPE, 1000)
        agent(self.root, "s1", "new", orchestrator.AGENT_TYPE, 2000)
        self.assertEqual(orchestrator.find_agent(self.root, "s1"), "new")

    def test_other_agent_types_are_left_out(self):
        agent(self.root, "s1", "dev", "baton:baton-developer")
        self.assertIsNone(orchestrator.find_agent(self.root, "s1"))

    def test_other_session_is_not_found(self):
        agent(self.root, "s2", "x", orchestrator.AGENT_TYPE)
        self.assertIsNone(orchestrator.find_agent(self.root, "s1"))

    def test_empty_session_id(self):
        agent(self.root, "s1", "x", orchestrator.AGENT_TYPE)
        self.assertIsNone(orchestrator.find_agent(self.root, ""))

    def test_invalid_meta_is_skipped(self):
        agent(self.root, "s1", "bad", None, raw="{not json")
        agent(self.root, "s1", "good", orchestrator.AGENT_TYPE, 1000)
        self.assertEqual(orchestrator.find_agent(self.root, "s1"), "good")

    def test_meta_time_without_transcript(self):
        agent(self.root, "s1", "solo", orchestrator.AGENT_TYPE, transcript=False)
        self.assertEqual(orchestrator.find_agent(self.root, "s1"), "solo")


if __name__ == "__main__":
    unittest.main()
