import json
import os
import sys
from datetime import datetime, timezone

from . import checklist, clean, comment, config, gate, githooks, issue, labels, merge, planfile, publish, questions, review, run, scan, sprint, state, usage, verify


def cmd_config(args):
    cfg = config.load(os.getcwd())
    if cfg is None:
        print("not a Baton repository: .baton/brief.md not found", file=sys.stderr)
        return 1
    data = dict(cfg.data)
    data["maintainers"] = list(data["maintainers"]) or cfg.maintainers(_owner())
    print(json.dumps(data, indent=2))
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
    except merge.PlanRetired as note:
        print(f"baton: {note}; not merged yet")
        return 0
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


def _maintainers(cfg):
    return cfg.maintainers(_owner())


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


def _publish_cmd(parse, args, act):
    try:
        opts = parse(args)
    except publish.UsageError as error:
        print(str(error), file=sys.stderr)
        return 64
    try:
        out = act(opts, _require_config(), scan.load_terms())
    except comment.CommentError as error:
        print(f"baton: {error}", file=sys.stderr)
        return 1
    if out:
        print(out)
    return 0


def _body(opts):
    source = opts.get("body_source")
    return None if source is None else comment.read_body(source, sys.stdin)


def _edit(target, opts, cfg, terms):
    publish.edit(cfg, run.run, target, opts["number"], terms, title=opts.get("title"),
                 body=_body(opts), add_labels=opts["add_labels"],
                 remove_labels=opts["remove_labels"], milestone=opts.get("milestone"),
                 remove_milestone=opts["remove_milestone"])
    return None


def cmd_issue(args):
    def act(o, cfg, terms):
        if o["action"] == "create":
            return publish.create_issue(cfg, run.run, o["title"], _body(o), o["labels"],
                                        o["milestone"], terms)
        return _edit("issue", o, cfg, terms)
    return _publish_cmd(publish.parse_issue, args, act)


def cmd_pr(args):
    def act(o, cfg, terms):
        if o["action"] == "create":
            return publish.create_pr(cfg, run.run, o["title"], _body(o), o["draft"], terms)
        if o["action"] == "ready":
            return publish.ready_pr(run.run, o["number"], o["undo"])
        return _edit("pr", o, cfg, terms)
    return _publish_cmd(publish.parse_pr, args, act)


def cmd_milestone(args):
    def act(o, cfg, terms):
        if o["action"] == "create":
            return publish.create_milestone(cfg, run.run, o["title"], o.get("description"),
                                            o.get("due_on"), terms)
        return publish.edit_milestone(cfg, run.run, o["ref"], terms, title=o.get("title"),
                                      description=o.get("description"), due_on=o.get("due_on"),
                                      state=o.get("state"))
    return _publish_cmd(publish.parse_milestone, args, act)


def _change_state(action, args):
    if len(args) != 4:
        print(f"usage: baton {action} <issue|pr> <number> <role> <reason-file | ->",
              file=sys.stderr)
        return 64
    target, number, role, path = args
    try:
        reason = comment.read_body(path, sys.stdin)
        print(state.change(_require_config(), run.run, action, target, int(number), role,
                           reason, scan.load_terms()))
    except comment.CommentError as error:
        print(f"baton: {error}", file=sys.stderr)
        return 1
    return 0


def cmd_close(args):
    return _change_state("close", args)


def cmd_reopen(args):
    return _change_state("reopen", args)


def cmd_review(args):
    if len(args) == 2 and args[0] == "state" and args[1].isdigit():
        cfg = _require_config()
        repo = json.loads(run.run(["gh", "repo", "view", "--json", "owner,defaultBranchRef"]))
        pr = json.loads(run.run(["gh", "pr", "view", args[1], "--json",
                                 "number,headRefName,headRefOid,comments"]))
        print(json.dumps(review.current(pr, cfg.maintainers(repo["owner"]["login"]), cfg, run.run,
                                        repo["defaultBranchRef"]["name"]),
                         indent=2, default=review.json_default))
        return 0
    if len(args) == 5 and args[0] == "post" and args[1].isdigit():
        _, number, verdict, commit, path = args
        try:
            body = comment.read_body(path, sys.stdin)
            cfg = _require_config()
            print(review.post(cfg, run.run, int(number), verdict, commit, body,
                              scan.load_terms(), _owner(), _repo_name().split("/", 1)[1],
                              maintainers=_maintainers(cfg)))
        except (comment.CommentError, review.ReviewError, checklist.TaskError) as error:
            print(f"baton: {error}", file=sys.stderr)
            return 1
        return 0
    print("usage: baton review state <pr> | baton review post <pr> <approve|changes|blocked|replan> "
          "<full commit sha> <body-file | ->", file=sys.stderr)
    return 64


def cmd_tasks(args):
    if len(args) == 2 and args[0] == "issue" and args[1].isdigit():
        cfg = _require_config()
        found = checklist.issue_plan(run.run, int(args[1]), cfg.data["docs"]["dir"])
        print(json.dumps({**found, **checklist.progress(found["tasks"])}, indent=2))
        return 0
    if len(args) == 2 and args[0] == "pr" and args[1].isdigit():
        cfg = _require_config()
        pr = json.loads(run.run(["gh", "pr", "view", args[1], "--json", "comments"]))
        print(json.dumps(review.review_tasks(pr, _maintainers(cfg)), indent=2))
        return 0
    print("usage: baton tasks <issue|pr> <number>", file=sys.stderr)
    return 64


_TASK_USAGE = "usage: baton task done <issue|pr> <number> <task> [--as-built <text>]"


def _repo_name():
    return json.loads(run.run(["gh", "repo", "view", "--json", "nameWithOwner"]))["nameWithOwner"]


def cmd_task(args):
    as_built = None
    if "--as-built" in args:
        at = args.index("--as-built")
        if at + 1 >= len(args) or args[1:2] != ["issue"]:
            print(_TASK_USAGE, file=sys.stderr)
            return 64
        as_built = args[at + 1]
        args = args[:at] + args[at + 2:]
    if len(args) == 4 and args[0] == "done" and args[1] in ("issue", "pr") and args[2].isdigit():
        cfg = _require_config()
        if args[1] == "issue":
            changed = checklist.tick_issue(run.run, int(args[2]), args[3], cwd=os.getcwd(),
                                           docs_dir=cfg.data["docs"]["dir"], as_built=as_built,
                                           terms=scan.load_terms() if as_built else None,
                                           allow=cfg.data["privacy_allow"])
        else:
            if as_built:
                print(_TASK_USAGE, file=sys.stderr)
                return 64
            if (os.environ.get("BATON_ROLE") != "orchestrator"
                    and review.in_linked_worktree(run.run, os.getcwd())):
                print("baton: warning: task done pr is the orchestrator's; developers report "
                      "FIXED or DECLINED instead. Set BATON_ROLE=orchestrator to silence this.",
                      file=sys.stderr)
            changed = review.tick_review(run.run, int(args[2]), _maintainers(cfg), args[3])
        print(f"ticked task {args[3]}" if changed else f"task {args[3]} was already ticked")
        return 0
    print(_TASK_USAGE, file=sys.stderr)
    return 64


def cmd_plan(args):
    drop = "--drop" in args
    args = [a for a in args if a != "--drop"]
    if (len(args) == 4 and args[0] in ("replace", "insert")
            and args[1].isdigit() and args[2].isdigit()):
        insert = args[0] == "insert"
        text = comment.read_body(args[3], sys.stdin)
        found = checklist.replace_issue_plan(_require_config(), run.run, int(args[1]), args[2],
                                             text, scan.load_terms(), drop=drop, insert=insert)
        for label, key in (("replaced", "replaced"), ("new", "written")):
            for task_id, title in found[key]:
                print(f"{label} task {task_id}: {title}")
        print(json.dumps(found, indent=2))
        return 0
    if len(args) == 3 and args[0] == "set" and args[1].isdigit():
        text = comment.read_body(args[2], sys.stdin)
        found = checklist.set_issue_plan(_require_config(), run.run, int(args[1]), text,
                                         scan.load_terms())
        print(f"set Developer Plan of #{args[1]}: {len(found)} task(s)")
        return 0
    if len(args) == 3 and args[0] == "against" and args[1].isdigit():
        checklist.against_issue_plan(_require_config(), run.run, int(args[1]), args[2])
        print(f"written against {args[2]}")
        return 0
    if args and args[0] in ("commit", "pin", "link") and len(args) >= 2 and args[1].isdigit():
        print("deprecated: plans live in the issue body; use `baton plan set <N> <file>`",
              file=sys.stderr)
        return 0
    if len(args) == 2 and args[0] == "migrate" and args[1].isdigit():
        print(planfile.migrate(_require_config(), run.run, int(args[1])))
        return 0
    print("usage: baton plan replace <issue> <from-task> <file | -> [--drop]\n"
          "       baton plan insert <issue> <before-task> <file | ->\n"
          "       baton plan set <issue> <file | ->\n"
          "       baton plan against <issue> <sha>\n"
          "       baton plan commit <issue> <path> <file | ->   (deprecated, does nothing)\n"
          "       baton plan pin <issue>   (deprecated, does nothing)\n"
          "       baton plan link <issue> ...   (deprecated, does nothing)\n"
          "       baton plan migrate <issue>\n"
          , file=sys.stderr)
    return 64


def cmd_usage(args):
    since, as_json, root, repo = None, False, "~/.claude/projects", None
    rest = list(args)
    while rest:
        flag = rest.pop(0)
        if flag == "--json":
            as_json = True
        elif flag in ("--since", "--dir", "--repo") and rest:
            value = rest.pop(0)
            if flag == "--since":
                since = value
            elif flag == "--dir":
                root = value
            else:
                repo = value
        else:
            print("usage: baton usage [--repo <owner/repo>] [--since <ISO time>] [--json] "
                  "[--dir <projects dir>]",
                  file=sys.stderr)
            return 64
    found = usage.report(root, since, repo)
    print(json.dumps(found, indent=2) if as_json else usage.table(found))
    return 0


def cmd_answers(args):
    if len(args) != 1 or not args[0].isdigit():
        print("usage: baton answers <issue-number>", file=sys.stderr)
        return 64
    cfg = _require_config()
    data = json.loads(run.run(["gh", "issue", "view", args[0], "--json", "comments"]))
    print(json.dumps(comment.pending_answers(data, _maintainers(cfg)), indent=2))
    return 0


def cmd_open_questions(args):
    if args:
        print("usage: baton open-questions", file=sys.stderr)
        return 64
    cfg = _require_config()
    issues = json.loads(run.run(["gh", "issue", "list", "--state", "open", "--limit", "300",
                                 "--json", "number,title,state,labels,body,author,comments"]))
    found = questions.open_questions(issues, cfg, _maintainers(cfg), datetime.now(timezone.utc))
    print(json.dumps(found, indent=2))
    return 0


def cmd_ready_check(args):
    if len(args) != 1 or not args[0].isdigit():
        print("usage: baton ready-check <issue-number>", file=sys.stderr)
        return 64
    cfg = _require_config()
    data = json.loads(run.run(["gh", "issue", "view", args[0], "--json", "author,body,labels"]))
    problems = issue.ready_problems(data, cfg, _maintainers(cfg), run.run)
    for problem in problems:
        print(f"- {problem}")
    if not problems:
        print("ready")
    return 1 if problems else 0


def cmd_sprint_status(args):
    cfg = _require_config()
    print(json.dumps(sprint.snapshot(cfg, run.run, args[0] if args else None), indent=2))
    return 0


def cmd_pr_state(args):
    if not args:
        print("usage: baton pr-state <PR>", file=sys.stderr)
        return 64
    cfg = _require_config()
    pr = json.loads(run.run(["gh", "pr", "view", args[0], "--json", sprint.PR_STATE_FIELDS]))
    print(json.dumps(sprint.pr_state(pr, cfg, run.run), indent=2))
    return 0


def cmd_verify(args):
    if len(args) != 2 or not args[0].isdigit():
        print("usage: baton verify <PR> <worktree>", file=sys.stderr)
        return 64
    cfg = _require_config()
    owner, name = _repo_name().split("/", 1)
    worktree = os.path.abspath(args[1])
    out = verify.verify(cfg, run.run, int(args[0]), worktree, owner, name)
    exit_text = "-" if out["exit"] is None else str(out["exit"])
    print(f"verify {out['result']} commit={out['commit'][:7]} exit={exit_text} "
          f"duration={out['seconds']}s")
    print(f"log: {out['log']}")
    print("--- last 40 lines ---")
    tail = out["tail"]
    print(tail if isinstance(tail, str) else "\n".join(tail))
    return {"green": 0, "red": 1, "timeout": 3}[out["result"]]


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


def cmd_clean(args):
    _require_config()
    repo = json.loads(run.run(["gh", "repo", "view", "--json", "nameWithOwner,defaultBranchRef"]))
    owner, name = repo["nameWithOwner"].split("/", 1)
    logs_root = verify.logs_dir(owner, name, 0).parent

    def report(line):
        print(line, file=sys.stderr if line.startswith("cannot remove") else sys.stdout)

    _, _, failed = clean.clean_worktrees(run.run, repo["defaultBranchRef"]["name"], report)
    _, failed_logs = clean.clean_logs(run.run, str(logs_root), report)
    return 1 if failed or failed_logs else 0


COMMANDS = {
    "clean": cmd_clean,
    "close": cmd_close,
    "answers": cmd_answers,
    "open-questions": cmd_open_questions,
    "comment": cmd_comment,
    "ready-check": cmd_ready_check,
    "config": cmd_config,
    "gate": cmd_gate,
    "git-hook": cmd_git_hook,
    "install-hooks": cmd_install_hooks,
    "issue": cmd_issue,
    "labels": cmd_labels,
    "lock": cmd_lock,
    "merge-pr": cmd_merge_pr,
    "milestone": cmd_milestone,
    "plan": cmd_plan,
    "pr": cmd_pr,
    "pr-state": cmd_pr_state,
    "reopen": cmd_reopen,
    "review": cmd_review,
    "set-status": cmd_set_status,
    "sprint-status": cmd_sprint_status,
    "task": cmd_task,
    "tasks": cmd_tasks,
    "usage": cmd_usage,
    "verify": cmd_verify,
}


def main(argv):
    if not argv or argv[0] not in COMMANDS:
        print("usage: baton <" + "|".join(sorted(COMMANDS)) + "> [args]", file=sys.stderr)
        return 64
    try:
        return COMMANDS[argv[0]](argv[1:])
    except (checklist.TaskError, comment.CommentError, review.ReviewError,
            verify.VerifyError) as error:
        print(f"baton: {error}", file=sys.stderr)
        return 1
    except (config.ConfigError, run.RunError, sprint.SnapshotError, ValueError, OSError) as error:
        print(f"baton: {error}", file=sys.stderr)
        return 2
