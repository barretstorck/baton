import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from baton import comment
from baton import triage
from baton import config
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

    def label(self, name):
        return name

    def status_labels(self):
        return ["status:refinement", "status:in-dev"]


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


def apply_decision(decision, replies=None, maintainers=("Boss",), runner=None):
    runner = runner or FakeRunner(replies if replies is not None
                                  else {"gh issue view 5": issue_json()})
    line = triage.apply(FakeCfg(), runner, 5, triage.parse_decision(json.dumps(decision)),
                        list(maintainers), [], "o", "r")
    return runner, line


class ApplyTests(unittest.TestCase):
    def index(self, lines, prefix):
        return next(i for i, c in enumerate(lines) if c.startswith(prefix))

    def test_spam(self):
        runner, line = apply_decision(valid("spam"))
        lines = runner.lines()
        self.assertLess(self.index(lines, "gh issue comment 5"),
                        self.index(lines, "gh issue close 5"))
        self.assertIn("gh issue edit 5 --add-label closed:spam", lines)
        self.assertTrue(any(c.startswith("gh issue lock 5") for c in lines))
        self.assertIn("spam", line)

    def test_duplicate(self):
        runner, _ = apply_decision(valid("duplicate"),
                                   {"gh issue view 5": issue_json(),
                                    "gh issue view 3": '{"number": 3}'})
        lines = runner.lines()
        self.assertTrue(any(c.startswith("gh issue close 5") and "--duplicate-of 3" in c
                            for c in lines))
        self.assertTrue(any(c.startswith("gh issue comment 3") for c in lines))

    def test_support_posts_comment_first(self):
        runner, _ = apply_decision(valid("support"))
        lines = runner.lines()
        comments = [i for i, c in enumerate(lines) if c.startswith("gh issue comment 5")]
        self.assertEqual(len(comments), 2)
        self.assertLess(comments[1], self.index(lines, "gh issue close 5"))
        self.assertIn("gh issue edit 5 --add-label closed:support", lines)

    def test_unclear_asks_and_never_closes(self):
        runner, _ = apply_decision(valid("unclear"))
        lines = runner.lines()
        self.assertLess(self.index(lines, "gh issue comment 5"),
                        self.index(lines, "gh issue edit 5 --add-label awaiting-feedback"))
        self.assertFalse([c for c in lines if c.startswith("gh issue close")])

    def test_unsure_notifies_once(self):
        runner, _ = apply_decision(valid("unsure"))
        self.assertEqual(len([c for c in runner.lines()
                              if c.startswith("gh issue comment 5")]), 1)
        marked = json.dumps({"comments": [{"body": "<!-- baton:notify:triage-unsure -->"}]})
        _, again = apply_decision(valid("unsure"),
                                  {"gh issue view 5": issue_json(),
                                   "gh issue view 5 --json comments": marked})
        self.assertIn("already notified", again)

    def test_refused_decision_writes_nothing(self):
        runner = FakeRunner({"gh issue view 5": issue_json(author="boss")})
        with self.assertRaises(triage.TriageError):
            apply_decision(valid("spam"), runner=runner)
        self.assertFalse([c for c in runner.lines() if c.startswith(WRITES)])

    def real(self, decision, author="outsider", labels=()):
        bodies = []

        class Reading(FakeRunner):
            def __call__(inner, argv, cwd=None, input=None):
                if "--body-file" in argv:
                    with open(argv[argv.index("--body-file") + 1]) as handle:
                        bodies.append((handle.name, handle.read()))
                return super().__call__(argv, cwd, input)

        view = json.loads(issue_json(author=author))
        view["labels"] = [{"name": n} for n in labels]
        view["body"] = "Intro\n\n## Triage summary\nold\n\n## Other\nkeep\n"
        raw = json.dumps(view)
        labels_reply = json.dumps({"labels": [{"name": "type:bug"}]})
        runner = Reading({"gh issue view 5": raw,
                          "gh issue view 5 --json labels": labels_reply})
        _, line = apply_decision(decision, runner=runner)
        return runner, bodies, line

    def test_bug_order_and_summary(self):
        data = dict(valid("bug"), priority=2, severity=3)
        runner, bodies, line = self.real(data, labels=["type:feature"])
        lines = runner.lines()
        edit = self.index(lines, "gh issue edit 5 --body-file")
        typ = self.index(lines, "gh issue edit 5 --add-label type:bug")
        prio = self.index(lines, "gh issue edit 5 --add-label priority:2")
        sev = self.index(lines, "gh issue edit 5 --add-label severity:3")
        status = self.index(lines, "gh issue edit 5 --add-label status:refinement")
        self.assertTrue(edit < typ < prio < sev < status)
        self.assertIn("--remove-label type:feature", lines[typ])
        text = bodies[0][1]
        self.assertIn("Origin: outside (@outsider)", text)
        self.assertNotIn("old", text)
        self.assertIn("## Other\nkeep", text)
        self.assertFalse([c for c in lines if c.startswith("gh issue close")])
        self.assertIn("bug", line)
        import os
        self.assertFalse(os.path.exists(bodies[0][0]))

    def test_maintainer_has_no_origin(self):
        _, bodies, _ = self.real(valid("feature"), author="boss")
        self.assertNotIn("Origin:", bodies[0][1])

    def test_feature_without_priority(self):
        runner, _, _ = self.real(valid("feature"))
        self.assertFalse([c for c in runner.lines() if "priority:" in c or "severity:" in c])


class CliTests(unittest.TestCase):
    def drive(self, args, text=None):
        import io
        import os
        import tempfile
        from unittest import mock
        from baton import cli
        calls = []

        def runner(cmd, *a, **k):
            calls.append(cmd)
            if cmd[:3] == ["gh", "issue", "view"]:
                return issue_json()
            if cmd[:3] == ["gh", "repo", "view"]:
                return '{"nameWithOwner": "o/r"}'
            return ""
        cfg = mock.MagicMock()
        cfg.maintainers.return_value = ["Boss"]
        cfg.label.side_effect = lambda n: n
        cfg.data = {"privacy_allow": []}
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            if text is not None:
                path = os.path.join(tmp, "d.json")
                with open(path, "w") as handle:
                    handle.write(text)
                args = [path if a == "FILE" else a for a in args]
            with mock.patch.object(cli, "_require_config", return_value=cfg), \
                    mock.patch.object(cli, "_owner", return_value="Boss"), \
                    mock.patch.object(cli.run, "run", runner), \
                    mock.patch.object(cli.scan, "load_terms", return_value=[]), \
                    mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
                code = cli.main(args)
        return code, out.getvalue(), err.getvalue(), calls

    def writes(self, calls):
        return [c for c in calls if c[:3] not in (["gh", "issue", "view"],
                                                  ["gh", "repo", "view"])]

    def test_valid_spam(self):
        code, out, _, calls = self.drive(["triage", "apply", "5", "FILE"],
                                         json.dumps(valid("spam")))
        self.assertEqual(code, 0)
        self.assertIn("spam", out)
        self.assertTrue(any(c[:3] == ["gh", "issue", "close"] for c in calls))

    def test_invalid_decision_writes_nothing(self):
        code, _, err, calls = self.drive(["triage", "apply", "5", "FILE"],
                                         json.dumps({"category": "bogus"}))
        self.assertEqual(code, 1)
        self.assertIn("baton:", err)
        self.assertFalse(self.writes(calls))

    def test_wrong_arguments(self):
        for args, text in ((["triage"], None), (["triage", "apply", "x", "FILE"], "{}"),
                           (["triage", "apply", "5", "/no/such/file"], None),
                           (["triage", "apply", "5"], None)):
            code, _, err, calls = self.drive(args, text)
            self.assertEqual(code, 64, args)
            self.assertIn("usage: baton triage apply <issue> <decision.json>", err)
            self.assertFalse(calls)


NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


def stamp(**ago):
    return (NOW - timedelta(**ago)).isoformat()


def item(number, ago=None, labels=(), state="OPEN", author="out", comments=(), **kw):
    return {"number": number, "state": state, "createdAt": stamp(**(ago or {"hours": 1})),
            "labels": [{"name": n} for n in labels], "author": {"login": author},
            "comments": [{"createdAt": stamp(**c)} for c in comments]}


def make_cfg(**triage_keys):
    cfg = config.Config(root=None, data=copy.deepcopy(config.DEFAULTS))
    cfg.data["triage"].update(triage_keys)
    return cfg


class SelectionTests(unittest.TestCase):
    def test_resolve_since_writes_once(self):
        with tempfile.TemporaryDirectory() as home:
            cfg = make_cfg()
            first = triage.resolve_since(cfg, "o", "r", NOW, home)
            self.assertEqual(first, (NOW, True))
            self.assertTrue(triage.since_path("o", "r", home).exists())
            later = NOW + timedelta(hours=3)
            self.assertEqual(triage.resolve_since(cfg, "o", "r", later, home), (NOW, False))

    def test_brief_wins_over_file(self):
        with tempfile.TemporaryDirectory() as home:
            triage.resolve_since(make_cfg(), "o", "r", NOW, home)
            cfg = make_cfg(since="2026-10-01T00:00:00Z")
            got, wrote = triage.resolve_since(cfg, "o", "r", NOW, home)
            self.assertEqual(got, datetime(2026, 10, 1, tzinfo=timezone.utc))
            self.assertFalse(wrote)

    def test_pending_filters_and_sorts(self):
        cfg = make_cfg()
        since = NOW - timedelta(days=2)
        issues = [item(9, {"hours": 1}), item(3, {"hours": 5}), item(4, {"days": 3}),
                  item(5, state="CLOSED"), item(6, labels=["status:in-dev"]),
                  item(7, labels=["closed:spam"]), item(8, labels=["closed:duplicate"]),
                  item(10, labels=["closed:support"]), item(11, labels=["closed:wont-do"]),
                  item(12, labels=["awaiting-feedback"]),
                  item(13, labels=["needs-maintainer"]), item(14, labels=["bug"])]
        got = [i["number"] for i in triage.pending(issues, cfg, since)]
        self.assertEqual(got, [3, 9, 14])

    def test_split_batch(self):
        issues = [item(n) for n in range(12)]
        batch, waiting = triage.split_batch(issues, 10)
        self.assertEqual([len(batch), len(waiting)], [10, 2])
        self.assertEqual(waiting[0]["number"], 10)

    def test_overdue(self):
        self.assertIsNone(triage.overdue([item(1, {"hours": 23})], NOW))
        self.assertEqual(triage.overdue([item(1, {"hours": 23}), item(2, {"hours": 25})],
                                        NOW)["number"], 2)
        self.assertIsNone(triage.overdue([], NOW))

    def test_stale(self):
        cfg = make_cfg()
        label = ["awaiting-feedback"]
        issues = [item(1, {"days": 13}, label), item(2, {"days": 15}, label),
                  item(3, {"days": 30}, label, comments=[{"days": 5}]),
                  item(4, {"days": 30}, label, comments=[{"days": 20}, {"days": 16}]),
                  item(5, {"days": 20}, label, author="boss"),
                  item(6, {"days": 20}), item(7, {"days": 20}, label, state="CLOSED")]
        self.assertEqual(triage.stale(issues, cfg, NOW, 14, ["boss"]), [2, 4])


def bug(number, ago=None, labels=("type:bug", "status:refinement"), body="## Triage summary\nx",
        handoffs=(), **kw):
    data = item(number, ago or {"days": 1}, labels, **kw)
    data["body"] = body
    data["comments"] = [{"createdAt": stamp(**h), "body": comment.compose("orchestrator", "status",
                                                           triage.REPRO_MARK + "\nhanded")}
                        for h in handoffs]
    return data


class ReproduceTests(unittest.TestCase):
    def test_handoffs_window(self):
        issues = [bug(1, handoffs=[{"hours": 23}, {"hours": 25}]),
                  bug(2, handoffs=[{"hours": 1}], state="CLOSED")]
        self.assertEqual(triage.handoffs_last_day(issues, NOW), 1)

    def test_other_comments_do_not_count(self):
        issue = bug(1)
        issue["comments"] = [{"createdAt": stamp(hours=1), "body": comment.compose("orchestrator", "status", "hi")}]
        self.assertEqual(triage.handoffs_last_day([issue], NOW), 0)

    def test_composed_handoff_counts_and_excludes(self):
        issue = bug(1)
        issue["comments"] = [{"createdAt": stamp(hours=1), "body": comment.compose(
            "orchestrator", "status", triage.REPRO_MARK + "\nhanded")}]
        other = bug(2)
        self.assertEqual(triage.handoffs_last_day([issue], NOW), 1)
        self.assertEqual(triage.reproduce_candidates([issue, other], make_cfg(), NOW, 5), [2])

    def test_limit_minus_recent(self):
        issues = [bug(1, handoffs=[{"hours": 1}]), bug(2, handoffs=[{"hours": 2}]),
                  bug(3, {"days": 5}), bug(4, {"days": 4}), bug(5, {"days": 3})]
        self.assertEqual(triage.reproduce_candidates(issues, make_cfg(), NOW, 3), [3])
        self.assertEqual(triage.reproduce_candidates(issues, make_cfg(), NOW, 1), [])

    def test_limit_zero(self):
        self.assertEqual(triage.reproduce_candidates([bug(1)], make_cfg(), NOW, 0), [])

    def test_exclusions_and_order(self):
        issues = [bug(1, labels=("type:feature", "status:refinement")),
                  bug(2, body="no summary"),
                  bug(3, handoffs=[{"days": 3}]),
                  bug(4, labels=("type:bug", "status:refinement", "awaiting-feedback")),
                  bug(5, labels=("type:bug", "status:ready")),
                  bug(6, {"days": 2}), bug(7, {"days": 6}), bug(8, state="CLOSED")]
        self.assertEqual(triage.reproduce_candidates(issues, make_cfg(), NOW, 5), [7, 6])


class QueueCommandTests(unittest.TestCase):
    def drive(self, issues, home, args=("triage", "queue"), since=""):
        import io
        import os
        from unittest import mock
        from baton import cli
        cfg = mock.MagicMock()
        cfg.label.side_effect = lambda n: n
        cfg.status_labels.return_value = ["status:in-dev"]
        cfg.data = {"triage": {"since": since, "max_per_run": 10, "stale_days": 14,
                                 "max_reproductions_per_day": 3}}
        runner = FakeRunner({"gh repo view": '{"nameWithOwner": "o/r"}',
                             "gh issue list": json.dumps(issues)})
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"BATON_HOME": home}), \
                mock.patch.object(cli, "_require_config", return_value=cfg), \
                mock.patch.object(cli, "_maintainers", return_value=["Boss"]), \
                mock.patch.object(cli.run, "run", runner), \
                mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            code = cli.main(list(args))
        return code, out.getvalue()

    def test_first_run_records_and_triages_nothing(self):
        import os
        issue = {"number": 1, "state": "OPEN", "labels": [], "comments": [],
                 "author": {"login": "x"}, "createdAt": "2020-01-01T00:00:00Z"}
        with tempfile.TemporaryDirectory() as home:
            code, text = self.drive([issue], home)
            data = json.loads(text)
            self.assertEqual(code, 0)
            self.assertTrue(data["first_run"])
            self.assertEqual(data["batch"], [])
            self.assertTrue(os.path.exists(triage.since_path("o", "r", home)))
            _, text = self.drive([issue], home)
            self.assertFalse(json.loads(text)["first_run"])
            self.assertEqual(json.loads(text)["reproduce"], [])

    def test_later_run_caps_batch(self):
        old = datetime.now(timezone.utc) - timedelta(days=3)
        issues = [{"number": n, "state": "OPEN", "labels": [], "comments": [],
                   "author": {"login": "x"},
                   "createdAt": (old + timedelta(minutes=n)).isoformat()} for n in range(1, 13)]
        issues.append({"number": 50, "state": "OPEN", "labels": [{"name": "awaiting-feedback"}],
                       "comments": [], "author": {"login": "x"},
                       "createdAt": "2020-01-01T00:00:00Z"})
        with tempfile.TemporaryDirectory() as home:
            code, text = self.drive(issues, home, since="2020-06-01T00:00:00Z")
        data = json.loads(text)
        self.assertEqual(code, 0)
        self.assertFalse(data["first_run"])
        self.assertEqual(data["batch"], list(range(1, 11)))
        self.assertEqual(data["waiting"], [11, 12])
        self.assertEqual(data["overdue"], 11)
        self.assertEqual(data["stale"], [50])

    def test_other_shape_keeps_usage(self):
        with tempfile.TemporaryDirectory() as home:
            code, _ = self.drive([], home, args=("triage", "queue", "x"))
        self.assertEqual(code, 64)
