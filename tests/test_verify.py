import pathlib
import tempfile
import unittest

from baton import verify

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
