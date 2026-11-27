import json
import urllib.parse

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

REVIEWS_JQ = (".[] | select(.submitted_at != null) | {id: .node_id, "
              "author: {login: .user.login, type: .user.type}, body: .body, "
              "createdAt: .submitted_at, url: .html_url} | @json")
FILES_JQ = (".[] | {filename: .filename, status: .status, "
            "previous_filename: .previous_filename, additions: .additions, "
            "deletions: .deletions} | @json")
_MILESTONES = "repos/{owner}/{repo}/milestones"


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

    def issue_view_text(self, number, fields):
        return self.runner(["gh", "issue", "view", str(number), "--json", fields])

    def pr_view(self, number, fields, empty=None):
        found = self._read(["gh", "pr", "view", str(number), "--json", fields], empty)
        if "files" in fields.split(",") and found is not empty:
            found["files"] = self.pr_files(number)
        return found

    def pr_files(self, number):
        text = self.runner(["gh", "api", "--paginate",
                            f"repos/{{owner}}/{{repo}}/pulls/{number}/files?per_page=100",
                            "--jq", FILES_JQ])
        files = []
        for line in (text or "").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            entry = {"path": item.get("filename"), "additions": item.get("additions"),
                     "deletions": item.get("deletions")}
            status = item.get("status")
            if status in ("renamed", "copied"):
                if item.get("previous_filename"):
                    entry["previousPath"] = item["previous_filename"]
            elif status:
                entry["previousPath"] = None
            files.append(entry)
        return files

    def closing_problems(self, pr):
        return []

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

    def label_list(self):
        return self._read(["gh", "label", "list", "--limit", "500", "--json",
                           "name,description"], [])

    def label_create(self, name, color, description, role=None):
        return self._write(["gh", "label", "create", name, "--color", color,
                            "--description", description], role)

    def pr_review(self, number, event, path, role=None, commit=None):
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

    def milestones(self):
        return self._read(["gh", "api", _MILESTONES + "?state=all"], [])

    def milestone_create(self, title, description=None, due_on=None, role=None):
        argv = ["gh", "api", _MILESTONES, "-f", f"title={title}"]
        if description:
            argv += ["-f", f"description={description}"]
        if due_on:
            argv += ["-f", f"due_on={due_on}"]
        return self._write(argv, role)

    def milestone_edit(self, number, changes, role=None):
        argv = ["gh", "api", "--method", "PATCH", f"{_MILESTONES}/{number}"]
        for key, value in changes:
            argv += ["-f", f"{key}={value}"]
        return self._write(argv, role)

    def issue_create(self, title, path, labels=(), milestone=None, role=None):
        argv = ["gh", "issue", "create", "--title", title, "--body-file", path]
        for label in labels:
            argv += ["--label", label]
        if milestone:
            argv += ["--milestone", milestone]
        return self._write(argv, role)

    def item_edit(self, target, number, title=None, add_labels=(), remove_labels=(),
                  milestone=None, remove_milestone=False, path=None, role=None):
        argv = ["gh", target, "edit", str(number)]
        if title:
            argv += ["--title", title]
        for label in add_labels:
            argv += ["--add-label", label]
        for label in remove_labels:
            argv += ["--remove-label", label]
        if milestone:
            argv += ["--milestone", milestone]
        if remove_milestone:
            argv += ["--remove-milestone"]
        if path is not None:
            argv += ["--body-file", path]
        return self._write(argv, role)

    def pr_create(self, title, path, head=None, draft=False, role=None):
        argv = ["gh", "pr", "create", "--title", title, "--body-file", path]
        if head:
            argv += ["--head", head]
        if draft:
            argv += ["--draft"]
        return self._write(argv, role)

    def pr_ready(self, number, undo=False, role=None):
        argv = ["gh", "pr", "ready", str(number)]
        if undo:
            argv.append("--undo")
        return self._write(argv, role)

    def pr_review_items(self, number):
        text = self.runner(["gh", "api", "--paginate",
                            f"repos/{{owner}}/{{repo}}/pulls/{number}/reviews?per_page=100",
                            "--jq", REVIEWS_JQ])
        return [json.loads(line) for line in (text or "").splitlines() if line.strip()]

    def review_edit_body(self, number, review_id, path, role=None):
        return self._write(["gh", "api", "-X", "PUT",
                            f"repos/{{owner}}/{{repo}}/pulls/{number}/reviews/{review_id}",
                            "-F", f"body=@{path}", "--silent"], role)

    def comment_edit_body(self, comment_id, path, role=None):
        return self._write(["gh", "api", "-X", "PATCH",
                            f"repos/{{owner}}/{{repo}}/issues/comments/{comment_id}",
                            "-F", f"body=@{path}", "--silent"], role)

    def review_request_remove(self, number, login, role=None):
        return self._write(["gh", "api", "-X", "DELETE",
                            f"repos/{{owner}}/{{repo}}/pulls/{number}/requested_reviewers",
                            "-f", f"reviewers[]={login}", "--silent"], role)

    def run_view(self, run_id, fields):
        return self._read(["gh", "run", "view", str(run_id), "--json", fields])

    def pr_diff(self, number):
        return self.runner(["gh", "pr", "diff", str(number)])

    def pr_merge(self, number, head_oid, role=None):
        return self._write(["gh", "pr", "merge", str(number), "--merge",
                            "--match-head-commit", head_oid], role)

    def workflow_entries_text(self, owner, repo, sha):
        return self.runner(["gh", "api", f"repos/{owner}/{repo}/contents/.github/workflows?ref={sha}"])

    def workflow_file_text(self, owner, repo, name, sha):
        return self.runner(["gh", "api",
                            f"repos/{owner}/{repo}/contents/.github/workflows/{name}?ref={sha}",
                            "-H", "Accept: application/vnd.github.raw"])

    def actions_permissions_text(self, owner, repo):
        return self.runner(["gh", "api", f"repos/{owner}/{repo}/actions/permissions"])

    def branch_head(self, owner, repo, branch):
        return self._read(["gh", "api", f"repos/{owner}/{repo}/commits/{branch}"])

    def last_push(self, owner, repo, branch):
        ref = urllib.parse.quote(branch, safe="/")
        entries = self._read(["gh", "api",
                              f"repos/{owner}/{repo}/activity?ref=refs/heads/{ref}&per_page=10"])
        for entry in entries:
            if entry.get("activity_type") in ("push", "force_push", "branch_creation"):
                return entry.get("timestamp")
        return None

    def run_list_text(self, branch, sha, event, limit, fields):
        return self.runner(["gh", "run", "list", "--branch", branch, "--commit", sha,
                            "--event", event, "--limit", str(limit), "--json", fields])

    def milestones_text(self, owner, repo, state):
        return self.runner(["gh", "api", f"repos/{owner}/{repo}/milestones?state={state}&per_page=100"])

    def _issue_list_argv(self, fields, state, limit, milestone=None, label=None, search=None):
        argv = ["gh", "issue", "list"]
        if milestone is not None:
            argv += ["--milestone", milestone]
        if label is not None:
            argv += ["--label", label]
        argv += ["--state", state]
        if search is not None:
            argv += ["--search", search]
        return argv + ["--limit", str(limit), "--json", fields]

    def issue_list_text(self, fields, state, limit, milestone=None, label=None, search=None):
        return self.runner(self._issue_list_argv(fields, state, limit, milestone, label, search))

    def issue_list(self, fields, state, limit, milestone=None, label=None, search=None, empty=None):
        return self._read(self._issue_list_argv(fields, state, limit, milestone, label, search), empty)

    def issues_closed_since(self, fields, day, limit, empty=None):
        return self._read(self._issue_list_argv(fields, "closed", limit, search="closed:>=" + day),
                          empty)

    def pr_list_text(self, fields, state, limit, head=None):
        argv = ["gh", "pr", "list", "--state", state, "--limit", str(limit)]
        if head:
            argv += ["--head", head]
        return self.runner(argv + ["--json", fields])

    def run_log_failed(self, run_id):
        return self.runner(["gh", "run", "view", str(run_id), "--log-failed"])
