import os
import pathlib
import tempfile
import unittest

from baton import sandbox
from tests.fakes import FakeRunner

TPL = "docker run --rm --network none -v {checkout}:/work sh -c {command}"


class SandboxTemplateTest(unittest.TestCase):
    def test_parse(self):
        text = f"# x\n## Sandbox\n<!-- Command: no -->\nCommand: {TPL}\n## Other\n"
        self.assertEqual(sandbox.parse_template(text), TPL)

    def test_parse_missing(self):
        self.assertIsNone(sandbox.parse_template("## Hosts\nCommand: x\n"))
        self.assertIsNone(sandbox.parse_template("## Sandbox\n<!-- Command: x -->\nCommand:\n"))

    def test_render_quotes(self):
        out = sandbox.render(TPL, "/a b", "echo 'hi'; ls")
        self.assertIn("-v '/a b':/work", out)
        self.assertIn("sh -c 'echo '\"'\"'hi'\"'\"'; ls'", out)

    def test_render_missing_placeholder(self):
        with self.assertRaises(sandbox.SandboxError):
            sandbox.render("docker run {checkout}", "/a", "x")
        with self.assertRaises(sandbox.SandboxError):
            sandbox.render("docker run {command}", "/a", "x")

    def test_load_template(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertIsNone(sandbox.load_template("o", "r", home=home))
            p = sandbox.brief_path("o", "r", home=home)
            p.parent.mkdir(parents=True)
            p.write_text(f"## Sandbox\nCommand: {TPL}\n")
            self.assertEqual(sandbox.load_template("o", "r", home=home), TPL)


class SandboxRunTest(unittest.TestCase):
    def setUp(self):
        self._h = tempfile.TemporaryDirectory()
        self.home = self._h.name
        p = sandbox.brief_path("o", "r", home=self.home)
        p.parent.mkdir(parents=True)
        p.write_text(f"## Sandbox\nCommand: {TPL}\n")
        self.runner = FakeRunner({"git remote get-url origin": "https://example.invalid/o/r.git\n"})
        self.seen = {}

    def tearDown(self):
        self._h.cleanup()

    def fake(self, text, status=0, raises=False):
        def execute(command, cwd, log_path, timeout):
            self.seen.update(command=command, cwd=cwd, log=log_path, timeout=timeout,
                             existed=os.path.isdir(cwd))
            pathlib.Path(log_path).write_text(text)
            if raises:
                raise RuntimeError("boom")
            return status
        return execute

    def test_run(self):
        out = sandbox.run_in_sandbox(self.runner, "o", "r", "main", "echo hi", 60,
                                     execute=self.fake("hi\n", 5), home=self.home)
        self.assertEqual(out["exit"], 5)
        self.assertEqual(out["tail"], ["hi"])
        self.assertIn("logs/sandbox/sandbox-", out["log"])
        self.assertTrue(self.seen["existed"])
        self.assertFalse(os.path.exists(self.seen["cwd"]))
        self.assertIn("sh -c 'echo hi'", self.seen["command"])
        clone = [c for c in self.runner.lines() if c.startswith("git clone")][0]
        self.assertIn("--branch main https://example.invalid/o/r.git", clone)
        self.assertIn("/o-r/sandbox/", clone)

    def test_timeout_is_3(self):
        def timed_out(command, cwd, log_path, timeout):
            pathlib.Path(log_path).write_text("")
            return None
        out = sandbox.run_in_sandbox(self.runner, "o", "r", "main", "x", 1,
                                     execute=timed_out, home=self.home)
        self.assertEqual(out["exit"], 3)

    def test_checkout_removed_on_error(self):
        with self.assertRaises(RuntimeError):
            sandbox.run_in_sandbox(self.runner, "o", "r", "main", "x", 1,
                                   execute=self.fake("", raises=True), home=self.home)
        self.assertFalse(os.path.exists(self.seen["cwd"]))

    def test_no_template(self):
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaises(sandbox.SandboxError):
                sandbox.run_in_sandbox(self.runner, "o", "r", "main", "x", 1, home=empty)
            self.assertEqual(sandbox.check(self.runner, "o", "r", "main", 1, home=empty),
                             ("none", None))

    def test_check_states(self):
        for text, want in (("UNREACHABLE\n", "isolated"), ("REACHABLE\n", "not-isolated"),
                           ("Traceback\n", "probe-failed")):
            state, log = sandbox.check(self.runner, "o", "r", "main", 5,
                                       execute=self.fake(text), home=self.home)
            self.assertEqual(state, want)
            self.assertIn("socket", self.seen["command"])


if __name__ == "__main__":
    unittest.main()
