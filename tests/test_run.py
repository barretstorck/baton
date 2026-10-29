import os
import unittest

from baton import run


class RunEnvTest(unittest.TestCase):
    def test_child_sees_extra_variable(self):
        out = run.run(["sh", "-c", "printf %s \"$BATON_TEST_VAR\""],
                      env={"BATON_TEST_VAR": "hello"})
        self.assertEqual(out, "hello")
        self.assertNotIn("BATON_TEST_VAR", os.environ)

    def test_env_is_merged_over_environ(self):
        out = run.run(["sh", "-c", "printf %s \"$PATH\""], env={"X": "1"})
        self.assertEqual(out, os.environ["PATH"])

    def test_no_env_changes_nothing(self):
        out = run.run(["sh", "-c", "printf %s \"${BATON_TEST_VAR-unset}\""])
        self.assertEqual(out, "unset")


if __name__ == "__main__":
    unittest.main()
