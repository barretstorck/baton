import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from baton import gate, githooks

TERMS = ["Zebrowski"]
ZEROS = "0" * 40


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def make_repo(brief='---\nfixtures_paths: ["tests/fixtures/"]\n---\n'):
    repo = tempfile.mkdtemp()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "dev@example.com")
    git(repo, "config", "user.name", "Dev")
    if brief is not None:
        pathlib.Path(repo, ".baton").mkdir()
        pathlib.Path(repo, ".baton", "brief.md").write_text(brief)
    pathlib.Path(repo, "a.txt").write_text("hello\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    return repo


def write(repo, name, content):
    path = pathlib.Path(repo, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode())


class AddedTextTest(unittest.TestCase):
    def test_keeps_only_added_lines(self):
        patch = ("diff --git a/Zebrowski.txt b/Zebrowski.txt\n--- a/x\n+++ b/x\n"
                 "@@ -5 +5 @@ def connect(host=\"192.168.7.7\"):\n"
                 "-removed 192.168.7.7\n context\n+added\n")
        self.assertEqual(githooks.added_text(patch), "added")


class AddedByFileTest(unittest.TestCase):
    def test_odd_line_separators_stay_inside_the_added_line(self):
        for sep in ("\x0c", "\x85", "\u2028"):
            patch = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n"
                     f"+x{sep}secret\n")
            self.assertEqual(githooks.added_by_file(patch), [("a.py", f"x{sep}secret")])
            self.assertEqual(githooks.added_text(patch), f"x{sep}secret")

    def test_quoted_names_are_unquoted(self):
        patch = ('diff --git "a/caf\\303\\251 \\"x\\".py" "b/caf\\303\\251 \\"x\\".py"\n'
                 '--- /dev/null\n+++ "b/caf\\303\\251 \\"x\\"\\t.py"\n@@ -0,0 +1 @@\n+bad\n')
        self.assertEqual(githooks.added_by_file(patch), [('caf\u00e9 "x"\t.py', "bad")])

    def test_unparsable_quoted_name_is_flagged(self):
        patch = 'diff --git a/x b/x\n--- a/x\n+++ "b/\\377.py"\n@@ -0,0 +1 @@\n+bad\n'
        self.assertEqual(githooks.unparsable_headers(patch), ['+++ "b/\\377.py"'])
        self.assertEqual(githooks.added_by_file(patch), [('"b/\\377.py"', "bad")])

    def test_splits_per_file_and_keeps_plus_lines_in_hunks(self):
        patch = ("diff --git a/one.txt b/one.txt\n--- a/one.txt\n+++ b/one.txt\n"
                 "@@ -1 +1,2 @@\n-old\n+new\n+++ not a header\n"
                 "diff --git a/gone.txt b/gone.txt\ndeleted file mode 100644\n"
                 "--- a/gone.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-bye\n"
                 "diff --git a/sub/two.txt b/sub/two.txt\nnew file mode 100644\n"
                 "--- /dev/null\n+++ b/sub/two.txt\n@@ -0,0 +1 @@\n+second\n"
                 "diff --git a/mode.sh b/mode.sh\nold mode 100644\nnew mode 100755\n")
        self.assertEqual(githooks.added_by_file(patch), [
            ("one.txt", "new\n++ not a header"), ("sub/two.txt", "second")])


class PreCommitTest(unittest.TestCase):
    def test_clean_change_passes(self):
        repo = make_repo()
        write(repo, "a.txt", "hello\nworld\n")
        git(repo, "add", "-A")
        self.assertEqual(githooks.pre_commit(repo, TERMS), [])

    def test_added_private_term_blocks(self):
        repo = make_repo()
        write(repo, "a.txt", "hello\nZebrowski\n")
        git(repo, "add", "-A")
        self.assertTrue(any("private-term" in p for p in githooks.pre_commit(repo, TERMS)))

    def test_removing_private_text_passes(self):
        repo = make_repo()
        write(repo, "a.txt", "hello\nhost 192.168.7.7\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "leak")
        write(repo, "a.txt", "hello\n")
        git(repo, "add", "-A")
        self.assertEqual(githooks.pre_commit(repo, TERMS), [])

    def test_removing_a_file_with_a_private_name_passes(self):
        repo = make_repo()
        write(repo, "Zebrowski.txt", "x\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "leak")
        git(repo, "rm", "-q", "Zebrowski.txt")
        self.assertEqual(githooks.pre_commit(repo, TERMS), [])

    def test_adding_a_file_with_a_private_name_blocks(self):
        repo = make_repo()
        write(repo, "scans/Zebrowski.txt", "x\n")
        git(repo, "add", "-A")
        self.assertTrue(any("private-term" in p for p in githooks.pre_commit(repo, TERMS)))

    def test_edit_below_a_private_line_passes(self):
        repo = make_repo()
        body = 'def connect(host="192.168.7.7"):\n' + "".join(f"    step{i}()\n" for i in range(8))
        write(repo, "net.py", body)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "leak")
        write(repo, "net.py", body.replace("step6()", "step6(retry=True)"))
        git(repo, "add", "-A")
        self.assertEqual(githooks.pre_commit(repo, TERMS), [])

    def test_hooks_follow_core_hookspath(self):
        repo = make_repo()
        git(repo, "config", "core.hooksPath", ".husky")
        written = githooks.install(repo, "/opt/baton/bin/baton")
        self.assertTrue(all("/.husky/" in path for path in written))

    def test_new_image_outside_fixtures_blocks(self):
        repo = make_repo()
        write(repo, "docs/face.JPG", b"\xff\xd8")
        git(repo, "add", "-A")
        self.assertTrue(any("image" in p for p in githooks.pre_commit(repo, TERMS)))

    def test_new_image_in_fixtures_passes(self):
        repo = make_repo()
        write(repo, "tests/fixtures/chart.png", b"\x89PNG")
        git(repo, "add", "-A")
        self.assertEqual(githooks.pre_commit(repo, TERMS), [])

    def test_non_baton_repo_is_ignored(self):
        repo = make_repo(brief=None)
        write(repo, "a.txt", "Zebrowski\n")
        git(repo, "add", "-A")
        self.assertEqual(githooks.pre_commit(repo, TERMS), [])


class CommitMsgTest(unittest.TestCase):
    def check(self, text):
        repo = make_repo()
        path = pathlib.Path(repo, "MSG")
        path.write_text(text)
        return githooks.commit_msg(repo, str(path), TERMS)

    def test_clean_message_passes(self):
        self.assertEqual(self.check("Fix the parser\n"), [])

    def test_private_message_blocks(self):
        problems = self.check("Ask Zebrowski\n")
        self.assertTrue(any("private-term" in p and "commit message" in p for p in problems))

    def test_private_text_on_a_comment_line_passes(self):
        self.assertEqual(self.check("Fix it\n# Zebrowski\n"), [])

    def test_non_baton_repo_is_ignored(self):
        repo = make_repo(brief=None)
        path = pathlib.Path(repo, "MSG")
        path.write_text("Zebrowski\n")
        self.assertEqual(githooks.commit_msg(repo, str(path), TERMS), [])


VERSION = ".".join(["10", "1", "2", "3"])


class DependencyFileTest(unittest.TestCase):
    def staged(self, name, content):
        repo = make_repo()
        write(repo, name, content)
        git(repo, "add", "-A")
        return repo

    def test_version_in_lockfiles_passes(self):
        for name in ("uv.lock", "sub/requirements-dev.txt"):
            repo = self.staged(name, f'nvidia-cublas = "{VERSION}"\n')
            self.assertEqual(githooks.pre_commit(repo, TERMS), [], name)

    def test_version_in_other_file_is_reported(self):
        repo = self.staged("a.txt", f"nvidia-cublas {VERSION}\n")
        self.assertTrue(any("private-ip" in p for p in githooks.pre_commit(repo, TERMS)))

    def test_other_kinds_in_lockfile_are_reported(self):
        lines = {
            "email": "mail " + "alice@corp-mail" + ".test",
            "host-path": "path = " + "/Us" + "ers/alice/proj",
            "internal-domain": "url = host" + ".internal",
        }
        for kind, line in lines.items():
            repo = self.staged("uv.lock", line + "\n")
            problems = githooks.pre_commit(repo, TERMS)
            self.assertTrue(any(kind in p for p in problems), (kind, problems))

    def test_push_of_new_branch_with_lockfile_passes(self):
        repo = self.staged("uv.lock", f'nvidia-cublas = "{VERSION}"\n')
        git(repo, "commit", "-q", "-m", "relock")
        head = git(repo, "rev-parse", "HEAD")
        line = f"refs/heads/main {head} refs/heads/main {ZEROS}\n"
        self.assertEqual(githooks.pre_push(repo, line, TERMS), [])


FORBID = '---\nforbid_added:\n  "pyproject.toml": \'^\\s*"src/\'\n---\n'
PLAN_PATH = "docs/baton/plans/7-x.md"
ALLOWS = '- [ ] **1. t**\n  - Allows: forbid_added "pyproject.toml"\n'


def gh_body(plan):
    """Patch `run.run` so `gh issue view 7 --json body` answers with a body holding `plan()`."""
    real = githooks.run.run

    def fake(cmd, *args, **kwargs):
        if cmd[:3] == ["gh", "repo", "view"]:
            return json.dumps({"owner": {"login": "owner"}})
        if cmd[:3] == ["gh", "issue", "view"]:
            text = "## Developer Plan\n\n" + plan() if cmd[3] == "7" else ""
            return json.dumps({"author": {"login": "owner"}, "body": text})
        return real(cmd, *args, **kwargs)
    return mock.patch.object(githooks.run, "run", fake)


class ForbidTest(unittest.TestCase):
    def setUp(self):
        self.plan = ""
        patcher = gh_body(lambda: self.plan)
        patcher.start()
        self.addCleanup(patcher.stop)

    def staged(self, branch="issue-7-x", plan=None):
        repo = make_repo(FORBID)
        self.plan = plan or ""
        git(repo, "checkout", "-q", "-b", branch)
        write(repo, "pyproject.toml", '[x]\n"src/a" = [1]\n')
        git(repo, "add", "-A")
        return repo

    def test_matching_added_line_blocks(self):
        problems = githooks.pre_commit(self.staged(), TERMS)
        self.assertTrue(any("forbidden added line in pyproject.toml" in p for p in problems))

    def test_plan_allowance_passes(self):
        self.assertEqual(githooks.pre_commit(self.staged(plan=ALLOWS), TERMS), [])

    def test_plan_without_allowance_blocks(self):
        repo = self.staged(plan="- [ ] **1. t**\n")
        self.assertTrue(githooks.pre_commit(repo, TERMS))

    def test_detached_head_still_enforces(self):
        repo = self.staged()
        git(repo, "commit", "-q", "-m", "c")
        git(repo, "checkout", "-q", "--detach")
        write(repo, "pyproject.toml", '[x]\n"src/a" = [1]\n"src/b" = [2]\n')
        git(repo, "add", "-A")
        self.assertTrue(githooks.pre_commit(repo, TERMS))

    def test_no_rules_no_change(self):
        repo = make_repo()
        write(repo, "pyproject.toml", '"src/a" = [1]\n')
        git(repo, "add", "-A")
        self.assertEqual(githooks.pre_commit(repo, TERMS), [])

    def test_push_blocks_and_uses_pushed_branch_allowance(self):
        repo = self.staged(plan=ALLOWS)
        git(repo, "commit", "-q", "-m", "c")
        head = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-q", "--detach")
        ok = f"refs/heads/issue-7-x {head} refs/heads/issue-7-x {ZEROS}\n"
        self.assertEqual(githooks.pre_push(repo, ok, TERMS), [])
        bad = f"refs/heads/other {head} refs/heads/other {ZEROS}\n"
        self.assertTrue(any("forbidden" in p for p in githooks.pre_push(repo, bad, TERMS)))

    def pushed(self):
        repo = self.staged(plan=ALLOWS)
        git(repo, "commit", "-q", "-m", "c")
        head = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-q", "--detach")
        return repo, head

    def test_push_from_other_issue_branch_does_not_borrow_allowance(self):
        repo, head = self.pushed()
        line = f"refs/heads/issue-8-y {head} refs/heads/issue-7-x {ZEROS}\n"
        self.assertTrue(any("forbidden" in p for p in githooks.pre_push(repo, line, TERMS)))

    def test_push_of_detached_head_to_an_issue_branch_gets_allowance(self):
        repo, head = self.pushed()
        line = f"HEAD {head} refs/heads/issue-7-x {ZEROS}\n"
        self.assertEqual(githooks.pre_push(repo, line, TERMS), [])

    def test_push_of_detached_head_to_another_issue_gets_none(self):
        repo, head = self.pushed()
        line = f"HEAD {head} refs/heads/issue-8-y {ZEROS}\n"
        self.assertTrue(any("forbidden" in p for p in githooks.pre_push(repo, line, TERMS)))

    def test_push_with_matching_issue_branch_passes(self):
        repo, head = self.pushed()
        line = f"refs/heads/issue-7-x {head} refs/heads/issue-7-x {ZEROS}\n"
        self.assertEqual(githooks.pre_push(repo, line, TERMS), [])


class PrePushTest(unittest.TestCase):
    def test_new_branch_scans_commits_not_on_remote(self):
        repo = make_repo()
        write(repo, "a.txt", "hello\nmail alice@corp-mail.test\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "add contact")
        head = git(repo, "rev-parse", "HEAD")
        line = f"refs/heads/main {head} refs/heads/main {ZEROS}\n"
        self.assertTrue(any("email" in p for p in githooks.pre_push(repo, line, TERMS)))

    def test_commit_message_is_scanned(self):
        repo = make_repo()
        base = git(repo, "rev-parse", "HEAD")
        write(repo, "a.txt", "hello\nmore\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "import scans\n\n- the Zebrowski set")
        head = git(repo, "rev-parse", "HEAD")
        line = f"refs/heads/main {head} refs/heads/main {base}\n"
        self.assertTrue(any("private-term" in p for p in githooks.pre_push(repo, line, TERMS)))

    def test_clean_range_and_branch_delete_pass(self):
        repo = make_repo()
        base = git(repo, "rev-parse", "HEAD")
        write(repo, "a.txt", "hello\nmore\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "more")
        head = git(repo, "rev-parse", "HEAD")
        self.assertEqual(githooks.pre_push(repo, f"refs/heads/main {head} refs/heads/main {base}\n", TERMS), [])
        self.assertEqual(githooks.pre_push(repo, f"(delete) {ZEROS} refs/heads/x {base}\n", TERMS), [])

    def tag_problems(self, message):
        repo = make_repo()
        git(repo, "tag", "-a", "-m", message, "v1")
        sha = git(repo, "rev-parse", "v1")
        return githooks.pre_push(repo, f"refs/tags/v1 {sha} refs/tags/v1 {ZEROS}\n", TERMS)

    def test_clean_annotated_tag_passes(self):
        self.assertEqual(self.tag_problems("release one"), [])

    def test_annotated_tag_message_is_scanned(self):
        problems = self.tag_problems("release the Zebrowski set")
        self.assertTrue(any("private-term" in p and "tag message" in p for p in problems))


class CommitMsgDispatchTest(unittest.TestCase):
    def test_install_writes_executable_commit_msg_hook(self):
        repo = make_repo()
        githooks.install(repo, "/opt/baton/bin/baton")
        hook = pathlib.Path(repo, ".git", "hooks", "commit-msg")
        self.assertTrue(hook.exists())
        self.assertTrue(hook.stat().st_mode & 0o111)

    def run_hook(self, text):
        from baton import cli
        repo = make_repo()
        path = pathlib.Path(repo, "MSG")
        path.write_text(text)
        old = os.getcwd()
        os.chdir(repo)
        try:
            with mock.patch.object(cli.scan, "load_terms", return_value=TERMS), \
                    mock.patch("sys.stderr") as err:
                code = cli.cmd_git_hook(["commit-msg", str(path)])
        finally:
            os.chdir(old)
        return code, "".join(c.args[0] for c in err.write.call_args_list)

    def test_clean_message_returns_zero(self):
        self.assertEqual(self.run_hook("Fix it\n")[0], 0)

    def test_private_message_is_blocked(self):
        code, out = self.run_hook("Ask Zebrowski\n")
        self.assertEqual(code, 1)
        self.assertIn("Baton blocked this commit", out)


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.cache = tempfile.mkdtemp()
        patcher = mock.patch.dict(os.environ, {"CLAUDE_CODE_PLUGIN_CACHE_DIR": self.cache})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fake_baton(self):
        """A fake executable bin/baton that logs its args; returns (install path, marker)."""
        install_path = tempfile.mkdtemp()
        os.makedirs(os.path.join(install_path, "bin"))
        marker = os.path.join(install_path, "ran")
        fake = pathlib.Path(install_path, "bin", "baton")
        fake.write_text(f'#!/bin/sh\necho "$@" >> "{marker}"\n')
        fake.chmod(0o755)
        return install_path, marker

    def _record(self, *install_paths):
        entries = [{"installPath": p} for p in install_paths]
        pathlib.Path(self.cache, "installed_plugins.json").write_text(
            json.dumps({"plugins": {"baton@baton": entries}}))

    def _commit(self, repo):
        write(repo, "b.txt", "x\n")
        git(repo, "add", "-A")
        dirs = [os.path.dirname(shutil.which("git")), os.path.dirname(shutil.which("python3")), "/bin"]
        return subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "b"], capture_output=True,
                              text=True, env=dict(os.environ, PATH=os.pathsep.join(dirs)))

    def _gone(self):
        return os.path.join(tempfile.mkdtemp(), "0.3.0", "bin", "baton")

    def test_install_record_serves_a_hook_with_a_gone_path(self):
        install_path, marker = self._fake_baton()
        self._record(install_path)
        repo = make_repo()
        githooks.install(repo, self._gone())
        self.assertTrue(githooks.installed(repo))
        result = self._commit(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(pathlib.Path(marker).read_text().splitlines()[0].split()[:2], ["git-hook", "pre-commit"])

    def test_install_record_wins_over_existing_recorded_path(self):
        install_path, marker = self._fake_baton()
        other_path, other_marker = self._fake_baton()
        self._record(install_path)
        repo = make_repo()
        githooks.install(repo, os.path.join(other_path, "bin", "baton"))
        result = self._commit(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.exists(marker))
        self.assertFalse(os.path.exists(other_marker))

    def test_first_entry_with_a_binary_is_used(self):
        install_path, marker = self._fake_baton()
        self._record(tempfile.mkdtemp(), install_path)
        repo = make_repo()
        githooks.install(repo, self._gone())
        self.assertTrue(githooks.installed(repo))
        self.assertEqual(self._commit(repo).returncode, 0)
        self.assertTrue(os.path.exists(marker))

    def test_missing_or_invalid_record_with_gone_path_is_not_installed(self):
        repo = make_repo()
        githooks.install(repo, self._gone())
        self.assertFalse(githooks.installed(repo))
        for text in ("{not json", "[]", '{"plugins": {"baton@baton": "x"}}'):
            with self.subTest(text=text):
                pathlib.Path(self.cache, "installed_plugins.json").write_text(text)
                self.assertFalse(githooks.installed(repo))
                result = self._commit(repo)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("baton install-hooks", result.stderr)
                git(repo, "reset", "-q")

    def test_repository_modules_cannot_hijack_the_resolver(self):
        install_path, marker = self._fake_baton()
        self._record(install_path)
        repo = make_repo()
        pwned = os.path.join(repo, "pwned")
        for name in ("json.py", "os.py"):
            write(repo, name, f"open({pwned!r}, 'w').close()\n")
        githooks.install(repo, self._gone())
        result = self._commit(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.exists(pwned))
        self.assertTrue(os.path.exists(marker))

    def test_existing_recorded_path_works_without_a_record(self):
        install_path, marker = self._fake_baton()
        repo = make_repo()
        githooks.install(repo, os.path.join(install_path, "bin", "baton"))
        self.assertTrue(githooks.installed(repo))
        self.assertEqual(self._commit(repo).returncode, 0)
        self.assertTrue(os.path.exists(marker))

    def test_install_writes_tagged_executable_shims(self):
        repo = make_repo()
        written = githooks.install(repo, "/opt/baton/bin/baton")
        self.assertEqual(len(written), 3)
        for path in written:
            content = pathlib.Path(path).read_text()
            self.assertIn(gate.HOOK_TAG, content)
            self.assertIn("/opt/baton/bin/baton", content)
            self.assertTrue(pathlib.Path(path).stat().st_mode & 0o111)

    def test_install_is_repeatable(self):
        repo = make_repo()
        githooks.install(repo, "/opt/baton/bin/baton")
        githooks.install(repo, "/other/bin/baton")
        self.assertIn("/other/bin/baton", pathlib.Path(repo, ".git/hooks/pre-push").read_text())

    def test_hook_whose_baton_is_gone_is_not_installed(self):
        repo = make_repo()
        githooks.install(repo, os.path.join(tempfile.mkdtemp(), "0.3.0", "bin", "baton"))
        self.assertFalse(githooks.installed(repo))

    def test_hook_whose_baton_exists_is_installed(self):
        repo = make_repo()
        githooks.install(repo, sys.executable)
        self.assertTrue(githooks.installed(repo))

    def _commit_with_gone_baton(self, path_dirs):
        repo = make_repo()
        githooks.install(repo, os.path.join(tempfile.mkdtemp(), "0.3.0", "bin", "baton"))
        write(repo, "b.txt", "x\n")
        git(repo, "add", "-A")
        env = dict(os.environ, PATH=os.pathsep.join(path_dirs))
        return subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "b"],
                              capture_output=True, text=True, env=env)

    def test_hook_falls_back_to_baton_on_path(self):
        bindir = tempfile.mkdtemp()
        marker = os.path.join(bindir, "ran")
        fake = pathlib.Path(bindir, "baton")
        fake.write_text(f'#!/bin/sh\necho "$@" >> "{marker}"\n')
        fake.chmod(0o755)
        git_dir = os.path.dirname(shutil.which("git"))
        result = self._commit_with_gone_baton([bindir, git_dir, "/bin"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(pathlib.Path(marker).read_text().splitlines()[0].split()[:2], ["git-hook", "pre-commit"])

    def test_hook_without_any_baton_names_install_hooks(self):
        git_dir = os.path.dirname(shutil.which("git"))
        result = self._commit_with_gone_baton([git_dir, "/bin"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("baton install-hooks", result.stderr)
        self.assertNotIn("not found", result.stderr)

    def test_install_refuses_foreign_hook(self):
        repo = make_repo()
        pathlib.Path(repo, ".git/hooks/pre-commit").write_text("#!/bin/sh\necho mine\n")
        with self.assertRaises(githooks.HookError):
            githooks.install(repo, "/opt/baton/bin/baton")
