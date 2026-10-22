class FakeRunner:
    """Returns the reply whose key is a prefix of the joined command line."""

    def __init__(self, replies=None):
        self.replies = replies or {}
        self.calls = []
        self.inputs = []

    def __call__(self, argv, cwd=None, input=None):
        self.calls.append(argv)
        self.inputs.append(input)
        line = " ".join(argv)
        for prefix in sorted(self.replies, key=len, reverse=True):
            if line.startswith(prefix):
                return self.replies[prefix]
        return ""

    def lines(self):
        return [" ".join(call) for call in self.calls]
