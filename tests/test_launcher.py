"""bin/baton picks a Python new enough to run Baton, whatever order PATH lists them in."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "bin" / "baton"


class LauncherTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.old = Path(tmp.name) / "old"
        self.new = Path(tmp.name) / "new"
        self.old.mkdir()
        self.new.mkdir()
        fake = self.old / "python3"  # fails the version check, as Python 3.9 would
        fake.write_text("#!/bin/sh\nexit 1\n")
        fake.chmod(0o755)
        (self.new / "python3").symlink_to(sys.executable)

    def run_baton(self, *dirs, env=None):
        environ = {"PATH": os.pathsep.join(str(d) for d in dirs), "HOME": os.environ.get("HOME", "/")}
        environ.update(env or {})
        return subprocess.run([str(BIN)], env=environ, capture_output=True, text=True)

    def test_skips_an_old_python3_ahead_of_a_new_one(self):
        result = self.run_baton(self.old, self.new)
        self.assertIn("usage: baton", result.stderr + result.stdout)
        self.assertNotIn("tomllib", result.stderr)

    def test_no_new_enough_python_is_a_clear_error(self):
        result = self.run_baton(self.old)
        self.assertEqual(result.returncode, 1)
        self.assertIn("needs Python 3.11 or newer", result.stderr)
        self.assertIn("BATON_PYTHON", result.stderr)

    def test_baton_python_wins(self):
        result = self.run_baton(self.old, env={"BATON_PYTHON": sys.executable})
        self.assertIn("usage: baton", result.stderr + result.stdout)

    def test_old_baton_python_falls_back_to_path(self):
        result = self.run_baton(self.new, env={"BATON_PYTHON": str(self.old / "python3")})
        self.assertIn("usage: baton", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
