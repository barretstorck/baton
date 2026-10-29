import copy
import json
import pathlib
import unittest
from baton import config, labels
from tests.fakes import FakeRunner


def make_config(overrides=None):
    data = copy.deepcopy(config.DEFAULTS)
    data["labels"]["map"].update(overrides or {})
    return config.Config(root=pathlib.Path("."), data=data)


def label_list(names):
    return json.dumps([{"name": name} for name in names])


class LabelsTest(unittest.TestCase):
    def test_missing_uses_mapped_names(self):
        cfg = make_config({"status:in-dev": "In Progress"})
        runner = FakeRunner({"gh label list": label_list(["In Progress", "status:ready"])})
        missing = labels.missing(cfg, runner)
        self.assertNotIn("In Progress", missing)
        self.assertNotIn("status:in-dev", missing)
        self.assertIn("status:blocked", missing)
        total = len(cfg.data["labels"]["map"])
        self.assertEqual(len(missing), total - 2)

    def test_missing_includes_priority_and_severity(self):
        cfg = make_config({"priority:1": "P1"})
        missing = labels.missing(cfg, FakeRunner({"gh label list": label_list([])}))
        for name in config.PRIORITIES[1:] + config.SEVERITIES:
            self.assertIn(name, missing)
        self.assertIn("P1", missing)
        self.assertNotIn("priority:1", missing)

    def test_ensure_creates_missing_labels(self):
        cfg = make_config()
        all_but_one = [n for n in cfg.data["labels"]["map"].values() if n != "stretch"]
        runner = FakeRunner({"gh label list": label_list(all_but_one)})
        created, still = labels.ensure(cfg, runner, create=True)
        self.assertEqual((created, still), (["stretch"], []))
        self.assertTrue(any(line.startswith("gh label create stretch") for line in runner.lines()))

    def test_ensure_without_create_reports_only(self):
        cfg = make_config()
        runner = FakeRunner({"gh label list": label_list([])})
        created, still = labels.ensure(cfg, runner, create=False)
        self.assertEqual(created, [])
        self.assertEqual(len(still), len(cfg.data["labels"]["map"]))
        self.assertFalse(any("label create" in line for line in runner.lines()))

    def test_set_status_swaps_only_present_status_labels(self):
        cfg = make_config({"status:in-dev": "In Progress"})
        issue = json.dumps({"labels": [{"name": "In Progress"}, {"name": "deliverable"}]})
        runner = FakeRunner({"gh issue view 7": issue})
        labels.set_status(cfg, runner, 7, "status:in-review")
        self.assertEqual(runner.calls[-1], [
            "gh", "issue", "edit", "7", "--add-label", "status:in-review",
            "--remove-label", "In Progress"])

    def test_spike_label_description_names_no_pull_request(self):
        description = labels._STYLE["type:spike"][1]
        self.assertNotIn("pull request", description.lower())

    def test_set_status_rejects_unknown_status(self):
        with self.assertRaises(ValueError):
            labels.set_status(make_config(), FakeRunner(), 7, "status:nope")


class SetGroupTest(unittest.TestCase):
    def runner(self, names):
        return FakeRunner({"gh issue view 7": json.dumps(
            {"labels": [{"name": n} for n in names]})})

    def test_priority_swaps_only_present_priority_label(self):
        cfg = make_config({"priority:3": "P3"})
        runner = self.runner(["P3", "priority:1", "deliverable", "severity:2"])
        labels.set_group(cfg, runner, 7, "priority", 2)
        self.assertEqual(runner.calls[-1], [
            "gh", "issue", "edit", "7", "--add-label", "priority:2",
            "--remove-label", "priority:1", "--remove-label", "P3"])

    def test_severity_on_bug_swaps(self):
        runner = self.runner(["type:bug", "severity:4", "priority:1"])
        labels.set_group(make_config(), runner, 7, "severity", 1)
        self.assertEqual(runner.calls[-1], [
            "gh", "issue", "edit", "7", "--add-label", "severity:1",
            "--remove-label", "severity:4"])

    def test_severity_on_non_bug_raises_without_edit(self):
        runner = self.runner(["type:feature"])
        with self.assertRaises(ValueError):
            labels.set_group(make_config(), runner, 7, "severity", 2)
        self.assertFalse(any(c[:3] == ["gh", "issue", "edit"] for c in runner.calls))

    def test_bad_value_or_group_raises(self):
        for group, value in (("priority", 5), ("priority", 0), ("status", 1)):
            with self.assertRaises(ValueError):
                labels.set_group(make_config(), self.runner([]), 7, group, value)
