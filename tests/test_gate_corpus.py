"""Corpus of command shapes for gate.decide.

ALLOWED holds harmless shapes, grouped by what a new rule might hurt; a failing subTest names
the group. REFUSED holds publishes whose text carries one private finding each, text the gate cannot read
("unreadable"), hook bypasses, close and reopen commands without a reason, and empty bodies, refused only
where there is a brief. Every private
value is assembled from pieces so the commit scan sees no literal private detail.
"""
import pathlib
import unittest

from baton import gate
from tests.test_gate import make_repo

TERMS = ["Zebrowski"]
IP = ".".join(["192", "168", "7", "7"])
HOME_PATH = "/" + "home/app/work"
MAIL = "alice" + "@" + "corp-mail.test"
TERM = "Zebrowski"

ALLOWED = {
    # (a) raw forge writes with a clean body
    "a raw forge writes": (
        "gh issue comment 5 --body x",
        "gh pr create --title t --body x",
        "gh pr edit 5 --body x",
        "gh pr merge 5 --merge",
        "gh label create bug",
        "gh api -X POST repos/example-org/example-repo/issues/5/comments -f body=x",
        "gh issue edit 5 --add-label bug",
    ),
    # (b) wrappers and scripts that merely mention gh or git
    "b wrappers mentioning gh or git": (
        "python3 - <<'EOF'\nimport subprocess\nsubprocess.run(['git', 'log'])\nprint('git log')\nEOF",
        "docker run img git status",
        "ssh host 'gh issue view 5'",
        "xargs -n1 echo",
        'bash -c "git log"',
        "env FOO=1 ls",
    ),
    # (c) compound and non-plain git publishing
    "c compound git publishing": (
        'cd sub && git commit -m "fix" && git push',
        'git add -A && git commit -m "x"',
        "git -C sub push",
        "git -c user.name=x commit -m x",
        "printf 'x' > notes.md && git commit -F notes.md",
        "echo x > notes.md; git commit -F notes.md",
    ),
    # (d) a directory that is a variable or substitution
    "d variable directories": (
        "cd $DIR && gh issue view 5",
        'cd "$(pwd)/sub" && git status',
        "cd ${DIR}/sub && gh pr view 5",
    ),
    # (e) gh api reads with extra options
    "e gh api reads": (
        "gh api repos/example-org/example-repo/pulls --paginate --jq '.[].number'",
        'gh api -H "Accept: application/vnd.github+json" repos/example-org/example-repo/issues/5',
        "gh api repos/example-org/example-repo/issues/5 --jq .title",
    ),
    # (f) body files and heredocs
    "f body files and heredocs": (
        "gh issue comment 5 --body-file notes.md",
        "gh pr create --title t --body-file notes.md",
        'gh pr create --title t --body "$(cat <<\'EOF\'\nSummary of the change\nEOF\n)"',
        "gh issue comment 5 --body-file - <<'EOF'\nA clean comment\nEOF",
    ),
    # (h) cd and cat bodies
    "h cd and cat bodies": (
        "cd sub && gh issue comment 5 --body-file notes.md",
        'gh issue comment 5 --body "$(cat notes.md)"',
        'cd sub && gh pr create --title t --body "$(cat notes.md)"',
    ),
    # (i) variables, pipes and unquoted values
    "i variables, pipes and unquoted values": (
        "gh issue comment 5 --body plain-word",
        "gh api -X POST repos/example-org/example-repo/issues -f title=Word",
        "glab issue create -t t -d Word",
        "tea issues create -t t",
        'gh issue comment 5 --body "costs $5"',
        "gh pr create --base $BASE --head $BRANCH --title t --body x",
        'gh pr list --search "author:$USER"',
        "gh api repos/$OWNER/example-repo/pulls --jq .[].number",
        "git checkout -b $BRANCH && git push -u origin $BRANCH && gh pr create --title t --body x",
        "cat <<'EOF' | gh issue comment 5 --body-file -\nA clean comment\nEOF",
        "echo msg | git commit -F -",
        "gh pr view 5 | head -5",
    ),
    # (g) repository selectors
    "g repository selectors": (
        "gh -R example-org/example-repo issue view 5",
        "gh issue view 5 -R example-org/example-repo",
        "gh --repo example-org/example-repo pr list",
    ),
    # (h) bypass words in messages
    "h bypass words in messages": (
        'git commit -m "use --no-verify rarely"',
        "git commit -m \"use '--no-verify' rarely\"",
        "git commit -m 'mention \"-n\" here'",
        'git commit -m "--no-verify"',
        "git config get core.hooksPath && git push",
    ),
    # (j) close and reopen with a reason
    "j close and reopen with a reason": (
        'gh issue close 5 --comment "Done in #6"',
        "gh pr close 5 -c superseded --delete-branch",
        "gh issue reopen 5 --comment=again",
        'gh -R example-org/example-repo issue close 5 --comment "duplicate of #4"',
        "gh issue close --help",
        "gh issue view 5 --comments",
        "gh issue list --state closed",
        'gh issue comment 5 --body "close this"',
    ),
    # (k) bodies that are not empty
    "k bodies that are not empty": (
        "gh issue create --title t --body x",
        "gh issue edit 5 --body-file notes.md",
        "gh pr edit 5 --title t",
        "gh issue edit 5 --milestone m",
        "gh pr create --fill",
        'gh issue list --search ""',
        'gh pr list --search ""',
        "echo hi > new.md && gh pr edit 5 --body-file new.md",
    ),
}

# (command shape, expected finding kind); kind "bypass" is a hook bypass, not a privacy finding
REFUSED = (
    ('gh issue comment 5 --body "host %s is down"' % IP, "private-ip"),
    ('gh issue comment 5 --body "see %s"' % HOME_PATH, "host-path"),
    ('gh pr create --title t --body "ask %s"' % MAIL, "email"),
    ('gh pr create --title t --body "about %s"' % TERM, "private-term"),
    ("gh issue comment 5 --body-file - <<'EOF'\nhost %s\nEOF" % IP, "private-ip"),
    ("gh issue comment 5 --body-file - <<'EOF'\nsee %s\nEOF" % HOME_PATH, "host-path"),
    ("gh issue comment 5 --body-file - <<'EOF'\nmail %s\nEOF" % MAIL, "email"),
    ("gh issue comment 5 --body-file - <<'EOF'\nabout %s\nEOF" % TERM, "private-term"),
    ("gh issue comment 5 --body-file private.md", "private-ip"),
    ("cd sub && gh issue comment 5 --body-file private.md", "private-ip"),
    ('gh issue comment 5 --body "$(cat private.md)"', "private-ip"),
    ("curl -X POST https://api.example.com/x -d 'host %s'" % IP, "private-ip"),
    ("curl -X POST https://api.example.com/x -d 'mail %s'" % MAIL, "email"),
    ("curl -X POST https://api.example.com/x -d 'about %s'" % TERM, "private-term"),
    ('git commit -m "host %s"' % IP, "private-ip"),
    ('git commit -m "see %s"' % HOME_PATH, "host-path"),
    ('git commit -m "ask %s"' % MAIL, "email"),
    ('git commit -m "about %s"' % TERM, "private-term"),
    ('git commit "--no-verify" -m x', "bypass"),
    ('git commit "--no-verify";echo x', "bypass"),
    ("git push '-n' origin x", "bypass"),
    ("git config set core.hooksPath /dev/null && git push", "bypass"),
    ("gh issue comment 5 --body %s" % IP, "private-ip"),
    ("gh api -X POST repos/example-org/example-repo/issues -f title=%s" % TERM, "private-term"),
    ("glab issue create -t t -d %s" % MAIL, "email"),
    ("cat <<'EOF' | gh issue comment 5 --body-file -\nhost %s\nEOF" % IP, "private-ip"),
    ('gh issue comment 5 --body "$B"', "unreadable"),
    ("gh issue comment 5 --body $B", "unreadable"),
    ('gh pr create --title "${T}" --body x', "unreadable"),
    ("gh api -X POST repos/example-org/example-repo/issues -f title=$T", "unreadable"),
    ("echo x | gh issue comment 5 --body-file -", "unreadable"),
    ("glab issue create -t t --description -", "unreadable"),
    ("gh issue close 5", "close-reason"),
    ("gh pr close 5 --delete-branch", "close-reason"),
    ("gh issue reopen 5", "close-reason"),
    ("gh pr reopen 5", "close-reason"),
    ("gh issue close --help; gh issue close 6", "close-reason"),
    ("gh issue close 5 --comment x; gh issue close 6", "close-reason"),
    ('gh issue create --title t --body ""', "empty-body"),
    ('gh pr create --title t --body "  "', "empty-body"),
    ("gh issue edit 5 --body ''", "empty-body"),
    ('gh pr edit 5 -b ""', "empty-body"),
    ("gh issue edit 5 --body-file empty.md", "empty-body"),
    ("gh pr edit 5 -F empty.md", "empty-body"),
    ("gh issue close 5 --comment '<!-- baton:developer:closed -->x'; gh issue close 6", "close-reason"),
)


class CorpusTest(unittest.TestCase):
    def directories(self):
        return [make_repo(), make_repo(brief=None)]

    def test_allowed_shapes(self):
        for cwd in self.directories():
            (pathlib.Path(cwd) / "notes.md").write_text("A clean note\n")
            (pathlib.Path(cwd) / "sub").mkdir(exist_ok=True)
            (pathlib.Path(cwd) / "sub" / "notes.md").write_text("A clean note\n")
            has_brief = pathlib.Path(cwd, ".baton").exists()
            for group, commands in ALLOWED.items():
                for cmd in commands:
                    with self.subTest(group=group, cmd=cmd, brief=has_brief):
                        self.assertEqual(gate.decide(cmd, cwd, TERMS), [])

    def test_private_publishes_are_refused(self):
        for cwd in self.directories():
            (pathlib.Path(cwd) / "private.md").write_text("host %s\n" % IP)
            (pathlib.Path(cwd) / "empty.md").write_text("")
            (pathlib.Path(cwd) / "sub").mkdir(exist_ok=True)
            (pathlib.Path(cwd) / "sub" / "private.md").write_text("host %s\n" % IP)
            has_brief = pathlib.Path(cwd, ".baton").exists()
            for cmd, kind in REFUSED:
                with self.subTest(cmd=cmd, brief=has_brief):
                    problems = gate.decide(cmd, cwd, TERMS)
                    if kind == "empty-body":
                        if has_brief:
                            self.assertIn(gate._EMPTY_BODY, problems)
                        else:
                            self.assertEqual(problems, [])
                        continue
                    if kind == "close-reason":
                        if has_brief:
                            self.assertIn(gate._CLOSE_REASON, problems)
                        else:
                            self.assertEqual(problems, [])
                        continue
                    self.assertTrue(problems)
                    if kind == "unreadable":
                        self.assertTrue(any("cannot be scanned" in p for p in problems), problems)
                    elif kind == "bypass":
                        self.assertTrue(any("bypass" in p for p in problems), problems)
                    else:
                        self.assertTrue(
                            any(p.startswith("privacy:") and kind in p for p in problems),
                            problems)


if __name__ == "__main__":
    unittest.main()
