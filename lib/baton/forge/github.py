import json

from .. import identity, run


class GitHub:
    kind = "github"

    def __init__(self, runner):
        self.runner = runner

    def _read(self, argv, empty=None):
        out = self.runner(argv)
        if not out.strip() and empty is not None:
            return empty
        return json.loads(out)

    def _write(self, argv, role=None):
        if role is None:
            return self.runner(argv)
        return identity.write(self.runner, argv, role)

    def repo_view(self, fields, empty=None):
        return self._read(["gh", "repo", "view", "--json", fields], empty)

    def issue_view(self, number, fields, empty=None):
        return self._read(["gh", "issue", "view", str(number), "--json", fields], empty)

    def pr_view(self, number, fields, empty=None):
        return self._read(["gh", "pr", "view", str(number), "--json", fields], empty)

    def issue_exists(self, number):
        try:
            found = self.runner(["gh", "issue", "view", str(number), "--json", "number"])
        except run.RunError:
            return False
        return bool((found or "").strip())

    def issue_edit_body(self, number, path, role=None):
        return self._write(["gh", "issue", "edit", str(number), "--body-file", path], role)

    def issue_edit_labels(self, number, add, remove=(), role=None):
        argv = ["gh", "issue", "edit", str(number)]
        for name in add:
            argv += ["--add-label", name]
        for name in remove:
            argv += ["--remove-label", name]
        return self._write(argv, role)

    def comment(self, target, number, path, role=None):
        return self._write(["gh", target, "comment", str(number), "--body-file", path], role)

    def change_state(self, target, action, number, role=None):
        return self._write(["gh", target, action, str(number)], role)

    def issue_close(self, number, reason, duplicate_of=None, role=None):
        argv = ["gh", "issue", "close", str(number), "--reason", reason]
        if duplicate_of is not None:
            argv += ["--duplicate-of", str(duplicate_of)]
        return self._write(argv, role)

    def issue_lock(self, number, reason=None, role=None):
        argv = ["gh", "issue", "lock", str(number)]
        if reason is not None:
            argv += ["--reason", reason]
        return self._write(argv, role)

    def label_names(self):
        items = self._read(["gh", "label", "list", "--limit", "500", "--json", "name"], [])
        return {item["name"] for item in items}

    def label_create(self, name, color, description, role=None):
        return self._write(["gh", "label", "create", name, "--color", color,
                            "--description", description], role)
