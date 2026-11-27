import json

from .. import identity


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
