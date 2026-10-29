import json
import os
import sys
from datetime import datetime, timedelta, timezone

from . import budget, checklist, clean, comment, config, doctor, gate, githooks, identity, issue, labels, merge, planfile, publish, questions, review, run, sandbox, scan, sprint, state, triage, usage, verify


def cmd_config(args):
    cfg = config.load(os.getcwd())
    if cfg is None:
        print("not a Baton repository: .baton/brief.md not found", file=sys.stderr)
        return 1
    data = dict(cfg.data)
    data["maintainers"] = list(data["maintainers"]) or cfg.maintainers(_owner())
    owner, _, name = _repo_name().partition("/")
    data["identity"] = identity.describe(identity.load(owner, name))
    print(json.dumps(data, indent=2))
    return 0


def cmd_budget(args):
    return budget.main(sys.stdin.read())


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
    if name == "commit-msg":
        if len(args) < 2:
            print("usage: baton git-hook commit-msg <message-file>", file=sys.stderr)
            return 64
        return _report(githooks.commit_msg(os.getcwd(), args[1], terms), "commit")
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


def _set_group(group, args):
    if len(args) != 2 or not args[0].isdigit() or not args[1].isdigit():
        print(f"usage: baton set-{group} <issue-number> <1-4>", file=sys.stderr)
        return 64
    labels.set_group(_require_config(), run.run, int(args[0]), group, int(args[1]))
    return 0


def cmd_set_priority(args):
    return _set_group("priority", args)


def cmd_set_severity(args):
    return _set_group("severity", args)


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
        problems = merge.merge(int(args[0]), _require_config(), tolerant, notes, bots=_bots())
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


def _trusted(cfg):
    """Logins trusted to post marker comments: the maintainers and the Baton logins."""
    return list(_maintainers(cfg)) + sorted(_bots())


def _bots():
    return identity.bot_logins()


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


def cmd_triage(args):
    usage_line = "usage: baton triage apply <issue> <decision.json>  |  baton triage queue"
    if args == ["queue"]:
        return _triage_queue()
    if len(args) != 3 or args[0] != "apply" or not args[1].isdigit():
        print(usage_line, file=sys.stderr)
        return 64
    try:
        with open(args[2], encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        print(usage_line, file=sys.stderr)
        return 64
    cfg = _require_config()
    owner, _, repo = _repo_name().partition("/")
    decision = triage.parse_decision(text)
    print(triage.apply(cfg, run.run, int(args[1]), decision, _maintainers(cfg),
                       scan.load_terms(), owner, repo))
    return 0


def _triage_queue():
    cfg = _require_config()
    owner, _, repo = _repo_name().partition("/")
    now = datetime.now(timezone.utc)
    since, first_run = triage.resolve_since(cfg, owner, repo, now)
    issues = json.loads(run.run(["gh", "issue", "list", "--state", "open", "--limit", "1000",
                                 "--json", "number,title,state,labels,body,author,createdAt,comments"]))
    day_ago = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    closed = json.loads(run.run(["gh", "issue", "list", "--state", "closed",
                                 "--search", "closed:>=" + day_ago, "--limit", "200",
                                 "--json", "number,state,comments,createdAt"]) or "[]")
    block = cfg.data["triage"]
    batch, waiting = triage.split_batch(triage.pending(issues, cfg, since), block["max_per_run"])
    late = triage.overdue(waiting, now)
    print(json.dumps({
        "first_run": first_run,
        "since": since.isoformat(),
        "batch": [item["number"] for item in batch],
        "waiting": [item["number"] for item in waiting],
        "overdue": late["number"] if late else None,
        "stale": triage.stale(issues, cfg, now, block["stale_days"], _maintainers(cfg)),
        "reproduce": triage.reproduce_candidates(
            issues, cfg, now, block["max_reproductions_per_day"], closed=closed),
    }, indent=2))
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


def _issue_missed(args):
    if len(args) != 3 or not args[1].isdigit():
        print("usage: baton issue missed <issue number> <milestone title or number>",
              file=sys.stderr)
        return 64
    try:
        publish.mark_missed(_require_config(), run.run, int(args[1]), args[2], scan.load_terms())
    except comment.CommentError as error:
        print(f"baton: {error}", file=sys.stderr)
        return 1
    return 0


def _issue_read(args):
    if len(args) != 2 or not args[1].isdigit():
        print("usage: baton issue read <issue>", file=sys.stderr)
        return 64
    cfg = _require_config()
    data = json.loads(run.run(["gh", "issue", "view", args[1], "--json",
                               "number,title,state,author,labels,body,comments"]))
    print(issue.render(data, _maintainers(cfg), _bots()))
    return 0


def cmd_issue(args):
    if args[:1] == ["missed"]:
        return _issue_missed(args)
    if args[:1] == ["read"]:
        return _issue_read(args)

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


def _close_flags(args):
    """Split close's flags from its positional arguments; None when malformed."""
    rest, flags, i = [], {}, 0
    while i < len(args):
        arg = args[i]
        if arg == "--lock":
            flags["lock"] = True
        elif arg.startswith(("--reason", "--duplicate-of")):
            name, eq, value = arg.partition("=")
            if name not in ("--reason", "--duplicate-of"):
                return None
            if not eq:
                i += 1
                if i >= len(args):
                    return None
                value = args[i]
            if name == "--duplicate-of":
                if not value.isdigit():
                    return None
                flags["duplicate_of"] = int(value)
            else:
                flags["close_reason"] = value
        elif arg.startswith("--"):
            return None
        else:
            rest.append(arg)
        i += 1
    return rest, flags


def _change_state(action, args):
    flags = {}
    if action == "close":
        parsed = _close_flags(args)
        if parsed is not None:
            args, flags = parsed
    elif any(a.startswith("--") for a in args):
        args = []
    if len(args) != 4 or (action == "close" and parsed is None):
        reason_usage = " [--reason spam|duplicate|support|wont-do|completed]" \
                       " [--duplicate-of <N>] [--lock]" if action == "close" else ""
        print(f"usage: baton {action} <issue|pr> <number> <role> <reason-file | ->{reason_usage}",
              file=sys.stderr)
        return 64
    target, number, role, path = args
    try:
        reason = comment.read_body(path, sys.stdin)
        print(state.change(_require_config(), run.run, action, target, int(number), role,
                           reason, scan.load_terms(), **flags))
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
        print(json.dumps(review.current(pr, _trusted(cfg), cfg, run.run,
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
                              maintainers=_trusted(cfg)))
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
        print(json.dumps(review.review_tasks(pr, _trusted(cfg)), indent=2))
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
            changed = review.tick_review(run.run, int(args[2]), _trusted(cfg), args[3])
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
    print(json.dumps(comment.pending_answers(data, _maintainers(cfg), _bots()), indent=2))
    return 0


def cmd_open_questions(args):
    if args:
        print("usage: baton open-questions", file=sys.stderr)
        return 64
    cfg = _require_config()
    issues = json.loads(run.run(["gh", "issue", "list", "--state", "open", "--limit", "300",
                                 "--json", "number,title,state,labels,body,author,comments"]))
    found = questions.open_questions(issues, cfg, _maintainers(cfg), datetime.now(timezone.utc),
                                     bots=_bots())
    print(json.dumps(found, indent=2))
    return 0


def cmd_notify(args):
    usage = ("usage: baton notify <issue> <cause> <text-file | ->  |  baton notify --rules\n"
             f"causes: {', '.join(comment.NOTIFY_CAUSES)}")
    if args == ["--rules"]:
        cfg = _require_config()
        issues = json.loads(run.run(["gh", "issue", "list", "--state", "open", "--limit", "300",
                                     "--json", "number,title,state,labels,body,author,comments"]))
        try:
            for number in questions.notify_matches(issues, cfg):
                url = comment.notify(cfg, run.run, number, "rule",
                                     "This issue matches a notify rule in the brief.",
                                     _maintainers(cfg), scan.load_terms())
                print(f"#{number} {url or 'already notified'}")
        except comment.CommentError as error:
            print(f"baton: {error}", file=sys.stderr)
            return 1
        return 0
    if len(args) != 3 or not args[0].isdigit() or args[1] not in comment.NOTIFY_CAUSES:
        print(usage, file=sys.stderr)
        return 64
    cfg = _require_config()
    try:
        text = comment.read_body(args[2], sys.stdin)
        url = comment.notify(cfg, run.run, int(args[0]), args[1], text,
                             _maintainers(cfg), scan.load_terms())
    except comment.CommentError as error:
        print(f"baton: {error}", file=sys.stderr)
        return 1
    print(url or "already notified")
    return 0


def cmd_ready_check(args):
    if len(args) != 1 or not args[0].isdigit():
        print("usage: baton ready-check <issue-number>", file=sys.stderr)
        return 64
    cfg = _require_config()
    data = json.loads(run.run(["gh", "issue", "view", args[0], "--json", "author,body,labels,comments"]))
    maintainers = _maintainers(cfg)
    problems = issue.ready_problems(data, cfg, maintainers, run.run, _bots())
    for problem in problems:
        print(f"- {problem}")
    if not problems:
        print("ready (outside issue: the maintainer marks it ready)"
              if issue.is_outside(data, maintainers, _bots()) else "ready")
    return 1 if problems else 0


def cmd_sprint_status(args):
    cfg = _require_config()
    print(json.dumps(sprint.snapshot(cfg, run.run, args[0] if args else None,
                                  bots=_bots()), indent=2))
    return 0


def cmd_sprint_order(args):
    if not args or not all(a.isdigit() for a in args):
        print("usage: baton sprint-order <issue>...", file=sys.stderr)
        return 64
    cfg = _require_config()
    issues = json.loads(run.run(["gh", "issue", "list", "--state", "open", "--limit", "300",
                                 "--json", "number,labels"]))
    spike = cfg.label("type:spike")
    flags = {i["number"]: any(l.get("name") == spike for l in i.get("labels", []))
             for i in issues}
    numbers = [int(a) for a in args]
    if any(n not in flags for n in numbers):
        print("usage: baton sprint-order <issue>...", file=sys.stderr)
        return 64
    found = sprint.spikes_first(numbers, flags, cfg.data["spike"]["max_per_sprint"])
    print(json.dumps(found, indent=2))
    return 0


_CAPACITY_USAGE = "usage: baton sprint-capacity <days> [--retros <n>]"


def cmd_sprint_capacity(args):
    rest, days, count = list(args), None, sprint.RETRO_COUNT
    while rest:
        arg = rest.pop(0)
        if arg == "--retros" and rest:
            value = rest.pop(0)
            if not value.isdigit() or int(value) < 1:
                print(_CAPACITY_USAGE, file=sys.stderr)
                return 64
            count = int(value)
        elif not arg.startswith("--") and days is None:
            try:
                days = float(arg)
            except ValueError:
                days = 0
            if not 0 < days < float("inf"):
                print(_CAPACITY_USAGE, file=sys.stderr)
                return 64
            days = int(days) if days == int(days) else days
        else:
            print(_CAPACITY_USAGE, file=sys.stderr)
            return 64
    if days is None:
        print(_CAPACITY_USAGE, file=sys.stderr)
        return 64
    cfg = _require_config()
    found = sprint.recent_metrics(cfg.root, cfg.data["docs"]["dir"], count)
    print(json.dumps(sprint.capacity(found, days), indent=2))
    return 0


_METRICS_USAGE = "usage: baton sprint-metrics <milestone> [--since <UTC ISO time>] [--dir <projects dir>]"


def cmd_sprint_metrics(args):
    since, root, rest, milestone = None, "~/.claude/projects", list(args), None
    while rest:
        arg = rest.pop(0)
        if arg in ("--since", "--dir") and rest:
            value = rest.pop(0)
            if arg == "--since":
                since = value
            else:
                root = value
        elif not arg.startswith("--") and milestone is None:
            milestone = arg
        else:
            print(_METRICS_USAGE, file=sys.stderr)
            return 64
    if milestone is None:
        print(_METRICS_USAGE, file=sys.stderr)
        return 64
    cfg = _require_config()
    try:
        repo = json.loads(run.run(["gh", "repo", "view", "--json", "owner,name"]))
        found = usage.report(root, since, f"{repo['owner']['login']}/{repo['name']}")
        metrics = sprint.sprint_metrics(cfg, run.run, milestone, usage.total_weighted(found))
    except sprint.SnapshotError as error:
        print(f"baton: {error}", file=sys.stderr)
        return 1
    print(sprint.format_metrics(metrics))
    return 0


def cmd_pr_state(args):
    if not args:
        print("usage: baton pr-state <PR>", file=sys.stderr)
        return 64
    cfg = _require_config()
    pr = json.loads(run.run(["gh", "pr", "view", args[0], "--json", sprint.PR_STATE_FIELDS]))
    print(json.dumps(sprint.pr_state(pr, cfg, run.run, bots=_bots()), indent=2))
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


def cmd_sandbox(args):
    action = args[0] if args else ""
    if action not in ("run", "check") or (action == "run" and len(args) < 2) \
            or (action == "check" and len(args) != 1):
        print("usage: baton sandbox <run <command...>|check>", file=sys.stderr)
        return 64
    cfg = _require_config()
    repo = json.loads(run.run(["gh", "repo", "view", "--json", "nameWithOwner,defaultBranchRef"]))
    owner, name = repo["nameWithOwner"].split("/", 1)
    branch = repo["defaultBranchRef"]["name"]
    seconds = cfg["verify"]["timeout_minutes"] * 60
    if action == "check":
        state = sandbox.check(owner, name)
        print(state)
        return 0 if state == "configured" else 1
    out = sandbox.run_in_sandbox(run.run, owner, name, branch, " ".join(args[1:]), seconds)
    print(f"sandbox exit={out['exit']}")
    print(f"log: {out['log']}")
    print("--- last 40 lines ---")
    tail = out["tail"]
    print(tail if isinstance(tail, str) else "\n".join(tail))
    return out["exit"]


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


def cmd_identity(args):
    if args != ["worktree"]:
        print("usage: baton identity worktree", file=sys.stderr)
        return 64
    who = identity.commit_identity(run.run)
    if who is None:
        print("tier 0: commits keep your git identity")
        return 0
    cwd = os.getcwd()
    if not review.in_linked_worktree(run.run, cwd):
        print("baton: run this in a linked worktree, not the main checkout", file=sys.stderr)
        return 1
    name, email = who
    try:
        flag = run.run(["git", "config", "--get", "extensions.worktreeConfig"], cwd=cwd).strip()
    except run.RunError:
        flag = ""
    if flag != "true":
        run.run(["git", "config", "extensions.worktreeConfig", "true"], cwd=cwd)
    run.run(["git", "config", "--worktree", "user.name", name], cwd=cwd)
    run.run(["git", "config", "--worktree", "user.email", email], cwd=cwd)
    print(f"commits in this worktree are by {name} {email}")
    return 0


def cmd_push(args):
    if len(args) != 1:
        print("usage: baton push <refspec>", file=sys.stderr)
        return 64
    info = json.loads(run.run(["gh", "repo", "view", "--json", "nameWithOwner,defaultBranchRef"]))
    default = (info.get("defaultBranchRef") or {}).get("name", "")
    problem = identity.refspec_problem(args[0], default, identity.ref_format_ok)
    if problem:
        print(f"baton push: refused {args[0]}: {problem}", file=sys.stderr)
        return 1
    argv, env = identity.push_command(args[0], info["nameWithOwner"])
    print(run.run(argv, cwd=os.getcwd(), env=env), end="")
    return 0


def cmd_doctor(args):
    if args:
        print("usage: baton doctor", file=sys.stderr)
        return 64
    return doctor.main(_repo_name())


COMMANDS = {
    "doctor": cmd_doctor,
    "clean": cmd_clean,
    "sprint-metrics": cmd_sprint_metrics,
    "close": cmd_close,
    "answers": cmd_answers,
    "notify": cmd_notify,
    "open-questions": cmd_open_questions,
    "comment": cmd_comment,
    "ready-check": cmd_ready_check,
    "config": cmd_config,
    "budget": cmd_budget,
    "gate": cmd_gate,
    "git-hook": cmd_git_hook,
    "install-hooks": cmd_install_hooks,
    "identity": cmd_identity,
    "issue": cmd_issue,
    "labels": cmd_labels,
    "lock": cmd_lock,
    "merge-pr": cmd_merge_pr,
    "milestone": cmd_milestone,
    "plan": cmd_plan,
    "push": cmd_push,
    "pr": cmd_pr,
    "pr-state": cmd_pr_state,
    "reopen": cmd_reopen,
    "review": cmd_review,
    "sandbox": cmd_sandbox,
    "set-status": cmd_set_status,
    "set-priority": cmd_set_priority,
    "set-severity": cmd_set_severity,
    "sprint-capacity": cmd_sprint_capacity,
    "sprint-order": cmd_sprint_order,
    "sprint-status": cmd_sprint_status,
    "task": cmd_task,
    "tasks": cmd_tasks,
    "triage": cmd_triage,
    "usage": cmd_usage,
    "verify": cmd_verify,
}


def _load_identity():
    try:
        repo = _repo_name()
    except run.RunError as error:
        raise identity.IdentityError(f"could not determine the repository to find identity.toml: {error}")
    return identity.load(*repo.split("/", 1))


def main(argv):
    if not argv or argv[0] not in COMMANDS:
        print("usage: baton <" + "|".join(sorted(COMMANDS)) + "> [args]", file=sys.stderr)
        return 64
    identity.use(_load_identity)
    try:
        return COMMANDS[argv[0]](argv[1:])
    except (identity.IdentityError, checklist.TaskError, comment.CommentError, review.ReviewError,
            verify.VerifyError, sandbox.SandboxError) as error:
        print(f"baton: {error}", file=sys.stderr)
        return 1
    except (config.ConfigError, run.RunError, sprint.SnapshotError, ValueError, OSError) as error:
        print(f"baton: {error}", file=sys.stderr)
        return 2
    finally:
        identity.use(None)
