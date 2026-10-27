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
IP = ".".join(["192", "168", "7", "7"])  # assembled so the commit scan sees no literal
HOME = "/" + "home/app"
MAIL = "alice" + "@" + "corp-mail.test"
SSH_URL = "git" + "@" + "github.com:owner/repo.git"


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


RAW_WRITES = (
    ("gh issue create --title t --body x", "baton issue create"),
    ("gh issue edit 5 --add-label bug", "baton issue edit"),
    ("gh issue comment 5 --body x", "baton comment"),
    ("gh pr create --title t --body x", "baton pr create"),
    ("gh pr edit 5 --title t", "baton pr edit"),
    ("gh pr comment 5 --body x", "baton comment"),
    ("gh pr review 5 --approve", "baton review post"),
    ("gh pr ready 5", "baton pr ready"),
    ("gh pr ready 5 --undo", "baton pr ready"),
    ("gh pr merge 5 --merge --admin", "baton merge-pr"),
    ("gh issue close 5", "baton close|reopen"),
    ("gh issue reopen 5", "baton close|reopen"),
    ("gh pr close 5 --comment done", "baton close|reopen"),
    ("gh pr reopen 5", "baton close|reopen"),
    ("gh label create bug", "baton labels"),
    ("gh release create v1", "no baton command"),
    ("gh repo edit --description x", "no baton command"),
    ("gh gist create f.txt", "no baton command"),
    ("gh -R o/r issue edit 5 --title t", "baton issue edit"),
    ("gh --repo o/r pr ready 5", "baton pr ready"),
    ("gh api -X PATCH repos/o/r/issues/5", "baton"),
    ("gh api --method PATCH repos/o/r/issues/5", "baton"),
    ("gh api --method=PATCH repos/o/r/issues/5", "baton"),
    ("gh api -XPOST repos/o/r/issues/5/comments", "baton"),
    ("gh api repos/o/r/issues/5/comments -f body=x", "baton"),
    ("gh api repos/o/r/issues/5/comments -fbody=x", "baton"),
    ("gh api repos/o/r/issues -F title=t", "baton"),
    ("gh api repos/o/r/issues --raw-field title=t", "baton"),
    ("gh api repos/o/r/issues --field=title=t", "baton"),
    ("gh api repos/o/r/issues/5 --input p.json", "baton"),
    ("gh api graphql -f query='mutation { x }'", "baton"),
    ("gh api graphql --jq .data -f query='Mutation { x }'", "baton"),
)

READS = (
    "gh issue view 5", "gh pr list", "gh pr checks 5", "gh repo view", "gh api repos/o/r/pulls",
    "gh api -X GET repos/o/r/pulls", "gh api repos/o/r/pulls --paginate", "gh pr diff 5",
    "gh pr view 5 --json state", "gh -R o/r issue list", "gh run watch 5", "gh pr checkout 5",
    "gh status", "gh search issues foo", "gh auth status", "gh --version", "gh help pr",
    "git log", "git status", "git log --oneline origin/main..HEAD", "git diff --stat",
    "ls", "echo gh issue edit 1",
)


class GateTest(unittest.TestCase):
    def test_non_publishing_command_is_ignored(self):
        self.assertEqual(gate.decide(f"ls {IP}", make_repo(), TERMS), [])

    def test_raw_writes_are_refused_in_a_baton_directory_and_elsewhere(self):
        for directory in (make_repo(), make_repo(brief=None), tempfile.mkdtemp()):
            for cmd, use in RAW_WRITES:
                with self.subTest(cmd=cmd, directory=bool(directory)):
                    problems = gate.decide(cmd, directory, TERMS)
                    self.assertTrue(problems)
                    self.assertTrue(any(use in p for p in problems), problems)

    def test_refusal_does_not_depend_on_a_clean_body(self):
        problems = gate.decide('gh issue create --title t --body "Add a flag"', make_repo(), TERMS)
        self.assertTrue(any("baton issue create" in p for p in problems))

    def test_reads_are_allowed(self):
        for directory in (make_repo(), make_repo(brief=None)):
            for cmd in READS:
                with self.subTest(cmd=cmd):
                    self.assertEqual(gate.decide(cmd, directory, TERMS), [])

    def test_raw_writes_after_other_commands_are_refused(self):
        for cmd in ("cd /somewhere && gh issue edit 1 --title t", "echo a#b; gh pr ready 5",
                    '"gh" issue create --title t --body x', "env FOO=1 gh pr merge 5",
                    "sudo gh issue close 5", "true | gh issue edit 1 --title t",
                    "env -i gh issue edit 1 --title t"):
            with self.subTest(cmd=cmd):
                self.assertTrue(gate.decide(cmd, make_repo(brief=None), TERMS))

    def test_wrappers_that_hide_gh_or_git_are_refused(self):
        for cmd in ('bash -c "gh issue edit 1"', "sh -c 'gh issue edit 1'", "xargs gh issue edit",
                    "echo 1 | xargs -n1 gh issue close", 'eval "gh pr merge 5"',
                    "zsh -c 'git push'", "python3 -c \"import os; os.system('gh pr ready 5')\"",
                    "node -e \"require('child_process').exec('gh issue edit 1')\"",
                    "perl -e 'system(q(gh issue edit 1))'", "ruby -e 'system(%q(git push))'",
                    "source gh", "sudo bash -c 'gh issue edit 1'", "command sh -c 'gh pr ready 5'"):
            with self.subTest(cmd=cmd):
                problems = gate.decide(cmd, make_repo(), TERMS)
                self.assertTrue(any("cannot tell" in p for p in problems), problems)

    def test_wrappers_without_gh_or_git_are_allowed(self):
        for cmd in ('bash -c "ls"', "python3 -m unittest tests.test_gate", "node build.js",
                    "python3 script.py --github", "bash run.sh"):
            with self.subTest(cmd=cmd):
                self.assertEqual(gate.decide(cmd, make_repo(), TERMS), [])

    def test_git_publishing_must_be_plain(self):
        repo = make_repo()
        for cmd in ('cd sub && git commit -m "fix"', 'cd /x; git push', "pushd /x && git push",
                    "builtin cd /x && git commit -m x", "popd && git commit -m x",
                    'git -C x push', 'git -C x commit -m "fix"', "git -Cx push",
                    "git --git-dir=.git push", "git --git-dir .git commit -m x",
                    "git --work-tree=. commit -m x", "git --namespace=n push",
                    "git --exec-path=/x push", "git -c user.name=x commit -m x",
                    "git -cuser.name=x commit -m x"):
            with self.subTest(cmd=cmd):
                problems = gate.decide(cmd, repo, TERMS)
                self.assertTrue(any("plain command" in p for p in problems), problems)

    def test_plain_git_publishing_is_allowed(self):
        repo = make_repo()
        for cmd in ('git commit -m "fix typo"', "git push origin HEAD:refs/heads/x",
                    f"git push {SSH_URL} HEAD",
                    'git commit -m "bump actions/checkout@v4.1.0"',
                    'echo a#b; git commit -m "Closes #4"',
                    "docker run -e HOME=/tmp img && git push",
                    "git push -n origin main", "git tag -a v1 -m release",
                    "git -C x log", "cd x && git log"):
            with self.subTest(cmd=cmd):
                self.assertEqual(gate.decide(cmd, repo, TERMS), [])

    def test_git_publishing_without_a_brief_is_refused(self):
        repo = make_repo(brief=None)
        for cmd in ("git commit -m x", "git push", "git tag v1", 'git commit -m "fix"'):
            with self.subTest(cmd=cmd):
                problems = gate.decide(cmd, repo, TERMS)
                self.assertTrue(any("no Baton brief" in p for p in problems), problems)

    def test_brief_is_looked_up_from_cwd_only(self):
        repo = make_repo()
        os.mkdir(os.path.join(repo, "sub"))
        self.assertEqual(gate.decide("git commit -m x", os.path.join(repo, "sub"), TERMS), [])
        other = tempfile.mkdtemp()
        self.assertTrue(gate.decide(f"cd {repo} && git commit -m x", other, TERMS))
        self.assertTrue(gate.decide("git commit -m x", other, TERMS))

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

    def test_hooks_must_be_in_the_effective_hooks_path(self):
        repo = make_repo()
        subprocess.run(["git", "-C", repo, "config", "core.hooksPath", ".husky"], check=True)
        problems = gate.decide("git commit -m x", repo, TERMS)
        self.assertTrue(any("install-hooks" in p for p in problems))

    def test_commit_message_is_scanned(self):
        problems = gate.decide('git commit -m "fix Zebrowski import"', make_repo(), TERMS)
        self.assertTrue(any("private-term" in p for p in problems))
        problems = gate.decide(f'git tag -m "host {IP}" v1', make_repo(), TERMS)
        self.assertTrue(any("private-ip" in p for p in problems))

    def test_heredoc_commit_message_is_scanned(self):
        cmd = f"git commit -m \"$(cat <<'EOF'\nmail {MAIL}\nEOF\n)\""
        self.assertTrue(any("email" in p for p in gate.decide(cmd, make_repo(), TERMS)))

    def test_message_file_is_read_relative_to_cwd(self):
        repo = make_repo()
        pathlib.Path(repo, "msg.txt").write_text("the Zebrowski set\n")
        problems = gate.decide("git commit -F msg.txt", repo, TERMS)
        self.assertTrue(any("private-term" in p for p in problems))
        pathlib.Path(repo, "ok.txt").write_text("fine\n")
        self.assertEqual(gate.decide("git commit -F ok.txt", repo, TERMS), [])
        self.assertEqual(gate.decide(f"git commit --file={repo}/ok.txt", repo, TERMS), [])

    def test_missing_or_stdin_message_file_is_refused(self):
        problems = gate.decide("git commit -F nope.txt", make_repo(), TERMS)
        self.assertTrue(any("not found" in p for p in problems))
        for cmd in ("git commit -F -", "cat m | git commit -F -"):
            with self.subTest(cmd=cmd):
                self.assertTrue(any("stdin" in p for p in gate.decide(cmd, make_repo(), TERMS)))

    def test_oversized_message_file_is_refused(self):
        repo = make_repo()
        pathlib.Path(repo, "big.txt").write_text("x" * (gate._MAX_FILE + 1))
        problems = gate.decide("git commit -F big.txt", repo, TERMS)
        self.assertTrue(any("too large" in p for p in problems))

    def test_message_from_substitution_or_variable_is_refused(self):
        for cmd in ('git commit -m "$(cat body.md)"', "git commit -m $(cat body.md)",
                    'git commit -m "$MSG"'):
            with self.subTest(cmd=cmd):
                self.assertTrue(any("cannot be scanned" in p
                                    for p in gate.decide(cmd, make_repo(), TERMS)))

    def test_text_after_a_hash_is_still_gated(self):
        cmd = f'echo a#b; git commit -m "{IP}"'
        self.assertTrue(any("private-ip" in p for p in gate.decide(cmd, make_repo(), TERMS)))

    def test_quoted_program_name_is_gated(self):
        cmd = f'"git" commit -m "{IP}"'
        self.assertTrue(any("private-ip" in p for p in gate.decide(cmd, make_repo(), TERMS)))

    def test_unparsable_brief_blocks(self):
        repo = make_repo(brief="---\nnot a mapping\n---\n")
        problems = gate.decide("git commit -m ok", repo, TERMS)
        self.assertTrue(any("brief.md" in p for p in problems))

    def test_allow_list_from_brief(self):
        repo = make_repo(brief=f'---\nprivacy_allow: ["{HOME}"]\n---\n')
        self.assertEqual(gate.decide(f'git commit -m "WORKDIR {HOME}"', repo, TERMS), [])
        self.assertTrue(gate.decide(f'git commit -m "host {IP}"', repo, TERMS))

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
                   "tool_input": {"command": f'git commit -m "{IP}"'}}
        os.environ["BATON_PRIVATE_TERMS"] = "/nonexistent"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(gate.main(json.dumps(payload)), 2)
        self.assertIn("private-ip", stderr.getvalue())
        self.assertNotIn(IP, stderr.getvalue())
        payload["tool_input"]["command"] = "gh pr ready 5"
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(gate.main(json.dumps(payload)), 2)
        self.assertIn("baton pr ready", stderr.getvalue())
        payload["tool_name"] = "Read"
        self.assertEqual(gate.main(json.dumps(payload)), 0)
        self.assertEqual(gate.main("not json"), 0)
