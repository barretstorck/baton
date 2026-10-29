"""At tier 1 every write helper sends the routed App's token; reads and tier 0 send none."""
import copy
import json
import pathlib
import unittest
from unittest import mock

from baton import comment, config, identity, labels, publish, review, state
from tests.fakes import FakeRunner
from tests.test_review import OWNER, changes_with_tasks, pr_runner
from tests.test_triage import apply_decision, valid


def cfg():
    return config.Config(root=pathlib.Path("."), data=copy.deepcopy(config.DEFAULTS))


class Tier1Test(unittest.TestCase):
    def setUp(self):
        ids = identity.Identities(identity.App(1, 10, "/k/b.pem", "baton[bot]"),
                                  identity.App(2, 20, "/k/r.pem", "baton-reviewer[bot]"))
        identity.use(lambda: ids)
        patcher = mock.patch.object(identity, "token", lambda app, *a: (
            "tok-reviewer" if app.app_id == 2 else "tok-baton"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(identity.use, None)

    def tokens(self, runner):
        return [e and e["GH_TOKEN"] for e in runner.envs]

    def test_comment_post_uses_the_role(self):
        for role, want in (("developer", "tok-baton"), ("reviewer", "tok-reviewer")):
            runner = FakeRunner()
            comment.post(cfg(), runner, "issue", 3, role, "status", "Hi.", [])
            self.assertEqual(self.tokens(runner), [want])

    def test_publish_defaults_to_orchestrator(self):
        runner = FakeRunner()
        comment.publish(cfg(), runner, "pr", 3, "text", [])
        self.assertEqual(self.tokens(runner), ["tok-baton"])
        self.assertEqual(runner.envs[0]["GH_PROMPT_DISABLED"], "1")

    def test_publish_module_writes(self):
        runner = FakeRunner()
        publish.ready_pr(runner, 7)
        publish.create_pr(cfg(), runner, "T", "body", True, [])
        publish.edit(cfg(), runner, "issue", 4, [], title="T2")
        publish.create_milestone(cfg(), runner, "M", "d", None, [])
        self.assertEqual(self.tokens(runner), ["tok-baton"] * 4)

    def test_milestone_read_has_no_env(self):
        runner = FakeRunner({"gh api repos": json.dumps([{"title": "M", "number": 2}])})
        publish.edit_milestone(cfg(), runner, "M", [], state="closed")
        self.assertEqual(self.tokens(runner), [None, "tok-baton"])

    def test_labels_write_but_not_read(self):
        runner = FakeRunner({"gh issue view": json.dumps({"labels": []})})
        labels.set_status(cfg(), runner, 7, "status:in-review")
        self.assertEqual(self.tokens(runner), [None, "tok-baton"])

    def test_state_change_uses_its_role(self):
        runner = FakeRunner()
        state.change(cfg(), runner, "reopen", "pr", 7, "reviewer", "Mistake.", [])
        self.assertEqual(self.tokens(runner), ["tok-reviewer", "tok-reviewer"])

    def test_tick_review_is_the_reviewer(self):
        runner = pr_runner([changes_with_tasks()])
        review.tick_review(runner, 9, OWNER, "1.1")
        tokens = self.tokens(runner)
        self.assertEqual(tokens[-1], "tok-reviewer")
        self.assertEqual(tokens[:-1], [None] * (len(tokens) - 1))

    def test_triage_writes_are_orchestrator(self):
        runner, _ = apply_decision(valid("bug"))
        pairs = list(zip(runner.calls, runner.envs))
        written = [e for c, e in pairs if c[:3] != ["gh", "issue", "view"]]
        self.assertTrue(written)
        self.assertTrue(all(e and e["GH_TOKEN"] == "tok-baton" for e in written))
        self.assertTrue(all(e is None for c, e in pairs if c[:3] == ["gh", "issue", "view"]))


class Tier0Test(unittest.TestCase):
    def test_no_env_at_tier_0(self):
        identity.use(None)
        runner = FakeRunner()
        comment.post(cfg(), runner, "issue", 3, "developer", "status", "Hi.", [])
        self.assertEqual(runner.envs, [None])


if __name__ == "__main__":
    unittest.main()


class MainScopeTest(unittest.TestCase):
    def test_main_unregisters_loader(self):
        from baton import cli
        with mock.patch.object(cli, "_repo_name", lambda: "o/r"):
            cli.main(["config"])
        self.assertIsNone(identity._loader)

    def test_repo_lookup_failure_is_identity_error(self):
        from baton import cli, run
        def boom():
            raise run.RunError("gh repo view failed")
        with mock.patch.object(cli, "_repo_name", boom):
            with self.assertRaises(identity.IdentityError):
                cli._load_identity()
