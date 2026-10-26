import copy
import pathlib
import unittest
from datetime import datetime, timezone
from baton import config, questions

OWNER = "octo"
NOW = datetime(2026, 1, 31, tzinfo=timezone.utc)
Q = "<!-- baton:refiner:question -->\n**Baton refiner: question**\n\nWhich?"
DECISION = "<!-- baton:orchestrator:decision -->\n**Baton orchestrator: decision**\n\n> go"


def cfg():
    return config.Config(root=pathlib.Path("."), data=copy.deepcopy(config.DEFAULTS))


def cm(login, body, n, created="2026-01-30T00:00:00Z"):
    return {"author": {"login": login}, "body": body, "url": f"https://example.com/c/{n}",
            "createdAt": created}


def issue(number, *labels, blocked="", state="OPEN", comments=()):
    body = "## Context\nx\n" + (f"Blocked by: {blocked}\n" if blocked else "")
    return {"number": number, "title": f"Issue {number}", "state": state, "body": body,
            "labels": [{"name": n} for n in labels], "comments": list(comments)}


def ask(number, *labels, created="2026-01-30T00:00:00Z", **kw):
    return issue(number, *labels, comments=[cm("bot", Q, number, created)], **kw)


def listed(issues):
    return questions.open_questions(issues, cfg(), OWNER, NOW)


def order(issues):
    return [e["number"] for e in listed(issues)]


class SelectionTests(unittest.TestCase):
    def test_each_kind(self):
        got = {e["number"]: e["kind"] for e in listed([
            ask(1), issue(2, "status:blocked"), issue(3, "needs-maintainer"), issue(4)])}
        self.assertEqual(got, {1: "question", 2: "blocked", 3: "needs-maintainer"})

    def test_question_wins_over_labels_and_fields(self):
        entry = listed([ask(1, "status:blocked", "needs-maintainer")])[0]
        self.assertEqual(entry["kind"], "question")
        self.assertEqual(entry["question_url"], "https://example.com/c/1")
        self.assertEqual(entry["age_days"], 1)

    def test_answered_question_excluded(self):
        answered = issue(1, comments=[cm("bot", Q, 1), cm(OWNER, "do x", 2)])
        decided = issue(2, comments=[cm("bot", Q, 3), cm(OWNER, DECISION, 4)])
        self.assertEqual(listed([answered, decided]), [])

    def test_answered_but_blocked_keeps_blocked_kind_without_age(self):
        item = issue(1, "status:blocked", comments=[cm("bot", Q, 1), cm(OWNER, "ok", 2)])
        entry = listed([item])[0]
        self.assertEqual((entry["kind"], entry["age_days"]), ("blocked", None))

    def test_closed_excluded(self):
        self.assertEqual(listed([ask(1, state="CLOSED")]), [])

    def test_labelled_issue_without_question(self):
        entry = listed([issue(1, "needs-maintainer")])[0]
        self.assertEqual((entry["question_url"], entry["age_days"]), (None, None))

    def test_blocks_only_from_open_issues(self):
        got = listed([ask(1), issue(2, blocked="#1"), issue(3, blocked="#1", state="CLOSED"),
                      issue(4, blocked="none")])
        self.assertEqual(got[0]["blocks"], [2])

    def test_tier_and_repair(self):
        got = {e["number"]: e for e in listed([
            ask(1, "deliverable"), ask(2, "stretch"), ask(3, "broken-default-branch"), ask(4)])}
        self.assertEqual([got[n]["tier"] for n in (1, 2, 3, 4)],
                         ["deliverable", "stretch", None, None])
        self.assertTrue(got[3]["repair"])
        self.assertFalse(got[1]["repair"])


class OrderTests(unittest.TestCase):
    def test_repair_first(self):
        self.assertEqual(order([ask(1, "deliverable"), ask(2, "broken-default-branch"),
                                issue(3, blocked="#1")]), [2, 1])

    def test_more_blocked_first(self):
        items = [ask(1, "deliverable"), ask(2), ask(3), issue(4, blocked="#2, #3"),
                 issue(5, blocked="#3")]
        self.assertEqual(order(items), [3, 2, 1])

    def test_tier_order(self):
        self.assertEqual(order([ask(1), ask(2, "stretch"), ask(3, "deliverable")]), [3, 2, 1])

    def test_oldest_question_first_then_no_question(self):
        items = [issue(1, "status:blocked"), ask(2, created="2026-01-29T00:00:00Z"),
                 ask(3, created="2026-01-20T00:00:00Z")]
        self.assertEqual(order(items), [3, 2, 1])

    def test_number_breaks_ties(self):
        self.assertEqual(order([ask(5), ask(2), ask(9)]), [2, 5, 9])


class CommandTests(unittest.TestCase):
    def run_cmd(self, issues):
        import io
        import json
        from contextlib import redirect_stdout
        from unittest import mock
        from baton import cli
        calls = []

        def fake(cmd, *a, **k):
            calls.append(" ".join(cmd))
            return json.dumps(issues)
        out = io.StringIO()
        with mock.patch.object(cli, "_require_config", return_value=cfg()), \
                mock.patch.object(cli, "_owner", return_value=OWNER), \
                mock.patch.object(cli.run, "run", fake), redirect_stdout(out):
            code = cli.cmd_open_questions([])
        return code, json.loads(out.getvalue()), calls

    def test_prints_sorted_list(self):
        code, found, calls = self.run_cmd([ask(5), ask(2)])
        self.assertEqual(code, 0)
        self.assertEqual([e["number"] for e in found], [2, 5])
        self.assertIn("gh issue list --state open --limit 300", calls[0])

    def test_prints_empty_list(self):
        self.assertEqual(self.run_cmd([])[:2], (0, []))

    def test_arguments_exit_64(self):
        from baton import cli
        self.assertEqual(cli.cmd_open_questions(["x"]), 64)


if __name__ == "__main__":
    unittest.main()
