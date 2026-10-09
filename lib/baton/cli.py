import json
import os
import sys

from . import checklist, comment, config, gate, githooks, issue, labels, merge, review, run, scan, sprint


def cmd_config(args):
    cfg = config.load(os.getcwd())
    if cfg is None:
        print("not a Baton repository: .baton/brief.md not found", file=sys.stderr)
        return 1
    print(json.dumps(cfg.data, indent=2))
    return 0


def cmd_gate(args):
    return gate.main(sys.stdin.read())


def _report(problems, what):
    if not problems:
        return 0
    print(f"Baton blocked this {what}:", file=sys.stderr)
    for problem in problems:
        print(f"- {problem}", file=sys.stderr)
    return 1


def cmd_git_hook(args):
    name = args[0] if args else ""
    terms = scan.load_terms()
    if name == "pre-commit":
        return _report(githooks.pre_commit(os.getcwd(), terms), "commit")
    if name == "pre-push":
        return _report(githooks.pre_push(os.getcwd(), sys.stdin.read(), terms), "push")
    print(f"baton: unknown git hook {name!r}", file=sys.stderr)
    return 64


def cmd_install_hooks(args):
    baton_bin = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "bin", "baton"))
    try:
        for path in githooks.install(os.getcwd(), baton_bin):
            print(f"installed {path}")
    except githooks.HookError as error:
        print(f"baton: {error}", file=sys.stderr)
        return 1
    return 0


def _require_config():
    cfg = config.load(os.getcwd())
    if cfg is None:
        raise config.ConfigError("not a Baton repository: .baton/brief.md not found")
    return cfg


def cmd_labels(args):
    cfg = _require_config()
    create = cfg.data["labels"]["auto_create"] or "--create" in args
    created, still = labels.ensure(cfg, run.run, create=create)
    for name in created:
        print(f"created {name}")
    for name in still:
        print(f"missing {name}", file=sys.stderr)
    return 1 if still else 0


def cmd_set_status(args):
    if len(args) != 2:
        print("usage: baton set-status <issue-number> <status:name>", file=sys.stderr)
        return 64
    labels.set_status(_require_config(), run.run, int(args[0]), args[1])
    return 0


def cmd_merge_pr(args):
    if len(args) != 1 or not args[0].isdigit():
        print("usage: baton merge-pr <pull-request-number>", file=sys.stderr)
        return 64

    def tolerant(argv, cwd=None):
        cleanup = (argv[:3] in (["git", "branch", "-D"], ["git", "push", "origin"],
                                ["git", "worktree", "remove"]) or argv[:2] == ["git", "-C"])
        try:
            return run.run(argv, cwd)
        except run.RunError as error:
            if not cleanup:
                raise
            print(f"baton: cleanup skipped: {error}", file=sys.stderr)
            return ""

    notes = []
    try:
        problems = merge.merge(int(args[0]), _require_config(), tolerant, notes)
    except merge.PostMergeError as error:
        print(f"baton: {error}", file=sys.stderr)
        return 1
    if problems:
        print(f"Baton refused to merge #{args[0]}:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    print(f"merged #{args[0]}")
    for note in notes:
        print(f"baton: {note}", file=sys.stderr)
    return 0


def _owner():
    return json.loads(run.run(["gh", "repo", "view", "--json", "owner"]))["owner"]["login"]


def cmd_comment(args):
    if len(args) != 5:
        print("usage: baton comment <issue|pr> <number> <role> <kind> <body-file | ->",
              file=sys.stderr)
        return 64
    target, number, role, kind, path = args
    try:
        body = comment.read_body(path, sys.stdin)
        print(comment.post(_require_config(), run.run, target, int(number), role, kind,
                           body, scan.load_terms()))
    except comment.CommentError as error:
        print(f"baton: {error}", file=sys.stderr)
        return 1
    return 0


def cmd_review(args):
    if len(args) == 2 and args[0] == "state" and args[1].isdigit():
        cfg = _require_config()
        repo = json.loads(run.run(["gh", "repo", "view", "--json", "owner,defaultBranchRef"]))
        pr = json.loads(run.run(["gh", "pr", "view", args[1], "--json",
                                 "number,headRefName,headRefOid,comments"]))
        print(json.dumps(review.current(pr, repo["owner"]["login"], cfg, run.run,
                                        repo["defaultBranchRef"]["name"]), indent=2))
        return 0
    if len(args) == 5 and args[0] == "post" and args[1].isdigit():
        _, number, verdict, commit, path = args
        try:
            body = comment.read_body(path, sys.stdin)
            print(review.post(_require_config(), run.run, int(number), verdict, commit, body,
                              scan.load_terms(), _owner()))
        except (comment.CommentError, review.ReviewError, checklist.TaskError) as error:
            print(f"baton: {error}", file=sys.stderr)
            return 1
        return 0
    print("usage: baton review state <pr> | baton review post <pr> <approve|changes> "
          "<full commit sha> <body-file | ->", file=sys.stderr)
    return 64


def cmd_tasks(args):
    if len(args) == 2 and args[0] == "issue" and args[1].isdigit():
        _require_config()
        found = checklist.issue_plan(run.run, int(args[1]))
        print(json.dumps({**found, **checklist.progress(found["tasks"])}, indent=2))
        return 0
    if len(args) == 2 and args[0] == "pr" and args[1].isdigit():
        _require_config()
        pr = json.loads(run.run(["gh", "pr", "view", args[1], "--json", "comments"]))
        print(json.dumps(review.review_tasks(pr, _owner()), indent=2))
        return 0
    print("usage: baton tasks <issue|pr> <number>", file=sys.stderr)
    return 64


def cmd_task(args):
    if len(args) == 4 and args[0] == "done" and args[1] in ("issue", "pr") and args[2].isdigit():
        _require_config()
        if args[1] == "issue":
            changed = checklist.tick_issue(run.run, int(args[2]), args[3])
        else:
            changed = review.tick_review(run.run, int(args[2]), _owner(), args[3])
        print(f"ticked task {args[3]}" if changed else f"task {args[3]} was already ticked")
        return 0
    print("usage: baton task done <issue|pr> <number> <task>", file=sys.stderr)
    return 64


def cmd_plan(args):
    if len(args) == 4 and args[0] == "replace" and args[1].isdigit() and args[2].isdigit():
        text = comment.read_body(args[3], sys.stdin)
        found = checklist.replace_issue_plan(_require_config(), run.run, int(args[1]), args[2],
                                             text, scan.load_terms())
        print(json.dumps(found, indent=2))
        return 0
    print("usage: baton plan replace <issue> <from-task> <file | ->", file=sys.stderr)
    return 64


def cmd_answers(args):
    if len(args) != 1 or not args[0].isdigit():
        print("usage: baton answers <issue-number>", file=sys.stderr)
        return 64
    _require_config()
    data = json.loads(run.run(["gh", "issue", "view", args[0], "--json", "comments"]))
    print(json.dumps(comment.pending_answers(data, _owner()), indent=2))
    return 0


def cmd_ready_check(args):
    if len(args) != 1 or not args[0].isdigit():
        print("usage: baton ready-check <issue-number>", file=sys.stderr)
        return 64
    cfg = _require_config()
    data = json.loads(run.run(["gh", "issue", "view", args[0], "--json", "author,body,labels"]))
    problems = issue.ready_problems(data, cfg, _owner())
    for problem in problems:
        print(f"- {problem}")
    if not problems:
        print("ready")
    return 1 if problems else 0


def cmd_sprint_status(args):
    cfg = _require_config()
    print(json.dumps(sprint.snapshot(cfg, run.run, args[0] if args else None), indent=2))
    return 0


def cmd_lock(args):
    ttl = 6.0
    if "--ttl-hours" in args:
        ttl = float(args[args.index("--ttl-hours") + 1])
    cfg = _require_config()
    common = run.run(["git", "-C", str(cfg.root), "rev-parse", "--git-common-dir"]).strip()
    path = os.path.join(str(cfg.root), common, "baton-sprint.lock")
    action = args[0] if args else ""
    if action == "acquire":
        if sprint.lock_acquire(path, f"pid {os.getppid()}", ttl):
            print("lock acquired")
            return 0
        print("another sprint run holds the lock", file=sys.stderr)
        return 1
    if action == "release":
        sprint.lock_release(path)
        print("lock released")
        return 0
    print("usage: baton lock <acquire|release> [--ttl-hours N]", file=sys.stderr)
    return 64


COMMANDS = {
    "answers": cmd_answers,
    "comment": cmd_comment,
    "ready-check": cmd_ready_check,
    "config": cmd_config,
    "gate": cmd_gate,
    "git-hook": cmd_git_hook,
    "install-hooks": cmd_install_hooks,
    "labels": cmd_labels,
    "lock": cmd_lock,
    "merge-pr": cmd_merge_pr,
    "plan": cmd_plan,
    "review": cmd_review,
    "set-status": cmd_set_status,
    "sprint-status": cmd_sprint_status,
    "task": cmd_task,
    "tasks": cmd_tasks,
}


def main(argv):
    if not argv or argv[0] not in COMMANDS:
        print("usage: baton <" + "|".join(sorted(COMMANDS)) + "> [args]", file=sys.stderr)
        return 64
    try:
        return COMMANDS[argv[0]](argv[1:])
    except (checklist.TaskError, comment.CommentError, review.ReviewError) as error:
        print(f"baton: {error}", file=sys.stderr)
        return 1
    except (config.ConfigError, run.RunError, ValueError, OSError) as error:
        print(f"baton: {error}", file=sys.stderr)
        return 2
