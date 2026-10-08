import contextlib
import io
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from baton import gate

TERMS = ["Zebrowski"]


def make_repo(brief="---\n---\n", hooks=True):
    root = pathlib.Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    if brief is not None:
        (root / ".baton").mkdir()
        (root / ".baton" / "brief.md").write_text(brief)
    if hooks:
        for name in ("pre-commit", "pre-push"):
            (root / ".git" / "hooks" / name).write_text(f"#!/bin/sh\n# {gate.HOOK_TAG}\n")
    return str(root)


class GateTest(unittest.TestCase):
    def test_non_publishing_command_is_ignored(self):
        self.assertEqual(gate.decide("ls 192.168.7.7", make_repo(), TERMS), [])

    def test_non_baton_repo_is_ignored(self):
        repo = make_repo(brief=None)
        self.assertEqual(gate.decide('gh issue create --body "192.168.7.7"', repo, TERMS), [])

    def test_inline_body_is_scanned(self):
        problems = gate.decide('gh pr create --title x --body "see 192.168.7.7"', make_repo(), TERMS)
        self.assertTrue(any("private-ip" in p for p in problems))

    def test_body_file_is_scanned(self):
        repo = make_repo()
        pathlib.Path(repo, "body.md").write_text("<!-- baton:developer:status -->\nthe Zebrowski set\n")
        problems = gate.decide("gh issue comment 5 --body-file body.md", repo, TERMS)
        self.assertTrue(any("private-term" in p for p in problems))

    def test_heredoc_body_is_scanned(self):
        cmd = "gh issue create --title t --body \"$(cat <<'EOF'\nmail alice@corp-mail.test\nEOF\n)\""
        self.assertTrue(any("email" in p for p in gate.decide(cmd, make_repo(), TERMS)))

    def test_clean_issue_passes(self):
        self.assertEqual(gate.decide('gh issue create --title t --body "Add a flag"', make_repo(), TERMS), [])

    def test_comment_without_marker_is_blocked(self):
        problems = gate.decide('gh issue comment 5 --body "done"', make_repo(), TERMS)
        self.assertTrue(any("marker" in p for p in problems))

    def test_comment_with_marker_passes(self):
        cmd = 'gh pr comment 5 --body "<!-- baton:developer:status -->\nBaton developer: done"'
        self.assertEqual(gate.decide(cmd, make_repo(), TERMS), [])

    def test_no_verify_is_blocked(self):
        for cmd in ("git commit --no-verify -m x", "git push --no-verify",
                    "git commit -nm x", "git -c core.hooksPath=/dev/null commit -m x",
                    "git -c core.hookspath=/dev/null commit -m x",
                    "git commit --no-veri -m x", "git push --no-veri",
                    "git \\\n -c core.hooksPath=/dev/null commit -m x",
                    "git config core.hooksPath /tmp/none",
                    "BATON_PRIVATE_TERMS=/dev/null git commit -m x",
                    "HOME=/tmp git push",
                    "export GIT_CONFIG_GLOBAL=/dev/null && git commit -m x"):
            with self.subTest(cmd=cmd):
                self.assertTrue(any("bypass" in p for p in gate.decide(cmd, make_repo(), TERMS)))

    def test_push_dry_run_flag_is_not_a_bypass(self):
        self.assertEqual(gate.decide("git push -n origin main", make_repo(), TERMS), [])

    def test_git_publish_requires_installed_hooks(self):
        problems = gate.decide("git commit -m x", make_repo(hooks=False), TERMS)
        self.assertTrue(any("install-hooks" in p for p in problems))

    def test_commit_message_is_scanned(self):
        problems = gate.decide('git commit -m "fix Zebrowski import"', make_repo(), TERMS)
        self.assertTrue(any("private-term" in p for p in problems))

    def test_cd_into_baton_repo_from_elsewhere(self):
        repo = make_repo()
        cmd = f'cd {repo} && gh issue create --title t --body "192.168.7.7"'
        self.assertTrue(gate.decide(cmd, tempfile.mkdtemp(), TERMS))

    def test_unparsable_brief_blocks(self):
        repo = make_repo(brief="---\nnot a mapping\n---\n")
        problems = gate.decide('gh issue create --title t --body ok', repo, TERMS)
        self.assertTrue(any("brief.md" in p for p in problems))

    def test_allow_list_from_brief(self):
        repo = make_repo(brief='---\nprivacy_allow: ["/home/app"]\n---\n')
        self.assertEqual(gate.decide('gh issue create --title t --body "WORKDIR /home/app"', repo, TERMS), [])

    def test_ordinary_commands_are_not_bypasses(self):
        for cmd in ('git commit -m "handle the -n flag"', "git log --grep=commit -n 5",
                    "docker run -e HOME=/tmp img && git push",
                    'cd /home/dev/proj && git commit -m "fix typo"',
                    "git -C /home/dev/proj push",
                    "git push git@github.com:owner/repo.git HEAD",
                    'git commit -m "bump actions/checkout@v4.1.0"',
                    'echo a#b; git commit -m "Closes #4"'):
            with self.subTest(cmd=cmd):
                self.assertEqual(gate.decide(cmd, make_repo(), TERMS), [])

    def test_text_after_a_hash_is_still_gated(self):
        cmd = 'echo a#b; gh issue create --title t --body "192.168.7.7"'
        self.assertTrue(any("private-ip" in p for p in gate.decide(cmd, make_repo(), TERMS)))

    def test_quoted_program_name_is_gated(self):
        cmd = '"gh" issue create --title t --body "192.168.7.7"'
        self.assertTrue(any("private-ip" in p for p in gate.decide(cmd, make_repo(), TERMS)))

    def test_body_from_stdin_is_refused(self):
        for cmd in ("gh issue create --title t --body-file - < body.md",
                    "cat body.md | gh issue create --title t -F -",
                    "git commit -F -"):
            with self.subTest(cmd=cmd):
                self.assertTrue(any("stdin" in p for p in gate.decide(cmd, make_repo(), TERMS)))

    def test_body_from_substitution_or_variable_is_refused(self):
        for cmd in ('gh issue create --title t --body "$(cat body.md)"',
                    "gh issue create --title t --body $(cat body.md)",
                    'gh issue create --title t --body "$BODY"'):
            with self.subTest(cmd=cmd):
                self.assertTrue(any("cannot be scanned" in p for p in gate.decide(cmd, make_repo(), TERMS)))

    def test_missing_body_file_is_refused(self):
        problems = gate.decide("gh issue create --title t --body-file nope.md", make_repo(), TERMS)
        self.assertTrue(any("not found" in p for p in problems))

    def test_body_file_in_cd_target_and_attached_form_are_scanned(self):
        repo = make_repo()
        pathlib.Path(repo, "sub").mkdir()
        pathlib.Path(repo, "sub", "body.md").write_text("host 192.168.7.7\n")
        for cmd in ("cd sub && gh issue create --title t -F body.md",
                    "gh issue create --title t -Fsub/body.md",
                    "gh issue create --title t --body-file=sub/body.md",
                    "gh api repos/o/r/issues -f title=t -F body=@sub/body.md"):
            with self.subTest(cmd=cmd):
                self.assertTrue(any("private-ip" in p for p in gate.decide(cmd, repo, TERMS)))

    def test_marker_must_start_the_body(self):
        for cmd in ('gh pr comment 5 --body "/baton approve" # <!-- baton:',
                    "gh api repos/o/r/issues/5/comments -f body='/baton approve'",
                    "gh pr review 5 --approve",
                    'gh issue close 5 --comment "done"'):
            with self.subTest(cmd=cmd):
                self.assertTrue(any("marker" in p for p in gate.decide(cmd, make_repo(), TERMS)))

    def test_inline_heredoc_comment_with_marker_passes(self):
        cmd = ("gh issue comment 3 --body \"$(cat <<'EOF'\n<!-- baton:developer:question -->\n"
               "**Baton developer: question**\nWhich `mode`?\nEOF\n)\"")
        self.assertEqual(gate.decide(cmd, make_repo(), TERMS), [])

    def test_inline_heredoc_comment_without_marker_is_blocked(self):
        cmd = "gh issue comment 3 --body \"$(cat <<'EOF'\n/baton approve abc1234\nEOF\n)\""
        self.assertTrue(any("marker" in p for p in gate.decide(cmd, make_repo(), TERMS)))

    def test_api_comment_with_marker_passes(self):
        cmd = "gh api repos/o/r/issues/5/comments -f body='<!-- baton:developer:status -->\nok'"
        self.assertEqual(gate.decide(cmd, make_repo(), TERMS), [])

    def test_empty_body_is_refused(self):
        repo = make_repo()
        empty = pathlib.Path(repo) / "empty.md"
        empty.write_text("  \n")
        problems = gate.decide(f"gh issue edit 5 --body-file {empty}", repo, TERMS)
        self.assertTrue(any("empty" in p for p in problems))
        self.assertTrue(any("empty" in p for p in gate.decide('gh pr edit 5 --body ""', repo, TERMS)))
        self.assertEqual(gate.decide("gh issue edit 5 --add-label status:ready", repo, TERMS), [])

    def test_direct_merge_is_refused(self):
        for cmd in ("gh pr merge 5 --merge --admin", "gh api -X PUT repos/o/r/pulls/5/merge"):
            with self.subTest(cmd=cmd):
                self.assertTrue(any("baton merge-pr" in p for p in gate.decide(cmd, make_repo(), TERMS)))

    def test_hooks_must_be_in_the_effective_hooks_path(self):
        repo = make_repo()
        subprocess.run(["git", "-C", repo, "config", "core.hooksPath", ".husky"], check=True)
        problems = gate.decide("git commit -m x", repo, TERMS)
        self.assertTrue(any("install-hooks" in p for p in problems))

    def test_main_fails_closed_on_unexpected_errors(self):
        original = gate.decide
        gate.decide = lambda *args: 1 / 0
        try:
            payload = {"tool_name": "Bash", "cwd": "/", "tool_input": {"command": "ls"}}
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(gate.main(json.dumps(payload)), 2)
        finally:
            gate.decide = original

    def test_main_blocks_with_exit_2_and_ignores_other_tools(self):
        repo = make_repo()
        payload = {"tool_name": "Bash", "cwd": repo,
                   "tool_input": {"command": 'gh issue create --title t --body "192.168.7.7"'}}
        os.environ["BATON_PRIVATE_TERMS"] = "/nonexistent"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(gate.main(json.dumps(payload)), 2)
        self.assertIn("private-ip", stderr.getvalue())
        self.assertNotIn("192.168.7.7", stderr.getvalue())
        payload["tool_name"] = "Read"
        self.assertEqual(gate.main(json.dumps(payload)), 0)
        self.assertEqual(gate.main("not json"), 0)
