import json
import urllib.parse


class FakeRunner:
    """Returns the reply whose key is a prefix of the joined command line."""

    def __init__(self, replies=None):
        self.replies = replies or {}
        self.calls = []
        self.inputs = []
        self.envs = []

    def __call__(self, argv, cwd=None, input=None, env=None):
        self.calls.append(argv)
        self.inputs.append(input)
        self.envs.append(env)
        line = " ".join(argv)
        for prefix in sorted(self.replies, key=len, reverse=True):
            if line.startswith(prefix):
                return self.replies[prefix]
        return ""

    def lines(self):
        return [" ".join(call) for call in self.calls]


class FakeTransport:
    """Matches "<METHOD> <path>?<query>" against the longest key prefix."""

    def __init__(self, replies=None):
        self.replies = replies or {}
        self.calls = []

    def __call__(self, method, url, headers, data):
        self.calls.append((method, url, headers, data))
        line = self._line(method, url)
        for prefix in sorted(self.replies, key=len, reverse=True):
            if line.startswith(prefix):
                status, body = self.replies[prefix]
                break
        else:
            status, body = 404, {"message": "not found"}
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        return status, {}, body.encode()

    @staticmethod
    def _line(method, url):
        parts = urllib.parse.urlsplit(url)
        return f"{method} {parts.path}" + (f"?{parts.query}" if parts.query else "")

    def lines(self):
        return [self._line(method, url) for method, url, _, _ in self.calls]
