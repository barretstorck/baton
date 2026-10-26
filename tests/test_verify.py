import datetime
import json
import pathlib
import tempfile
import unittest

from baton import verify
from tests.fakes import FakeRunner

SHA = "a" * 40
OTHER = "b" * 40


class VerifyFilesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = self._tmp.name

    def folder(self):
        return verify.logs_dir("o", "r", 7, home=self.home)

    def test_logs_dir_layout(self):
        self.assertEqual(self.folder(), pathlib.Path(self.home) / "o-r" / "logs" / "pr-7")

    def test_write_then_read_back(self):
        path = verify.write_result(self.folder(), SHA, "green", 0, 12, "20261026T100000Z")
        self.assertEqual(path.name, "verify-aaaaaaa-20261026T100000Z.json")
        got = verify.latest_result("o", "r", 7, SHA, home=self.home)
        self.assertEqual(got, {"commit": SHA, "result": "green", "exit": 0, "seconds": 12})

    def test_newest_wins(self):
        verify.write_result(self.folder(), SHA, "red", 1, 5, "20261026T100000Z")
        verify.write_result(self.folder(), SHA, "green", 0, 6, "20261026T110000Z")
        self.assertEqual(verify.latest_result("o", "r", 7, SHA, home=self.home)["result"], "green")

    def test_other_commit_ignored(self):
        verify.write_result(self.folder(), OTHER, "green", 0, 1, "20261026T100000Z")
        self.assertIsNone(verify.latest_result("o", "r", 7, SHA, home=self.home))

    def test_malformed_skipped_and_missing_folder(self):
        self.assertIsNone(verify.latest_result("o", "r", 7, SHA, home=self.home))
        verify.write_result(self.folder(), SHA, "red", 1, 5, "20261026T100000Z")
        (self.folder() / "verify-aaaaaaa-20261026T120000Z.json").write_text("{not json")
        self.assertEqual(verify.latest_result("o", "r", 7, SHA, home=self.home)["result"], "red")

    def test_remove_logs(self):
        self.assertFalse(verify.remove_logs("o", "r", 7, home=self.home))
        verify.write_result(self.folder(), SHA, "green", 0, 1, "20261026T100000Z")
        self.assertTrue(verify.remove_logs("o", "r", 7, home=self.home))
        self.assertFalse(self.folder().exists())

    def test_tail(self):
        short = pathlib.Path(self.home) / "short.log"
        short.write_text("a\nb\n")
        self.assertEqual(verify.tail(short), ["a", "b"])
        long = pathlib.Path(self.home) / "long.log"
        long.write_text("".join(f"{i}\n" for i in range(100)))
        got = verify.tail(long)
        self.assertEqual(len(got), 40)
        self.assertEqual(got[-1], "99")


if __name__ == "__main__":
    unittest.main()


class VerifyRunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = self._tmp.name
        self.cfg = {"verify": {"command": "make check {worktree}", "timeout_minutes": 10}}
        self.now = datetime.datetime(2026, 10, 26, 10, 0, 0, tzinfo=datetime.timezone.utc)
        self.seen = []

    def runner(self, head=SHA, local=SHA, dirty=""):
        return FakeRunner({
            "gh pr view": json.dumps({"headRefOid": head}),
            "git -C /w rev-parse HEAD": local + "\n",
            "git -C /w status": dirty,
        })

    def fake(self, status, text="line1\nline2\n"):
        def execute(command, cwd, log_path, timeout_seconds):
            self.seen.append((command, cwd, timeout_seconds))
            pathlib.Path(log_path).write_text(text)
            return status
        return execute

    def run_verify(self, status=0, runner=None, cfg=None):
        runner = runner or self.runner()
        out = verify.verify(cfg or self.cfg, runner, 7, "/w", "o", "r",
                            execute=self.fake(status), now=self.now, home=self.home)
        return out, runner

    def test_green(self):
        out, _ = self.run_verify(0)
        self.assertEqual((out["result"], out["exit"], out["commit"]), ("green", 0, SHA))
        self.assertEqual(out["tail"], ["line1", "line2"])
        self.assertEqual(self.seen[0], ("make check /w", "/w", 600))

    def test_red(self):
        out, _ = self.run_verify(2)
        self.assertEqual((out["result"], out["exit"]), ("red", 2))

    def test_timeout(self):
        out, _ = self.run_verify(None)
        self.assertEqual((out["result"], out["exit"]), ("timeout", None))

    def test_result_file_written(self):
        out, _ = self.run_verify(0)
        got = verify.latest_result("o", "r", 7, SHA, home=self.home)
        self.assertEqual(got["result"], "green")
        self.assertTrue(pathlib.Path(out["log"]).is_file())

    def test_head_mismatch(self):
        with self.assertRaises(verify.VerifyError):
            self.run_verify(runner=self.runner(local=OTHER))
        self.assertEqual(self.seen, [])

    def test_dirty_worktree(self):
        with self.assertRaises(verify.VerifyError):
            self.run_verify(runner=self.runner(dirty=" M a.py\n"))
        self.assertEqual(self.seen, [])

    def test_empty_command(self):
        with self.assertRaises(verify.VerifyError):
            self.run_verify(cfg={"verify": {"command": "", "timeout_minutes": 10}})

    def test_quotes_worktree(self):
        runner = FakeRunner({"gh pr view": json.dumps({"headRefOid": SHA}),
                             "git -C /my w rev-parse": SHA, "git -C /my w status": ""})
        verify.verify(self.cfg, runner, 7, "/my w", "o", "r", execute=self.fake(0),
                      now=self.now, home=self.home)
        self.assertEqual(self.seen[0][0], "make check '/my w'")

    def test_no_writes_to_pull_request(self):
        _, runner = self.run_verify(0)
        for line in runner.lines():
            self.assertTrue(line.startswith(("gh pr view", "git -C")), line)
            self.assertNotIn("comment", line)


class ExecuteTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log = pathlib.Path(self._tmp.name) / "x.log"

    def test_exit_statuses(self):
        self.assertEqual(verify.execute("true", self._tmp.name, self.log, 5), 0)
        self.assertEqual(verify.execute("false", self._tmp.name, self.log, 5), 1)

    def test_output_logged(self):
        verify.execute("echo out; echo err >&2", self._tmp.name, self.log, 5)
        self.assertEqual(sorted(self.log.read_text().split()), ["err", "out"])

    def test_timeout(self):
        self.assertIsNone(verify.execute("sleep 5", self._tmp.name, self.log, 1))
