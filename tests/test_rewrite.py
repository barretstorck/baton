"""Spike #222: the gate completes `gh pr create` from a detached HEAD instead of refusing it."""
import contextlib
import io
import json
import os
import subprocess
import unittest
from unittest import mock

from baton import gate, rewrite
from baton.run import RunError
from tests.fakes import FakeRunner
from tests.test_gate import MAIL, TERMS, make_repo

NEW = "claude-code_2-1-296_agent"
OLD = "claude-code_2-1-200_agent"


class Detached(FakeRunner):
    """`git symbolic-ref` fails (detached HEAD); `for-each-ref` lists `refs`."""

    def __init__(self, refs):
        super().__init__({"git for-each-ref": "\n".join(refs) + "\n"})

    def __call__(self, argv, cwd=None, input=None):
        if argv[:2] == ["git", "symbolic-ref"]:
            self.calls.append(argv)
            raise RunError("not a symbolic ref")
        return super().__call__(argv, cwd, input)


class PlanTest(unittest.TestCase):
    def test_attached_head_is_left_alone(self):
        self.assertIsNone(rewrite.plan('gh pr create --title t --body "b"', ".", FakeRunner(), NEW))

    def test_other_commands_and_explicit_head_are_left_alone(self):
        runner = Detached(["refs/heads/issue-1-x"])
        self.assertIsNone(rewrite.plan("gh pr edit 5 --title t", ".", runner, NEW))
        self.assertIsNone(rewrite.plan("gh pr create --head issue-1-x --title t", ".", runner, NEW))
        self.assertIsNone(rewrite.plan("gh pr create -H issue-1-x --title t", ".", runner, NEW))
        self.assertEqual(runner.calls, [])

    def test_detached_head_gets_the_branch(self):
        runner = Detached(["refs/heads/issue-1-x", "refs/remotes/origin/issue-1-x"])
        step = rewrite.plan('gh pr create --draft --title "t" --body "b"', ".", runner, NEW)
        self.assertEqual(step[0], "rewrite")
        self.assertEqual(step[1], 'gh pr create --head issue-1-x --draft --title "t" --body "b"')
        self.assertIn("--head issue-1-x", step[2])

    def test_head_flag_inside_a_quoted_body_does_not_count(self):
        step = rewrite.plan('gh pr create --title t --body "use --head x"', ".",
                            Detached(["refs/heads/b"]), NEW)
        self.assertEqual(step[1], 'gh pr create --head b --title t --body "use --head x"')

    def test_no_single_branch_is_refused_with_the_fix(self):
        for refs in ([], ["refs/heads/a", "refs/heads/b"]):
            step = rewrite.plan("gh pr create --title t --body b", ".", Detached(refs), NEW)
            self.assertEqual(step[0], "refuse")
            self.assertIn("--head <branch>", step[1])

    def test_compound_command_or_old_claude_is_refused_with_the_exact_flag(self):
        runner = Detached(["refs/heads/issue-1-x"])
        for command, agent in (("cd x && gh pr create --title t --body b", NEW),
                               ("gh pr create --title t --body b", OLD),
                               ("gh pr create --title t --body b", None)):
            step = rewrite.plan(command, ".", runner, agent)
            self.assertEqual(step[0], "refuse", command)
            self.assertIn("--head issue-1-x", step[1])

    def test_version(self):
        self.assertEqual(rewrite.claude_version(NEW), (2, 1, 296))
        self.assertIsNone(rewrite.claude_version("cursor"))


def detached_repo():
    root = make_repo()
    git = ["git", "-C", root, "-c", "user.name=t", "-c", "user.email=" + MAIL]
    subprocess.run(git + ["commit", "-q", "--allow-empty", "--no-verify", "-m", "x"], check=True)
    subprocess.run(git + ["switch", "-q", "-c", "issue-216-detached"], check=True)
    subprocess.run(git + ["commit", "-q", "--allow-empty", "--no-verify", "-m", "y"], check=True)
    subprocess.run(git + ["switch", "-q", "--detach"], check=True)
    return root


def run_gate(command, cwd, agent=NEW):
    payload = {"tool_name": "Bash", "cwd": cwd,
               "tool_input": {"command": command, "description": "Open the pull request", "timeout": 60000}}
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, {"AI_AGENT": agent}), \
            mock.patch.object(gate.scan, "load_terms", return_value=TERMS), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = gate.main(json.dumps(payload))
    return code, out.getvalue(), err.getvalue()


class GateTest(unittest.TestCase):
    def test_clean_pr_create_from_detached_head_runs_with_head_added(self):
        code, out, err = run_gate('gh pr create --draft --title "Fix" --body "Closes #216"', detached_repo())
        self.assertEqual(code, 0, err)
        hook = json.loads(out)["hookSpecificOutput"]
        self.assertNotIn("permissionDecision", hook)
        self.assertEqual(hook["updatedInput"], {
            "command": 'gh pr create --head issue-216-detached --draft --title "Fix" --body "Closes #216"',
            "description": "Open the pull request", "timeout": 60000})
        self.assertIn("issue-216-detached", hook["additionalContext"])

    def test_scanned_term_is_refused_before_any_rewrite(self):
        code, out, err = run_gate('gh pr create --title "Fix" --body "ask Zebrowski"', detached_repo())
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("privacy: private-term", err)

    def test_old_claude_code_is_told_the_exact_flag(self):
        code, out, err = run_gate('gh pr create --title "Fix" --body "b"', detached_repo(), OLD)
        self.assertEqual(code, 2)
        self.assertIn("--head issue-216-detached", err)


if __name__ == "__main__":
    unittest.main()
