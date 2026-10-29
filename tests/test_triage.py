import copy
import json
import unittest

from baton import triage
from tests.fakes import FakeRunner


def valid(category):
    base = {"category": category}
    if category == "duplicate":
        base["duplicate_of"] = 3
    if category in ("support", "unclear"):
        base["comment"] = "Please say more."
    if category in triage.REAL:
        base["summary"] = {"symptom": "It crashes", "expected": "No crash",
                           "actual": "Crash", "area": "cli", "version": "0.5.1",
                           "type": category}
    return base


def refused(testcase, base_category, fragment, **changes):
    data = copy.deepcopy(valid(base_category))
    for key, value in changes.items():
        if value is KeyError:
            data.pop(key, None)
        else:
            data[key] = value
    with testcase.assertRaises(triage.TriageError) as ctx:
        triage.parse_decision(json.dumps(data))
    testcase.assertIn(fragment, str(ctx.exception))


class ParseTests(unittest.TestCase):
    def test_valid_each_category(self):
        for category in triage.CATEGORIES:
            got = triage.parse_decision(json.dumps(valid(category)))
            self.assertEqual(got["category"], category)
        self.assertIsNone(triage.parse_decision('{"category": "spam"}')["comment"])

    def test_not_object(self):
        for text in ("[]", "nope", '"x"'):
            with self.assertRaises(triage.TriageError):
                triage.parse_decision(text)

    def test_unknown_key(self):
        refused(self, "spam", "label", label="x")

    def test_category(self):
        refused(self, "spam", "category", category=KeyError)
        refused(self, "spam", "category", category="other")

    def test_integers(self):
        refused(self, "duplicate", "duplicate_of", duplicate_of="3")
        refused(self, "duplicate", "duplicate_of", duplicate_of=True)
        refused(self, "bug", "priority", priority=True)
        refused(self, "bug", "priority", priority=0)
        refused(self, "bug", "priority", priority=5)
        refused(self, "bug", "severity", severity=5)
        refused(self, "bug", "severity", severity=1.5)

    def test_duplicate_of(self):
        refused(self, "duplicate", "duplicate_of", duplicate_of=KeyError)
        refused(self, "spam", "duplicate_of", duplicate_of=3)

    def test_severity_and_priority(self):
        refused(self, "feature", "severity", severity=2)
        refused(self, "spam", "priority", priority=2)
        refused(self, "support", "priority", priority=2)

    def test_comment(self):
        refused(self, "support", "comment", comment=KeyError)
        refused(self, "unclear", "comment", comment="  ")
        refused(self, "support", "2000", comment="x" * 2001)
        refused(self, "spam", "comment", comment="hi")
        refused(self, "bug", "comment", comment="hi")
        refused(self, "unsure", "2000", comment="x" * 2001)
        refused(self, "unsure", "string", comment=5)

    def test_summary_shape(self):
        refused(self, "bug", "summary", summary=KeyError)
        refused(self, "spam", "summary", summary=valid("bug")["summary"])
        refused(self, "bug", "keys", summary={"symptom": "a"})
        extra = dict(valid("bug")["summary"], extra="x")
        refused(self, "bug", "keys", summary=extra)
        refused(self, "bug", "type",
                summary=dict(valid("bug")["summary"], type="feature"))

    def test_summary_fields(self):
        base = valid("bug")["summary"]
        for field, limit in (("symptom", 300), ("area", 80), ("version", 40)):
            refused(self, "bug", field, summary=dict(base, **{field: "x" * (limit + 1)}))
        refused(self, "bug", "symptom", summary=dict(base, symptom=""))
        refused(self, "bug", "symptom", summary=dict(base, symptom=5))
        refused(self, "bug", "single line", summary=dict(base, symptom="a\nb"))

    def test_summary_content(self):
        base = valid("bug")["summary"]
        for bad in ("a `b`", "```x", "see http://x", "https://x", "www.x.com",
                    "[a](b)", "$ ls", "#!/bin/sh", "curl x", "Bash -c x",
                    "git push", "npm i", "python3 x"):
            refused(self, "bug", "summary.", summary=dict(base, actual=bad))
        ok = dict(valid("bug"), summary=dict(base, actual="a git hook fails"))
        triage.parse_decision(json.dumps(ok))


if __name__ == "__main__":
    unittest.main()


class FakeCfg:
    data = {"privacy_allow": []}


class CheckCommentTests(unittest.TestCase):
    def check(self, text, terms=()):
        triage.check_comment(FakeCfg(), text, list(terms), "me", "repo")

    def test_private_term_refused(self):
        with self.assertRaises(triage.TriageError) as ctx:
            self.check("see secret-host", ["secret-host"])
        self.assertTrue(str(ctx.exception).startswith("privacy scan found:"))

    def test_other_site_refused(self):
        with self.assertRaises(triage.TriageError) as ctx:
            self.check("see https://example.com/x")
        self.assertIn("https://example.com/x", str(ctx.exception))

    def test_repository_links_pass(self):
        self.check("https://github.com/me/repo")
        self.check("See https://github.com/Me/repo/issues/3 and https://github.com/me/repo#readme.")
        self.check("https://me.github.io/repo/docs")

    def test_lookalike_refused(self):
        for url in ("https://github.com/me/repo-fork", "https://me.github.io/repo2"):
            with self.assertRaises(triage.TriageError):
                self.check(url)


class SummaryTests(unittest.TestCase):
    SUMMARY = valid("bug")["summary"]

    def test_maintainer_has_no_origin(self):
        text = triage.render_summary(self.SUMMARY, "Boss", ["boss"])
        self.assertNotIn("Origin:", text)
        self.assertTrue(text.startswith("## Triage summary\n"))
        self.assertIn("Symptom: It crashes\nExpected: No crash\nActual: Crash\n"
                      "Area: cli\nVersion: 0.5.1\nType: bug", text)

    def test_outsider_ends_with_origin(self):
        text = triage.render_summary(self.SUMMARY, "visitor", ["boss"])
        self.assertTrue(text.endswith("Origin: outside (@visitor)"))

    def test_with_summary_appends(self):
        out = triage.with_summary("## Context\n\nText\n", "## Triage summary\n\nA: b")
        self.assertEqual(out, "## Context\n\nText\n\n## Triage summary\n\nA: b\n")

    def test_with_summary_replaces_in_middle(self):
        body = "## Context\n\nText\n\n## Triage summary\n\nOld: x\n\n## Pointers\n\nP\n"
        out = triage.with_summary(body, "## Triage summary\n\nNew: y")
        self.assertEqual(out, "## Context\n\nText\n\n## Triage summary\n\nNew: y\n\n## Pointers\n\nP\n")

    def test_with_summary_replaces_last(self):
        out = triage.with_summary("Intro\n\n## Triage summary\n\nOld", "## Triage summary\n\nNew")
        self.assertEqual(out, "Intro\n\n## Triage summary\n\nNew\n")


WRITES = ("gh issue edit", "gh issue close", "gh issue comment", "gh issue lock")


def issue_json(author="outsider", state="OPEN"):
    return json.dumps({"number": 5, "author": {"login": author}, "body": "b",
                       "labels": [], "state": state})


def run_check(testcase, decision, issue, replies=None, maintainers=("Boss",)):
    runner = FakeRunner(replies or {})
    triage.check(FakeCfg(), runner, 5, triage.parse_decision(json.dumps(decision)),
                 issue, list(maintainers), [], "o", "r")
    testcase.assertFalse([c for c in runner.lines() if c.startswith(WRITES)])
    return runner


class CheckTests(unittest.TestCase):
    def issue(self, **kw):
        return json.loads(issue_json(**kw))

    def refuse(self, decision, issue, fragment, replies=None):
        with self.assertRaises(triage.TriageError) as ctx:
            run_check(self, decision, issue, replies)
        self.assertIn(fragment, str(ctx.exception))

    def test_closed_issue(self):
        self.refuse(valid("spam"), self.issue(state="CLOSED"), "closed")

    def test_maintainer_issue_not_closed(self):
        for category in ("spam", "duplicate", "support"):
            self.refuse(valid(category), self.issue(author="boss"),
                        "never closes an issue written by a maintainer",
                        {"gh issue view 3": '{"number": 3}'})

    def test_maintainer_issue_accepted(self):
        for category in ("unclear", "bug", "unsure"):
            run_check(self, valid(category), self.issue(author="boss"))

    def test_duplicate_of_self(self):
        data = valid("duplicate")
        data["duplicate_of"] = 5
        self.refuse(data, self.issue(), "itself")

    def test_duplicate_missing(self):
        self.refuse(valid("duplicate"), self.issue(), "does not exist")

    def test_duplicate_found(self):
        run_check(self, valid("duplicate"), self.issue(),
                  {"gh issue view 3": '{"number": 3}'})

    def test_comment_refused(self):
        data = valid("support")
        data["comment"] = "see https://example.com/x"
        self.refuse(data, self.issue(), "outside the repository")

    def test_load_issue(self):
        runner = FakeRunner({"gh issue view 5": issue_json()})
        self.assertEqual(triage.load_issue(runner, 5)["number"], 5)
        with self.assertRaises(triage.TriageError):
            triage.load_issue(FakeRunner(), 5)
