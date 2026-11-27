import json

from .. import identity, run


EDITS_QUERY = (
    "query($owner:String!,$repo:String!,$number:Int!,$endCursor:String){"
    "repository(owner:$owner,name:$repo){issue(number:$number){"
    "userContentEdits(first:100,after:$endCursor){pageInfo{hasNextPage endCursor} "
    "nodes{editedAt editor{login __typename}}}}}}")
_EDITS_JQ = (".data.repository.issue.userContentEdits.nodes[] | "
             "[.editedAt, (.editor.login // \"\"), (.editor.__typename // \"\")] | @tsv")

TIMELINE_JQ = (
    '.[] | select(.event == "assigned" or .event == "unassigned" or '
    '.event == "review_requested" or .event == "review_request_removed") | '
    '[.event, '
    '(if .event == "assigned" or .event == "unassigned" then (.actor.login // "") '
    'else (.review_requester.login // .actor.login // "") end), '
    '(if .event == "assigned" or .event == "unassigned" then (.assignee.login // "") '
    'else (.requested_reviewer.login // "") end), '
    '(.created_at // "")] | @tsv')

AUTHOR_TYPES_JQ = '.[] | [.node_id, .user.type] | @tsv'


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

    def pr_review(self, number, event, path, role=None):
        return self._write(["gh", "pr", "review", str(number), f"--{event}",
                            "--body-file", path], role)

    def review_urls(self, number, login):
        out = self.runner(["gh", "api", "--paginate",
                           f"repos/{{owner}}/{{repo}}/pulls/{number}/reviews?per_page=100",
                           "--jq", f'.[] | select(.user.login == "{login}") | .html_url'])
        return [line.strip() for line in out.splitlines() if line.strip()]

    def user(self, login):
        return json.loads(self.runner(["gh", "api", f"users/{login}"]))

    def comment_author_types(self, number):
        text = self.runner(["gh", "api", "--paginate",
                            f"repos/{{owner}}/{{repo}}/issues/{number}/comments?per_page=100",
                            "--jq", AUTHOR_TYPES_JQ])
        return identity.parse_author_types(text)

    def issue_body_edits(self, number):
        try:
            text = self.runner(["gh", "api", "graphql", "--paginate",
                                "-F", "owner={owner}", "-F", "repo={repo}",
                                "-F", f"number={number}",
                                "-f", f"query={EDITS_QUERY}", "--jq", _EDITS_JQ])
        except run.RunError:
            return None
        edits = []
        for line in (text or "").splitlines():
            parts = line.rstrip("\r\n").split("\t")
            if len(parts) != 3 or not parts[0].strip():
                continue
            editor = {"login": parts[1], "__typename": parts[2]} if parts[1] else None
            edits.append({"editedAt": parts[0].strip(), "editor": editor})
        return edits

    def issue_timeline(self, number):
        try:
            text = self.runner(["gh", "api", "--paginate",
                                f"repos/{{owner}}/{{repo}}/issues/{number}/timeline?per_page=100",
                                "--jq", TIMELINE_JQ])
        except run.RunError:
            return None
        events = []
        for line in (text or "").splitlines():
            parts = line.rstrip("\r\n").split("\t")
            if len(parts) != 4:
                continue
            events.append({"event": parts[0], "by": parts[1], "subject": parts[2], "at": parts[3]})
        return events
