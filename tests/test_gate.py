import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
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
        gate.githooks.install(str(root), sys.executable)
    return str(root)


def both_directories():
    """A directory with a brief, and one without."""
    return [make_repo(), make_repo(brief=None)]


ALLOWED = (
    "gh issue comment 5 --body x",
    "gh pr create --title t --body-file notes.md",
    "gh pr merge 5 --merge",
    "gh label create bug",
    "gh api -X POST repos/o/r/issues/5/comments -f body=x",
    "gh api -X POST repos/o/r/issues -F title=t",
    "gh -R o/r issue edit 5 --title t",
    "cd sub && git commit -m \"fix\"",
    "git add -A && git commit -m x && git push",
    "git -C x push",
    "python3 - <<'EOF'\nimport subprocess\nprint('git log')\nEOF",
    "ssh host 'ls'",
    "docker run img git status",
    "cd $DIR && gh issue view 5",
    "bash -c \"ls\"",
    "gh api repos/o/r/pulls --paginate",
    "docker run -e HOME=/x img ls",
    f"ls {IP}",
    f"curl https://{IP}/x",
)


def refused_forms(text):
    """The same private text in each place a command can carry it."""
    return {
        "body": f'gh issue comment 5 --body "{text}"',
        "single quoted": f"gh issue comment 5 --body '{text}'",
        "heredoc": f"gh issue comment 5 --body-file - <<'EOF'\n{text}\nEOF",
        "inline heredoc": f"gh issue comment 5 --body \"$(cat <<'EOF'\n{text}\nEOF\n)\"",
        "file": "gh issue comment 5 --body-file notes.md",
        "curl": f"curl -X POST -d '{text}' https://example.test/api",
        "commit": f'git commit -m "{text}"',
    }


class GateTest(unittest.TestCase):
    def prepare(self, directory):
        pathlib.Path(directory, "notes.md").write_text("clean\n")
        return directory

    def test_clean_commands_are_allowed(self):
        for directory in both_directories():
            self.prepare(directory)
            for cmd in ALLOWED:
                with self.subTest(cmd=cmd, brief=os.path.isdir(os.path.join(directory, ".baton"))):
                    self.assertEqual(gate.decide(cmd, directory, TERMS), [])

    def test_private_text_is_refused_wherever_it_sits(self):
        for text in (IP, HOME, MAIL, "Zebrowski"):
            for name, cmd in refused_forms(text).items():
                for directory in both_directories():
                    pathlib.Path(directory, "notes.md").write_text(f"see {text}\n")
                    with self.subTest(text=text[:4], form=name):
                        problems = gate.decide(cmd, directory, TERMS)
                        self.assertTrue(any(p.startswith("privacy:") for p in problems), problems)

    def test_privacy_message_names_the_kind_not_the_text(self):
        problems = gate.decide(f'gh issue comment 5 --body "{IP}"', make_repo(), TERMS)
        self.assertEqual(problems, ["privacy: private-ip in the text this command would publish"])

    def test_bare_words_and_urls_are_not_scanned(self):
        repo = make_repo()
        self.assertEqual(gate.decide(f"ls {IP}", repo, TERMS), [])
        self.assertEqual(gate.decide(f"curl https://{IP}/x", repo, TERMS), [])
        self.assertTrue(gate.decide(f"curl -d '{IP}' https://example.test/x", repo, TERMS))
        self.assertTrue(gate.decide(f"curl --json '{{\"a\": \"{IP}\"}}' https://example.test/x", repo, TERMS))

    def test_stdin_body_needs_a_heredoc_or_a_pipe(self):
        repo = make_repo()
        for cmd in ("gh issue comment 5 -F -", "gh issue comment 5 --body-file -",
                    "gh api x --input -", "curl -d @- https://example.test/x",
                    "curl -d - https://example.test/x"):
            with self.subTest(cmd=cmd):
                self.assertTrue(gate.decide(cmd, repo, TERMS))
        for cmd in ("gh issue comment 5 -F - <<'EOF'\nhello\nEOF",
                    "cat f | gh issue comment 5 -F -",
                    "curl -d @- https://example.test/x <<'EOF'\n{}\nEOF"):
            with self.subTest(cmd=cmd):
                self.assertEqual(gate.decide(cmd, repo, TERMS), [])

    def test_missing_body_file_is_refused_unless_the_command_writes_it(self):
        repo = make_repo()
        problems = gate.decide("gh issue comment 5 --body-file missing.md", repo, TERMS)
        self.assertTrue(any("missing.md" in p for p in problems), problems)
        self.assertEqual(
            gate.decide("echo hi > missing.md && gh issue comment 5 --body-file missing.md", repo, TERMS), [])
        self.assertTrue(gate.decide("git commit -F missing.md", repo, TERMS))

    def test_quoted_attached_and_variable_body_files_are_scanned(self):
        repo = make_repo()
        pathlib.Path(repo, "n.md").write_text(f"server at {IP} here\n")
        pathlib.Path(repo, "clean.md").write_text("fine\n")
        for cmd in ('gh issue comment 5 --body-file "n.md"', "gh issue comment 5 --body-file 'n.md'",
                    "curl -d" + "@n.md https://x.test/api", 'curl -d "@n.md" https://x.test/api',
                    "gh issue comment 5 -Fn.md", "git commit -Fn.md", 'git commit -F "n.md"'):
            problems = gate.decide(cmd, repo, TERMS)
            self.assertTrue(any("privacy" in p for p in problems), (cmd, problems))
        for cmd in ("gh issue comment 5 --body-file $PWD/n.md", "gh issue comment 5 --body-file=$HOME/x.md",
                    "git commit -F $PWD/n.md"):
            self.assertTrue(gate.decide(cmd, repo, TERMS), cmd)
        self.assertEqual(gate.decide("gh issue comment 5 --body-file clean.md", repo, TERMS), [])
        self.assertEqual(gate.decide('gh issue comment 5 --body-file "clean.md"', repo, TERMS), [])

    def test_body_file_is_read_relative_to_cwd_and_oversized_is_refused(self):
        repo = make_repo()
        pathlib.Path(repo, "ok.md").write_text("fine\n")
        self.assertEqual(gate.decide("git commit -F ok.md", repo, TERMS), [])
        pathlib.Path(repo, "big.md").write_text("x" * (gate._MAX_FILE + 1))
        self.assertTrue(gate.decide("gh issue comment 5 --body-file big.md", repo, TERMS))

    def test_command_substitution_in_a_double_quoted_body_is_refused(self):
        repo = make_repo()
        for cmd in ('gh issue comment 5 --body "done `date`"', 'gh issue comment 5 --body "done $(date)"',
                    'gh issue comment 5 -b "x $(date)"'):
            with self.subTest(cmd=cmd):
                self.assertTrue(gate.decide(cmd, repo, TERMS))
        self.assertEqual(
            gate.decide("gh issue comment 5 --body \"$(cat <<'EOF'\nhello\nEOF\n)\"", repo, TERMS), [])
        self.assertEqual(gate.decide("gh issue comment 5 --body 'costs $(5)'", repo, TERMS), [])

    def test_hook_bypass_is_refused(self):
        repo = make_repo()
        for cmd in ("git commit --no-verify -m x", "git push --no-verify", "git commit -n -m x",
                    "git push -n origin main", "git -c core.hooksPath=/x commit -m x",
                    "git config core.hooksPath /x && git commit -m x", "HOME=/x git push",
                    "GIT_CONFIG_GLOBAL=/x git commit -m x", "BATON_PRIVATE_TERMS=/x git commit -m x",
                    "git --config-env=core.hooksPath=V commit -m x",
                    'git -c "core.hooksPath=/x" commit -m x', "HOME=/x git push origin main -m 'a'"):
            with self.subTest(cmd=cmd):
                problems = gate.decide(cmd, repo, TERMS)
                self.assertTrue(any("bypass" in p for p in problems), problems)

    def test_bypass_words_inside_a_quoted_message_are_allowed(self):
        repo = make_repo()
        for cmd in ('git commit -m "mention --no-verify docs"', "git commit -m 'set HOME=/x then -n'"):
            with self.subTest(cmd=cmd):
                problems = gate.decide(cmd, repo, TERMS)
                self.assertFalse(any("bypass" in p for p in problems), problems)

    def test_harmless_git_options_are_not_a_bypass(self):
        repo = make_repo()
        for cmd in ("git commit -m x", "git push origin main", "git push --dry-run origin main",
                    "GIT_AUTHOR_NAME=a git commit -m x", "git commit --amend --no-edit"):
            with self.subTest(cmd=cmd):
                self.assertEqual(gate.decide(cmd, repo, TERMS), [])

    def test_missing_hooks_refuse_publishing_only(self):
        repo = make_repo(hooks=False)
        problems = gate.decide("git commit -m x", repo, TERMS)
        self.assertTrue(any("install-hooks" in p for p in problems))
        self.assertEqual(gate.decide("gh issue view 5", repo, TERMS), [])

    def test_hooks_whose_baton_is_gone_are_refused(self):
        repo = make_repo(hooks=False)
        gate.githooks.install(repo, os.path.join(tempfile.mkdtemp(), "0.3.0", "bin", "baton"))
        self.assertTrue(any("install-hooks" in p for p in gate.decide("git commit -m x", repo, TERMS)))

    def test_hooks_must_be_in_the_effective_hooks_path(self):
        repo = make_repo()
        subprocess.run(["git", "-C", repo, "config", "core.hooksPath", ".husky"], check=True)
        self.assertTrue(any("install-hooks" in p for p in gate.decide("git commit -m x", repo, TERMS)))

    def test_no_brief_still_scans_but_skips_the_hooks_check(self):
        repo = make_repo(brief=None, hooks=False)
        self.assertEqual(gate.decide("git commit -m x", repo, TERMS), [])
        self.assertTrue(gate.decide(f'git commit -m "{IP}"', repo, TERMS))

    def test_invalid_brief_is_refused_only_for_a_triggered_command(self):
        repo = make_repo(brief="---\nnot a mapping\n---\n")
        for cmd in ("git commit -m ok", "gh issue view 5"):
            with self.subTest(cmd=cmd):
                self.assertTrue(any("brief.md" in p for p in gate.decide(cmd, repo, TERMS)))
        self.assertEqual(gate.decide("ls", repo, TERMS), [])

    def test_allow_list_from_brief(self):
        repo = make_repo(brief=f'---\nprivacy_allow: ["{HOME}"]\n---\n')
        self.assertEqual(gate.decide(f'git commit -m "WORKDIR {HOME}"', repo, TERMS), [])
        self.assertTrue(gate.decide(f'git commit -m "host {IP}"', repo, TERMS))

    def test_ssh_remote_url_is_not_an_email(self):
        self.assertEqual(gate.decide(f'git commit -m "remote {SSH_URL}"', make_repo(), TERMS), [])

    def test_forge_facing_trigger(self):
        for cmd in ("gh issue view 5", "cd x && glab mr list", "sudo curl url", "FOO=1 tea login",
                    "/usr/bin/gh pr list", "xargs gh api x", "git commit -m x", "git -C d push"):
            with self.subTest(cmd=cmd):
                self.assertTrue(gate.forge_facing(cmd))
        for cmd in ("ls", "docker run img git status", "echo ghost", "git log", "ssh host 'ls'"):
            with self.subTest(cmd=cmd):
                self.assertFalse(gate.forge_facing(cmd))
        self.assertEqual(gate.FORGE_CLIS[0], "gh")

    def test_main_blocks_with_exit_2_and_ignores_other_tools(self):
        repo = make_repo()
        payload = {"tool_name": "Bash", "cwd": repo,
                   "tool_input": {"command": f'git commit -m "{IP}"'}}
        os.environ["BATON_PRIVATE_TERMS"] = "/nonexistent"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(gate.main(json.dumps(payload)), 2)
        self.assertIn("private-ip", stderr.getvalue())
        self.assertIn("placeholders", stderr.getvalue())
        self.assertNotIn(IP, stderr.getvalue())
        payload["tool_input"]["command"] = "gh pr ready 5"
        self.assertEqual(gate.main(json.dumps(payload)), 0)
        payload["tool_name"] = "Read"
        self.assertEqual(gate.main(json.dumps(payload)), 0)
        self.assertEqual(gate.main("not json"), 0)

    def _main(self, command, tool="Bash"):
        payload = {"tool_name": tool, "cwd": "/", "tool_input": {"command": command}}
        with contextlib.redirect_stderr(io.StringIO()):
            return gate.main(json.dumps(payload))

    def test_main_fails_closed_only_on_a_trigger(self):
        original = gate.decide
        gate.decide = lambda *args: 1 / 0
        try:
            self.assertEqual(self._main("ls"), 0)
            self.assertEqual(self._main("gh issue comment 5 --body x"), 2)
            self.assertEqual(self._main("git commit -m x"), 2)
        finally:
            gate.decide = original

    def test_main_times_out_like_an_error(self):
        import time
        original, limit = gate.decide, gate.TIME_LIMIT
        gate.decide = lambda *args: time.sleep(1) or []
        gate.TIME_LIMIT = 0.1
        try:
            self.assertEqual(self._main("ls"), 0)
            self.assertEqual(self._main("gh issue comment 5 --body x"), 2)
        finally:
            gate.decide, gate.TIME_LIMIT = original, limit

    def test_decide_is_fast_on_pathological_text(self):
        import time
        for unit in ('"`', "'$(", '"'):
            command = "gh issue comment 5 --body " + unit * 70000
            start = time.monotonic()
            gate.decide(command, "/", TERMS)
            self.assertLess(time.monotonic() - start, 2.0)

    def test_hooks_json_registers_mcp_matcher(self):
        path = pathlib.Path(__file__).resolve().parent.parent / "hooks" / "hooks.json"
        entries = json.loads(path.read_text())["hooks"]["PreToolUse"]
        cmd = "\"${CLAUDE_PLUGIN_ROOT}/bin/baton\" gate"
        self.assertTrue(any(e["matcher"] == "mcp__.*"
                            and any(h.get("command") == cmd for h in e["hooks"])
                            for e in entries), entries)

    def test_main_ignores_other_tools(self):
        self.assertEqual(self._main("gh issue comment 5 --body x", tool="Read"), 0)

    def test_main_is_silent_when_private_terms_file_is_missing(self):
        payload = {"tool_name": "Bash", "cwd": "/", "tool_input": {"command": "ls"}}
        os.environ["BATON_PRIVATE_TERMS"] = "/nonexistent/terms.txt"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(gate.main(json.dumps(payload)), 0)
        self.assertEqual(stderr.getvalue(), "")


class McpToolInputTest(unittest.TestCase):
    def run_main(self, tool, tool_input, cwd):
        os.environ["BATON_PRIVATE_TERMS"] = "/nonexistent"
        payload = {"tool_name": tool, "cwd": cwd, "tool_input": tool_input}
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = gate.main(json.dumps(payload))
        return code, stderr.getvalue()

    def test_clean_nested_input_is_allowed(self):
        tool_input = {"title": "t", "items": [{"body": "fine", "n": 3}, "x"]}
        self.assertEqual(self.run_main("mcp__forge__create_issue", tool_input, make_repo())[0], 0)

    def test_private_value_in_nested_input_is_refused(self):
        for secret, kind in ((IP, "private-ip"), (MAIL, "email")):
            with self.subTest(kind=kind):
                tool_input = {"a": {"b": [{"c": "ok"}, {"d": f"host {secret}"}]}}
                code, err = self.run_main("mcp__forge__create_issue", tool_input, make_repo())
                self.assertEqual(code, 2)
                self.assertIn(kind, err)
                self.assertNotIn(secret, err)

    def test_other_tools_and_non_baton_repos_are_allowed(self):
        tool_input = {"body": IP}
        self.assertEqual(self.run_main("Read", tool_input, make_repo())[0], 0)
        self.assertEqual(self.run_main("mcp__x__y", tool_input, make_repo(brief=None))[0], 0)
