import contextlib
import io
import json
import os
import pathlib
import subprocess
import tempfile
import time
import unittest
from baton import gate

TERMS = ["Zebrowski"]
IP = ".".join(["192", "168", "7", "7"])  # assembled so the commit scan sees no literal


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

    def _api(self, command, **files):
        repo = make_repo()
        for name, text in files.items():
            pathlib.Path(repo, name.replace("_", ".")).write_text(text)
        return gate.decide(command, repo, TERMS)

    def _closes(self, problems):
        return any("baton close" in p for p in problems)

    def test_input_file_state_is_refused(self):
        patch = "gh api -X PATCH repos/o/r/issues/5 --input p.json"
        self.assertTrue(self._closes(self._api(patch, p_json='{"state": "closed"}')))
        pull = "gh api -X PATCH repos/o/r/pulls/5 --input p.json"
        self.assertTrue(self._closes(self._api(pull, p_json='{"state_reason": "completed"}')))

    def _two_bases(self, command, first, second):
        repo = make_repo()
        pathlib.Path(repo, "sub").mkdir()
        pathlib.Path(repo, "f.md").write_text(first)
        pathlib.Path(repo, "sub", "f.md").write_text(second)
        return gate.decide(command, repo, TERMS)

    def test_every_base_holding_a_relative_file_is_scanned(self):
        mark = "<!-- baton:developer:status -->\n"
        dirty = mark + "host " + IP
        clean = mark + "fine"
        for cmd in ("gh issue comment 5 --body-file f.md",
                    "gh api -X PATCH repos/o/r/issues/5 -F body=@f.md"):
            full = "cd sub && " + cmd
            self.assertTrue(any("private-ip" in p for p in self._two_bases(full, clean, dirty)), cmd)
            self.assertTrue(any("private-ip" in p for p in self._two_bases(full, dirty, clean)), cmd)
            self.assertEqual([], self._two_bases(full, clean, clean), cmd)
        cmd = "cd sub && gh api -X PATCH repos/o/r/issues/5 --input f.md"
        problems = self._two_bases(cmd, '{"a": 1}', '{"a": "%s"}' % IP)
        self.assertTrue(any("private-ip" in p for p in problems))

    def test_state_payload_in_any_base_is_refused(self):
        cmd = "cd sub && gh api -X PATCH repos/o/r/issues/5 --input f.md"
        problems = self._two_bases(cmd, '{"title": "t"}', '{"state": "closed"}')
        self.assertTrue(self._closes(problems))

    def test_file_only_in_repo_root_is_still_scanned(self):
        repo = make_repo()
        os.mkdir(os.path.join(repo, "sub"))
        pathlib.Path(repo, "r.md").write_text("<!-- baton:developer:status -->\nhost " + IP)
        problems = gate.decide("cd sub && gh issue comment 5 --body-file r.md", repo, TERMS)
        self.assertTrue(any("private-ip" in p for p in problems))

    def test_missing_file_in_every_base_is_not_found(self):
        problems = self._two_bases("cd sub && gh issue comment 5 --body-file nope.md", "x", "y")
        self.assertTrue(any("not found" in p for p in problems))

    def test_body_empty_in_one_base_is_refused(self):
        mark = "<!-- baton:developer:status -->\n"
        cmd = "cd sub && gh issue comment 5 --body-file f.md"
        self.assertTrue(any("empty" in p for p in self._two_bases(cmd, mark + "ok", "")))

    def test_input_file_state_bypasses_are_refused(self):
        patch = "gh api -X PATCH repos/o/r/issues/5 --input p.json"
        escaped = self._api(patch, p_json='{"\\u0073tate": "closed"}')
        self.assertTrue(self._closes(escaped))
        nested = self._api(patch, p_json='{"a": [{"b": {"state_reason": "completed"}}]}')
        self.assertTrue(self._closes(nested))
        for text in ('{"title": ', '["state"]', '"state"'):
            self.assertTrue(any("not a valid JSON object" in p
                                for p in self._api(patch, p_json=text)), text)

    def test_input_file_graphql_decoded_query_is_checked(self):
        cmd = "gh api graphql --input q.json"
        newline = json.dumps({"query": '\nmutation { updateIssue(input:{id:"x", state:CLOSED}){x} }'})
        self.assertTrue(self._closes(self._api(cmd, q_json=newline)))
        escaped = '{"query": "mutation { \\u0063loseIssue(input: {issueId: \\"x\\"}) { x } }"}'
        self.assertTrue(self._closes(self._api(cmd, q_json=escaped)))
        readonly = json.dumps({"query": "query { viewer { login } }"})
        self.assertFalse(self._closes(self._api(cmd, q_json=readonly)))
        self.assertTrue(any("not a valid JSON object" in p
                            for p in self._api(cmd, q_json="mutation { closeIssue }")))

    def test_input_file_graphql_variables_with_state_key_are_refused(self):
        cmd = "gh api graphql --input q.json"
        q = "mutation($i: UpdateIssueInput!) { updateIssue(input: $i) { clientMutationId } }"
        for variables in ({"i": {"state": "CLOSED"}}, {"a": [{"b": {"stateReason": "x"}}]},
                          {"i": {"state_reason": "x"}}):
            payload = json.dumps({"query": q, "variables": variables})
            self.assertTrue(self._closes(self._api(cmd, q_json=payload)), variables)
        for payload in (json.dumps({"query": q, "variables": {"i": {"title": "t", "id": "X"}}}),
                        json.dumps({"query": "query { viewer { login } }", "state": "x",
                                    "variables": {"n": 1}})):
            self.assertFalse(self._closes(self._api(cmd, q_json=payload)), payload)
        self.assertNotIn(gate._CLOSE_MESSAGE, self._api(cmd, q_json="not json"))

    def test_input_file_graphql_string_variables_are_checked(self):
        cmd = "gh api graphql --input q.json"
        q = "mutation($i: UpdateIssueInput!) { updateIssue(input: $i) { clientMutationId } }"

        def run(variables):
            return self._api(cmd, q_json=json.dumps({"query": q, "variables": variables}))
        self.assertTrue(self._closes(run(json.dumps({"i": {"state": "CLOSED"}}))))
        self.assertIn(gate._UNPARSABLE_MESSAGE, run("not json"))
        self.assertIn(gate._UNPARSABLE_MESSAGE, run("[1]"))
        self.assertIn(gate._UNPARSABLE_MESSAGE, run(5))
        self.assertIn(gate._UNPARSABLE_MESSAGE, run([{"i": {"state": "x"}}]))
        self.assertEqual([], run(json.dumps({"i": {"title": "t"}})))

    def test_input_file_graphql_is_refused(self):
        cmd = "gh api graphql --input q.json"
        body = json.dumps({"query": 'mutation { closeIssue(input: {issueId: "x"}) { clientMutationId } }'})
        self.assertTrue(self._closes(self._api(cmd, q_json=body)))
        cmd = "gh api graphql -F query=@q.graphql"
        query = 'mutation { updateIssue(input: {id: "x", state: CLOSED}) { clientMutationId } }'
        self.assertTrue(self._closes(self._api(cmd, q_graphql=query)))

    def test_field_file_on_rest_endpoint_is_not_parsed_as_json(self):
        cmd = "gh api -X PATCH repos/o/r/issues/5 -F body=@n.md"
        text = "<!-- baton:developer:status -->\nplain text, not JSON"
        self.assertEqual(self._api(cmd, n_md=text), [])
        pull = "gh api -X PATCH repos/o/r/pulls/5 -F body=@n.md"
        self.assertEqual(self._api(pull, n_md=text), [])
        state = self._api("gh api -X PATCH repos/o/r/issues/5 -F state=@n.md", n_md="closed")
        self.assertTrue(self._closes(state))

    def test_unreadable_state_payload_is_refused(self):
        missing = self._api("gh api -X PATCH repos/o/r/issues/5 --input missing.json")
        self.assertTrue(any("not found" in p for p in missing))
        stdin = self._api("gh api -X PATCH repos/o/r/issues/5 --input -")
        self.assertTrue(any("stdin" in p for p in stdin))

    def test_other_input_files_are_allowed(self):
        title = self._api("gh api -X PATCH repos/o/r/issues/5 --input p.json", p_json='{"title": "t"}')
        self.assertFalse(self._closes(title))
        comment = self._api("gh api repos/o/r/issues/5/comments --input c.json",
                            c_json='{"body": "<!-- baton:developer:status -->\nok"}')
        self.assertFalse(self._closes(comment))
        labels = self._api("gh api -X POST repos/o/r/issues/5/labels --input l.json",
                           l_json='{"labels": ["bug"]}')
        self.assertFalse(self._closes(labels))
        read = self._api("gh api graphql -F query=@q.graphql",
                         q_graphql="query { viewer { login } }")
        self.assertFalse(self._closes(read))

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
                    "gh pr review 5 --approve"):
            with self.subTest(cmd=cmd):
                self.assertTrue(any("marker" in p for p in gate.decide(cmd, make_repo(), TERMS)))

    def test_raw_close_and_reopen_are_refused(self):
        for group in ("issue", "pr"):
            for action in ("close", "reopen"):
                for tail in ("", ' --comment "done"'):
                    cmd = f"gh {group} {action} 5{tail}"
                    with self.subTest(cmd=cmd):
                        problems = gate.decide(cmd, make_repo(), TERMS)
                        self.assertTrue(any("use `baton close|reopen" in p for p in problems))

    def test_api_state_changes_and_close_mutations_are_refused(self):
        mutations = ("closeIssue", "reopenIssue", "closePullRequest", "reopenPullRequest")
        cmds = ["gh api -X PATCH repos/o/r/issues/5 -f state=closed",
                "gh api -X PATCH repos/o/r/pulls/5 -f state=open",
                "gh api -X PATCH repos/o/r/issues/5 -f state=closed -f state_reason=completed",
                "gh api -X PATCH repos/o/r/issues/5 -F state=closed"]
        cmds += [f"gh api graphql -f query='mutation {{ {m}(input: {{id: \"X\"}}) "
                 f"{{ clientMutationId }} }}'" for m in mutations]
        cmds += [
            "gh api graphql -f query='mutation { updateIssue(input: {id: \"X\", state: CLOSED}) "
            "{ clientMutationId } }'",
            "gh api graphql -f query='mutation { updatePullRequest(input: {id: \"X\", state: OPEN}) "
            "{ clientMutationId } }'",
            "gh api graphql -f query='mutation { updateIssue(input: {id: \"X\", stateReason: COMPLETED}) "
            "{ clientMutationId } }'",
            "gh api graphql -f query='mutation($state: IssueState) { updateIssue(input: {id: \"X\", "
            "state: $state}) { clientMutationId } }' -f state=CLOSED",
            "gh api graphql -f query='mutation { updateIssue(input: {id: \"X\", state , : CLOSED}) "
            "{ clientMutationId } }'",
            "gh api graphql -f query='mutation { updateIssue(input: {id: \"X\", state # c\n: CLOSED}) "
            "{ clientMutationId } }'",
            "gh api graphql -f query='mutation { updateIssue(input: {id: \"X\", state #c\r: CLOSED}) "
            "{ clientMutationId } }'"]
        for ch in ("﻿", " ", " ", "​", "⁠"):
            cmds.append("gh api graphql -f query='mutation { updateIssue(input: {id: \"X\", state"
                        + ch + ": CLOSED}) { clientMutationId } }'")
        for ch in ("\ufeff", "\u200b", "\u2060"):
            cmds.append("gh api graphql -f query='mutation { updateIssue(input: {id: $x" + ch
                        + "state:CLOSED}) { clientMutationId } }'")
        cmds.append("gh api graphql -f query='﻿mutation { closeIssue(input: {id: \"X\"}) "
                    "{ clientMutationId } }'")
        q = ("gh api graphql -f query='mutation($i: UpdateIssueInput!) "
             "{ updateIssue(input: $i) { clientMutationId } }' ")
        cmds += [q + "-F 'i[state]=CLOSED'", q + "-F 'i[stateReason]=COMPLETED'",
                 q + "-F 'i[state_reason]=completed'", q + "-F 'i[input][state]=CLOSED'",
                 q + "-f i='{\"id\":\"X\",\"state\":\"CLOSED\"}'",
                 q + "-F i=@vars.json", q + "-f i=notjson", q + "--input vars.json"]
        for sep in ("input, : $i", "input,: $i", "input :, $i", "input:# c\n$i", "input # c\n: $i",
                    "input:\r# c\r\n,$i", "input: $ i", "input: $,i", "input: $# c\ni"):
            qs = ("gh api graphql -f query='mutation($i: UpdateIssueInput!) { updateIssue("
                  + sep + ") { clientMutationId } }' ")
            cmds += [qs + "-f i=notjson", qs + "-F i=@vars.json"]
        for endpoint in ("/graphql", "https://api.github.com/graphql"):
            cmds.append(f"gh api {endpoint} -f query='mutation {{ closeIssue(input: {{id: \"X\"}}) "
                        f"{{ clientMutationId }} }}'")
        for cmd in cmds:
            with self.subTest(cmd=cmd):
                problems = gate.decide(cmd, make_repo(), TERMS)
                self.assertTrue(any("use `baton close|reopen" in p for p in problems))

    def test_graphql_query_from_file_fails_closed_on_unverifiable_variables(self):
        base = "gh api graphql -F query=@q.graphql "
        for tail in ("-F i=@v.json", "-f i=notjson"):
            with self.subTest(tail=tail):
                problems = gate.decide(base + tail, make_repo(), TERMS)
                self.assertTrue(any("use `baton close|reopen" in p for p in problems))
        for tail in ("-F 'i[title]=t'", "-f 'i[body]=b' -F 'i[n]=3'"):
            with self.subTest(tail=tail):
                problems = gate.decide(base + tail, make_repo(), TERMS)
                self.assertFalse(any("use `baton close|reopen" in p for p in problems))

    def test_api_state_pattern_does_not_backtrack_catastrophically(self):
        for query in ("state#" + " " * 5000, "state#" + "," * 5000, "state" + "# " * 2000,
                      "state" + " ," * 3000 + "#", "stateReason#" + " ," * 3000):
            with self.subTest(query=query[:20]):
                start = time.monotonic()
                self.assertIsNone(gate._API_STATE_ARG.search(query))
                self.assertLess(time.monotonic() - start, 1.0)

    def test_api_comments_labels_edits_and_queries_are_not_refused(self):
        for cmd in ("gh api repos/o/r/issues/5/comments -f body='<!-- baton:developer:status -->\nok'",
                    "gh api -X POST repos/o/r/issues/5/labels -f 'labels[]=bug'",
                    "gh api -X PATCH repos/o/r/issues/5 -f title=t",
                    "gh api graphql -f query='query { viewer { login } }'",
                    "gh api graphql -f query='query { repository(owner: \"o\", name: \"r\") "
                    "{ issues(states: OPEN) { totalCount } } }'",
                    "gh api graphql -f query='mutation { updateIssue(input: {id: \"X\", title: \"t\"}) "
                    "{ clientMutationId } }'",
                    "gh api graphql -f query='mutation { addLabelsToLabelable(input: {labelableId: \"X\", "
                    "labelIds: [\"L\"]}) { clientMutationId } }'",
                    "gh api graphql -f query='mutation($state: IssueState) { updateIssue(input: "
                    "{id: \"X\", title: \"t\"}) { clientMutationId } }'",
                    "gh api graphql -f query='mutation($i: UpdateIssueInput!) { updateIssue(input: $i) "
                    "{ clientMutationId } }' -F 'i[title]=t' -F 'i[id]=X'",
                    "gh api -X PATCH repos/o/r/issues/5 -f 'i[state]=x' -f title=t",
                    "gh api graphql -f query='﻿mutation { updateIssue(input: {id: \"X\", "
                    "title : \"t\"}) { clientMutationId } }'",
                    "gh api graphql -f query='query﻿{ viewer { login } }'"):
            with self.subTest(cmd=cmd):
                self.assertEqual(gate.decide(cmd, make_repo(), TERMS), [])

    def test_normalise_query(self):
        for raw, want in (("a﻿b​c⁠d", "a b c d"),
                          ("a b c\td", "a b c d"),
                          ("a # c\n\r b", "a # c\n\r b"),
                          ("plain query", "plain query")):
            with self.subTest(raw=raw):
                self.assertEqual(gate._normalise_query(raw), want)

    def test_comment_and_edit_are_not_refused_as_closes(self):
        for cmd in ("gh issue edit 5 --title t",
                    "gh issue comment 5 --body \"<!-- baton:developer:status -->\nhi\""):
            with self.subTest(cmd=cmd):
                self.assertFalse(any("closing or reopening" in p
                                     for p in gate.decide(cmd, make_repo(), TERMS)))

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
