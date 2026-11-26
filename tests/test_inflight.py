import unittest
from datetime import datetime, timedelta, timezone

from baton import inflight

OWNER = {"login": "owner"}
LOGINS = ["owner"]
TASKS = [{"id": "1", "done": True}, {"id": "2", "done": False}]


def note(body, author=OWNER, at="2026-01-01T10:00:00Z", role="developer", url="u"):
    return {"author": author, "createdAt": at, "url": url,
            "body": f"<!-- baton:{role}:status -->\n**Baton {role}: status**\n\n{body}"}


class CurrentTest(unittest.TestCase):
    def test_open_record_is_returned(self):
        got = inflight.current([note("In flight: task 2 (run abcd1234)", url="x")], TASKS, LOGINS)
        self.assertEqual(got, {"task": "2", "run": "abcd1234", "since": "2026-01-01T10:00:00Z", "url": "x"})

    def test_ticked_task_gives_none(self):
        self.assertIsNone(inflight.current([note("In flight: task 1 (run abcd1234)")], TASKS, LOGINS))

    def test_release_same_run_closes_other_run_does_not(self):
        start = note("In flight: task 2 (run abcd1234)")
        self.assertIsNone(inflight.current([start, note("Released: task 2 (run abcd1234)")], TASKS, LOGINS))
        got = inflight.current([start, note("Released: task 2 (run 11111111)")], TASKS, LOGINS)
        self.assertEqual(got["run"], "abcd1234")

    def test_later_record_wins(self):
        got = inflight.current([note("In flight: task 2 (run aaaaaaaa)"),
                                note("In flight: task 2 (run bbbbbbbb)")], TASKS, LOGINS)
        self.assertEqual(got["run"], "bbbbbbbb")

    def test_ignored_comments(self):
        other = note("In flight: task 2 (run abcd1234)", author={"login": "stranger"})
        orch = note("In flight: task 2 (run abcd1234)", role="orchestrator")
        self.assertIsNone(inflight.current([other, orch], TASKS, LOGINS))

    def test_unknown_task_gives_none(self):
        self.assertIsNone(inflight.current([note("In flight: task 9 (run abcd1234)")], TASKS, LOGINS))


class IsStaleTest(unittest.TestCase):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    def rec(self, ago):
        return {"since": (self.now - ago).isoformat()}

    def test_old_is_stale(self):
        self.assertTrue(inflight.is_stale(self.rec(timedelta(hours=3)), None, self.now, 120))

    def test_recent_push_is_fresh(self):
        push = (self.now - timedelta(minutes=10)).isoformat()
        self.assertFalse(inflight.is_stale(self.rec(timedelta(hours=3)), push, self.now, 120))

    def test_recent_record_is_fresh(self):
        self.assertFalse(inflight.is_stale(self.rec(timedelta(minutes=30)), None, self.now, 120))


class BodiesTest(unittest.TestCase):
    def test_round_trip(self):
        run = inflight.new_run()
        self.assertRegex(run, r"^[0-9a-f]{8}$")
        self.assertEqual(inflight.current([note(inflight.start_body("2", run))], TASKS, LOGINS)["run"], run)
        both = [note(inflight.start_body("2", run)), note(inflight.release_body("2", run))]
        self.assertIsNone(inflight.current(both, TASKS, LOGINS))
        self.assertTrue(inflight.start_body("2", run).startswith("In flight: task 2 (run "))


if __name__ == "__main__":
    unittest.main()
