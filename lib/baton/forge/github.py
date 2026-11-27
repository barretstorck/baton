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
