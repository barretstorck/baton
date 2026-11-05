import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from . import branches, cliargs, budget, checklist, clean, comment, compact, config, doctor, gate, githooks, identity, issue, labels, merge, orchestrator, planfile, publish, questions, review, run, sandbox, scan, sprint, state, triage, usage, verify


@cliargs.command("config")
def cmd_config(opts):
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


@cliargs.command("budget")
def cmd_budget(opts):
    return budget.main(sys.stdin.read())


@cliargs.command("compact-reminder")
def cmd_compact_reminder(opts):
    return compact.main(sys.stdin.read())


@cliargs.command("gate")
def cmd_gate(opts):
    return gate.main(sys.stdin.read())


@cliargs.command("hook")
def cmd_hook(opts):
    from . import hook
    return hook.main(sys.stdin.read())


def _report(problems, what):
    if not problems:
        return 0
    print(f"Baton blocked this {what}:", file=sys.stderr)
    for problem in problems:
        print(f"- {problem}", file=sys.stderr)
    return 1


@cliargs.command("git-hook")
def cmd_git_hook(opts):
    terms = scan.load_terms()
    if opts.action == "pre-commit":
        return _report(githooks.pre_commit(os.getcwd(), terms), "commit")
    if opts.action == "commit-msg":
        return _report(githooks.commit_msg(os.getcwd(), opts.message_file, terms), "commit")
    return _report(githooks.pre_push(os.getcwd(), sys.stdin.read(), terms), "push")


@cliargs.command("install-hooks")
def cmd_install_hooks(opts):
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


@cliargs.command("labels")
def cmd_labels(opts):
    cfg = _require_config()
    create = cfg.data["labels"]["auto_create"] or opts.create
    created, still = labels.ensure(cfg, run.run, create=create)
    for name in created:
        print(f"created {name}")
    for name in still:
        print(f"missing {name}", file=sys.stderr)
    return 1 if still else 0


@cliargs.command("set-status")
def cmd_set_status(opts):
    labels.set_status(_require_config(), run.run, opts.issue, opts.status)
    return 0


def _set_group(group, opts):
    labels.set_group(_require_config(), run.run, opts.issue, group, opts.value)
    return 0


@cliargs.command("set-priority")
def cmd_set_priority(opts):
    return _set_group("priority", opts)


@cliargs.command("set-severity")
def cmd_set_severity(opts):
    return _set_group("severity", opts)


@cliargs.command("merge-pr")
def cmd_merge_pr(opts):
    notes = []
    try:
        problems = merge.merge(opts.pr, _require_config(), merge.tolerant(run.run), notes, bots=_bots(),
                               reviewers=_reviewers())
    except merge.PlanRetired as note:
        print(f"baton: {note}; not merged yet")
        return 0
    except merge.PostMergeError as error:
        print(f"baton: {error}", file=sys.stderr)
        return 1
    if problems:
        print(f"Baton refused to merge #{opts.pr}:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    print(f"merged #{opts.pr}")
    for note in notes:
        print(f"baton: {note}", file=sys.stderr)
    return 0


def _owner():
    return json.loads(run.run(["gh", "repo", "view", "--json", "owner"]))["owner"]["login"]


def _maintainers(cfg):
    return cfg.maintainers(_owner())


def _trusted(cfg):
    """Logins trusted for review and verify markers: the maintainers and the reviewer App."""
    return list(_maintainers(cfg)) + sorted(_reviewers())


def _bots():
    return identity.bot_logins()


def _reviewers():
    return identity.reviewer_logins()


@cliargs.command("comment")
def cmd_comment(opts):
    try:
        body = comment.read_body(opts.body, sys.stdin)
        print(comment.post(_require_config(), run.run, opts.target, opts.number, opts.role, opts.kind,
                           body, scan.load_terms()))
    except comment.CommentError as error:
        print(f"baton: {error}", file=sys.stderr)
        return 1
    return 0


_EXEC_REFUSED_GROUPS = ("auth", "extension", "alias")


_EXEC_DROPPED_ENV = ("GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")


def _exec_forbidden(words):
    """Explicit refusals that hold even if the write allowlist changes: token-printing or
    code-running groups, a leading global flag, and --hostname (would send the token elsewhere)."""
    if words[1].startswith("-") or words[1] in _EXEC_REFUSED_GROUPS:
        return True
    if any(w == "--hostname" or w.startswith("--hostname=") for w in words):
        return True
    if words[1] == "api" and any("://" in w for w in words[2:]):
        return True
    return any(not _OWNER_REPO.fullmatch(v) for v in _repo_values(words))


_OWNER_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def _repo_values(words):
    """The value of every -R/--repo option (separate, `=` or attached form); "" when it has none."""
    out = []
    for i, w in enumerate(words[2:], 2):
        if w in ("-R", "--repo"):
            out.append(words[i + 1] if i + 1 < len(words) else "")
        elif w.startswith("--repo="):
            out.append(w[len("--repo="):])
        elif w.startswith("-R"):
            out.append(w[2:])
    return out


@cliargs.command("exec")
def cmd_exec(opts):
    words = opts.words
    if not words or words[0] != "gh" or len(words) < 2 or opts.role not in set(comment.ROLES) | set(config.ROLES) \
            or not gate.is_recognised_write(words) or _exec_forbidden(words):
        return cliargs.fail("exec")
    extra = identity.child_env(opts.role)
    env = None
    if extra:
        env = {k: v for k, v in os.environ.items() if k not in _EXEC_DROPPED_ENV}
        env.update(extra, GH_HOST="github.com")
    return subprocess.run(words, env=env).returncode


@cliargs.command("triage")
def cmd_triage(opts):
    if opts.action == "queue":
        return _triage_queue()
    try:
        with open(opts.decision, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return cliargs.fail("triage")
    cfg = _require_config()
    owner, _, repo = _repo_name().partition("/")
    decision = triage.parse_decision(text)
    print(triage.apply(cfg, run.run, opts.issue, decision, _maintainers(cfg),
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
    for item in issues + closed:
        item["comments"] = identity.mark_authors(run.run, item["number"], item.get("comments"))
    maintainers = _maintainers(cfg)
    trusted = list(maintainers) + sorted(_bots())
    block = cfg.data["triage"]
    batch, waiting = triage.split_batch(triage.pending(issues, cfg, since), block["max_per_run"])
    late = triage.overdue(waiting, now)
    print(json.dumps({
        "first_run": first_run,
        "since": since.isoformat(),
        "batch": [item["number"] for item in batch],
        "waiting": [item["number"] for item in waiting],
        "overdue": late["number"] if late else None,
        "stale": triage.stale(issues, cfg, now, block["stale_days"], maintainers),
        "reproduce": triage.reproduce_candidates(
            issues, cfg, now, block["max_reproductions_per_day"], trusted, closed=closed,
            maintainers=maintainers, bots=_bots()),
    }, indent=2))
    return 0


def _publish_run(opts, act):
    try:
        out = act(opts, _require_config(), scan.load_terms())
    except comment.CommentError as error:
        print(f"baton: {error}", file=sys.stderr)
        return 1
    if out:
        print(out)
    return 0


def _body(opts):
    source = getattr(opts, "body_source", None)
    return None if source is None else comment.read_body(source, sys.stdin)


def _edit(target, opts, cfg, terms):
    publish.edit(cfg, run.run, target, opts.number, terms, title=opts.title,
                 body=_body(opts), add_labels=opts.add_labels,
                 remove_labels=opts.remove_labels, milestone=opts.milestone,
                 remove_milestone=opts.remove_milestone)
    return None


def _issue_missed(opts):
    try:
        publish.mark_missed(_require_config(), run.run, opts.number, opts.milestone,
                            scan.load_terms())
    except comment.CommentError as error:
        print(f"baton: {error}", file=sys.stderr)
        return 1
    return 0


def _issue_read(opts):
    cfg = _require_config()
    number = str(opts.number)
    data = json.loads(run.run(["gh", "issue", "view", number, "--json",
                               "number,title,state,author,labels,body,comments"]))
    data["comments"] = identity.mark_authors(run.run, opts.number, data.get("comments"))
    print(issue.render(data, _maintainers(cfg), _bots()))
    return 0


@cliargs.command("issue")
def cmd_issue(opts):
    if opts.action == "missed":
        return _issue_missed(opts)
    if opts.action == "read":
        return _issue_read(opts)

    def act(o, cfg, terms):
        if o.action == "create":
            return publish.create_issue(cfg, run.run, o.title, _body(o), o.labels,
                                        o.milestone, terms)
        return _edit("issue", o, cfg, terms)
    return _publish_run(opts, act)


@cliargs.command("pr")
def cmd_pr(opts):
    def act(o, cfg, terms):
        if o.action == "create":
            head = publish.resolve_head(run.run, os.getcwd(), o.head)
            return publish.create_pr(cfg, run.run, o.title, _body(o), o.draft, terms,
                                     head=head)
        if o.action == "ready":
            return publish.ready_pr(run.run, o.number, o.undo)
        return _edit("pr", o, cfg, terms)
    return _publish_run(opts, act)


@cliargs.command("milestone")
def cmd_milestone(opts):
    def act(o, cfg, terms):
        if o.action == "create":
            return publish.create_milestone(cfg, run.run, o.title, o.description,
                                            o.due_on, terms)
        return publish.edit_milestone(cfg, run.run, o.ref, terms, title=o.title,
                                      description=o.description, due_on=o.due_on,
                                      state=o.state)
    return _publish_run(opts, act)


def _change_state(action, opts):
    flags = {"close_reason": getattr(opts, "close_reason", None),
             "duplicate_of": getattr(opts, "duplicate_of", None),
             "lock": getattr(opts, "lock", False)}
    flags = {k: v for k, v in flags.items() if v}
    try:
        reason = comment.read_body(opts.reason_file, sys.stdin)
        print(state.change(_require_config(), run.run, action, opts.target, opts.number,
                           opts.role, reason, scan.load_terms(), **flags))
    except comment.CommentError as error:
        print(f"baton: {error}", file=sys.stderr)
        return 1
    return 0


@cliargs.command("close")
def cmd_close(opts):
    return _change_state("close", opts)


@cliargs.command("reopen")
def cmd_reopen(opts):
    return _change_state("reopen", opts)


def _review_state(opts):
    cfg = _require_config()
    repo = json.loads(run.run(["gh", "repo", "view", "--json", "owner,defaultBranchRef"]))
    pr = json.loads(run.run(["gh", "pr", "view", str(opts.pr), "--json",
                             "number,headRefName,headRefOid,comments"]))
    pr["comments"] = identity.mark_authors(run.run, opts.pr, pr.get("comments"))
    print(json.dumps(review.current(pr, _trusted(cfg), cfg, run.run,
                                    repo["defaultBranchRef"]["name"],
                                    list(_maintainers(cfg)) + sorted(_bots())),
                     indent=2, default=review.json_default))
    return 0


def _review_post(opts):
    try:
        body = comment.read_body(opts.body, sys.stdin)
        cfg = _require_config()
        print(review.post(cfg, run.run, opts.pr, opts.verdict, opts.commit, body,
                          scan.load_terms(), _owner(), _repo_name().split("/", 1)[1],
                          maintainers=_trusted(cfg)))
    except (comment.CommentError, review.ReviewError, checklist.TaskError) as error:
        print(f"baton: {error}", file=sys.stderr)
        return 1
    return 0


@cliargs.command("review")
def cmd_review(opts):
    return _review_state(opts) if opts.action == "state" else _review_post(opts)


@cliargs.command("tasks")
def cmd_tasks(opts):
    cfg = _require_config()
    if opts.target == "issue":
        found = checklist.issue_plan(run.run, opts.number, cfg.data["docs"]["dir"])
        print(json.dumps({**found, **checklist.progress(found["tasks"])}, indent=2))
        return 0
    pr = json.loads(run.run(["gh", "pr", "view", str(opts.number), "--json", "comments"]))
    pr["comments"] = identity.mark_authors(run.run, opts.number, pr.get("comments"))
    print(json.dumps(review.review_tasks(pr, _trusted(cfg)), indent=2))
    return 0


def _repo_name():
    return json.loads(run.run(["gh", "repo", "view", "--json", "nameWithOwner"]))["nameWithOwner"]


def _tick_pr(opts, cfg):
    if (os.environ.get("BATON_ROLE") != "orchestrator"
            and review.in_linked_worktree(run.run, os.getcwd())):
        print("baton: warning: task done pr is the orchestrator's; developers report "
              "FIXED or DECLINED instead. Set BATON_ROLE=orchestrator to silence this.",
              file=sys.stderr)
    return review.tick_review(run.run, opts.number, _trusted(cfg), opts.task)


@cliargs.command("orchestrator-agent")
def cmd_orchestrator_agent(opts):
    found = orchestrator.find_agent(opts.dir, os.environ.get("CLAUDE_CODE_SESSION_ID", ""))
    if not found:
        return 1
    print(found)
    return 0


@cliargs.command("task")
def cmd_task(opts):
    cfg = _require_config()
    if opts.target == "issue":
        as_built = opts.as_built
        changed = checklist.tick_issue(run.run, opts.number, opts.task, cwd=os.getcwd(),
                                       docs_dir=cfg.data["docs"]["dir"], as_built=as_built,
                                       terms=scan.load_terms() if as_built else None,
                                       allow=cfg.data["privacy_allow"])
    elif opts.as_built:
        return cliargs.fail("task")
    else:
        changed = _tick_pr(opts, cfg)
    print(f"ticked task {opts.task}" if changed else f"task {opts.task} was already ticked")
    return 0


def _plan_replace(opts):
    text = comment.read_body(opts.file, sys.stdin)
    found = checklist.replace_issue_plan(_require_config(), run.run, opts.issue, str(opts.task),
                                         text, scan.load_terms(), drop=opts.drop,
                                         insert=opts.action == "insert")
    for label, key in (("replaced", "replaced"), ("new", "written")):
        for task_id, title in found[key]:
            print(f"{label} task {task_id}: {title}")
    print(json.dumps(found, indent=2))
    return 0


def _plan_set(opts):
    text = comment.read_body(opts.file, sys.stdin)
    found = checklist.set_issue_plan(_require_config(), run.run, opts.issue, text,
                                     scan.load_terms())
    print(f"set Developer Plan of #{opts.issue}: {len(found["tasks"])} task(s)")
    return 0


@cliargs.command("plan")
def cmd_plan(opts):
    if opts.action in ("replace", "insert"):
        return _plan_replace(opts)
    if opts.action == "set":
        return _plan_set(opts)
    if opts.action == "against":
        checklist.against_issue_plan(_require_config(), run.run, opts.issue, opts.sha)
        print(f"written against {opts.sha}")
        return 0
    if opts.action == "migrate":
        print(planfile.migrate(_require_config(), run.run, opts.issue))
        return 0
    print("deprecated: plans live in the issue body; use `baton plan set <N> <file>`",
          file=sys.stderr)
    return 0


@cliargs.command("usage")
def cmd_usage(opts):
    found = usage.report(opts.dir, opts.since, opts.repo)
    print(json.dumps(found, indent=2) if opts.json else usage.table(found))
    return 0


@cliargs.command("answers")
def cmd_answers(opts):
    cfg = _require_config()
    data = json.loads(run.run(["gh", "issue", "view", str(opts.issue), "--json", "comments"]))
    data["comments"] = identity.mark_authors(run.run, opts.issue, data.get("comments"))
    print(json.dumps(comment.pending_answers(data, _maintainers(cfg), _bots()), indent=2))
    return 0


@cliargs.command("open-questions")
def cmd_open_questions(opts):
    cfg = _require_config()
    issues = json.loads(run.run(["gh", "issue", "list", "--state", "open", "--limit", "300",
                                 "--json", "number,title,state,labels,body,author,comments"]))
    for item in issues:
        item["comments"] = identity.mark_authors(run.run, item["number"], item.get("comments"))
    found = questions.open_questions(issues, cfg, _maintainers(cfg), datetime.now(timezone.utc),
                                     bots=_bots())
    print(json.dumps(found, indent=2))
    return 0


def _notify_rules():
    cfg = _require_config()
    issues = json.loads(run.run(["gh", "issue", "list", "--state", "open", "--limit", "300",
                                 "--json", "number,title,state,labels,body,author,comments"]))
    try:
        for number in questions.notify_matches(issues, cfg):
            url = comment.notify(cfg, run.run, number, "rule",
                                 "This issue matches a notify rule in the brief.",
                                 _maintainers(cfg), scan.load_terms(),
                                 bots=_bots())
            print(f"#{number} {url or 'already notified'}")
    except comment.CommentError as error:
        print(f"baton: {error}", file=sys.stderr)
        return 1
    return 0


@cliargs.command("notify")
def cmd_notify(opts):
    given = [opts.issue, opts.cause, opts.file]
    if opts.rules:
        return _notify_rules() if not any(given) else cliargs.fail("notify")
    if None in given:
        return cliargs.fail("notify")
    cfg = _require_config()
    try:
        text = comment.read_body(opts.file, sys.stdin)
        url = comment.notify(cfg, run.run, opts.issue, opts.cause, text,
                             _maintainers(cfg), scan.load_terms(), bots=_bots())
    except comment.CommentError as error:
        print(f"baton: {error}", file=sys.stderr)
        return 1
    print(url or "already notified")
    return 0


@cliargs.command("ready-check")
def cmd_ready_check(opts):
    cfg = _require_config()
    number = opts.issue
    data = json.loads(run.run(["gh", "issue", "view", str(number), "--json", "author,body,labels,comments"]))
    data["comments"] = identity.mark_authors(run.run, number, data.get("comments"))
    maintainers = _maintainers(cfg)
    if issue.is_outside(data, maintainers, _bots()):
        data["edits"] = issue.body_edits(run.run, number)
    problems = issue.ready_problems(data, cfg, maintainers, run.run, _bots())
    for problem in problems:
        print(f"- {problem}")
    if not problems:
        print("ready (outside issue: the maintainer marks it ready)"
              if issue.is_outside(data, maintainers, _bots()) else "ready")
    return 1 if problems else 0


@cliargs.command("sprint-status")
def cmd_sprint_status(opts):
    cfg = _require_config()
    print(json.dumps(sprint.snapshot(cfg, run.run, opts.milestone,
                                  bots=_bots(), reviewers=_reviewers()), indent=2))
    return 0


@cliargs.command("sprint-order")
def cmd_sprint_order(opts):
    cfg = _require_config()
    issues = json.loads(run.run(["gh", "issue", "list", "--state", "open", "--limit", "300",
                                 "--json", "number,labels"]))
    spike = cfg.label("type:spike")
    flags = {i["number"]: any(l.get("name") == spike for l in i.get("labels", []))
             for i in issues}
    numbers = opts.issues
    if any(n not in flags for n in numbers):
        return cliargs.fail("sprint-order")
    found = sprint.spikes_first(numbers, flags, cfg.data["spike"]["max_per_sprint"])
    print(json.dumps(found, indent=2))
    return 0


@cliargs.command("sprint-capacity")
def cmd_sprint_capacity(opts):
    cfg = _require_config()
    count = opts.retros or sprint.RETRO_COUNT
    found = sprint.recent_metrics(cfg.root, cfg.data["docs"]["dir"], count)
    print(json.dumps(sprint.capacity(found, opts.days), indent=2))
    return 0


@cliargs.command("sprint-metrics")
def cmd_sprint_metrics(opts):
    cfg = _require_config()
    try:
        repo = json.loads(run.run(["gh", "repo", "view", "--json", "owner,name"]))
        found = usage.report(opts.dir, opts.since, f"{repo['owner']['login']}/{repo['name']}")
        metrics = sprint.sprint_metrics(cfg, run.run, opts.milestone, usage.total_weighted(found))
    except sprint.SnapshotError as error:
        print(f"baton: {error}", file=sys.stderr)
        return 1
    print(sprint.format_metrics(metrics))
    return 0


@cliargs.command("pr-state")
def cmd_pr_state(opts):
    cfg = _require_config()
    pr = json.loads(run.run(["gh", "pr", "view", opts.pr, "--json", sprint.PR_STATE_FIELDS]))
    number = pr.get("number") or (int(opts.pr) if opts.pr.isdigit() else None)
    if number:
        pr["comments"] = identity.mark_authors(run.run, number, pr.get("comments"))
    print(json.dumps(sprint.pr_state(pr, cfg, run.run, reviewers=_reviewers()), indent=2))
    return 0


@cliargs.command("verify")
def cmd_verify(opts):
    cfg = _require_config()
    owner, name = _repo_name().split("/", 1)
    worktree = os.path.abspath(opts.worktree)
    out = verify.verify(cfg, run.run, opts.pr, worktree, owner, name)
    exit_text = "-" if out["exit"] is None else str(out["exit"])
    print(f"verify {out['result']} commit={out['commit'][:7]} exit={exit_text} "
          f"duration={out['seconds']}s")
    print(f"log: {out['log']}")
    print("--- last 40 lines ---")
    tail = out["tail"]
    print(tail if isinstance(tail, str) else "\n".join(tail))
    return {"green": 0, "red": 1, "timeout": 3}[out["result"]]


@cliargs.command("sandbox")
def cmd_sandbox(opts):
    if opts.action == "run" and not opts.words:
        return cliargs.fail("sandbox")
    cfg = _require_config()
    repo = json.loads(run.run(["gh", "repo", "view", "--json", "nameWithOwner,defaultBranchRef"]))
    owner, name = repo["nameWithOwner"].split("/", 1)
    if opts.action == "check":
        state = sandbox.check(owner, name)
        print(state)
        return 0 if state == "configured" else 1
    seconds = cfg.data["verify"]["timeout_minutes"] * 60
    branch = repo["defaultBranchRef"]["name"]
    out = sandbox.run_in_sandbox(run.run, owner, name, branch, " ".join(opts.words), seconds)
    print(f"sandbox exit={out['exit']}")
    print(f"log: {out['log']}")
    print("--- last 40 lines ---")
    tail = out["tail"]
    print(tail if isinstance(tail, str) else "\n".join(tail))
    return out["exit"]


@cliargs.command("lock")
def cmd_lock(opts):
    ttl = float(opts.ttl_hours)
    cfg = _require_config()
    common = run.run(["git", "-C", str(cfg.root), "rev-parse", "--git-common-dir"]).strip()
    path = os.path.join(str(cfg.root), common, "baton-sprint.lock")
    if opts.action == "acquire":
        if sprint.lock_acquire(path, f"pid {os.getppid()}", ttl):
            print("lock acquired")
            return 0
        print("another sprint run holds the lock", file=sys.stderr)
        return 1
    sprint.lock_release(path)
    print("lock released")
    return 0


@cliargs.command("clean")
def cmd_clean(opts):
    _require_config()
    repo = json.loads(run.run(["gh", "repo", "view", "--json", "nameWithOwner,defaultBranchRef"]))
    owner, name = repo["nameWithOwner"].split("/", 1)
    logs_root = verify.logs_dir(owner, name, 0).parent

    def report(line):
        print(line, file=sys.stderr if line.startswith("cannot remove") else sys.stdout)

    _, _, failed = clean.clean_worktrees(run.run, repo["defaultBranchRef"]["name"], report)
    _, failed_logs = clean.clean_logs(run.run, str(logs_root), report)
    return 1 if failed or failed_logs else 0


@cliargs.command("identity")
def cmd_identity(opts):
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


@cliargs.command("push")
def cmd_push(opts):
    info = json.loads(run.run(["gh", "repo", "view", "--json", "nameWithOwner,defaultBranchRef"]))
    default = (info.get("defaultBranchRef") or {}).get("name", "")
    problem = identity.refspec_problem(opts.refspec, default, identity.ref_format_ok)
    if problem:
        print(f"baton push: refused {opts.refspec}: {problem}", file=sys.stderr)
        return 1
    if identity.pushes_to_origin():
        try:
            url = run.run(["git", "remote", "get-url", "--push", "origin"], cwd=os.getcwd()).strip()
            problem = identity.origin_problem(url, info["nameWithOwner"])
        except run.RunError:
            problem = "no origin remote"
        if problem:
            print(f"baton push: refused {opts.refspec}: {problem}", file=sys.stderr)
            return 1
    tier1 = not identity.pushes_to_origin()
    sha = ""
    if tier1:
        try:
            sha = run.run(["git", "rev-parse", "--verify", f"{opts.refspec.partition(':')[0]}^{{commit}}"],
                          cwd=os.getcwd()).strip()
        except run.RunError:
            sha = ""
    argv, env = identity.push_command(opts.refspec, info["nameWithOwner"])
    print(run.run(argv, cwd=os.getcwd(), env=env), end="")
    if tier1:
        branches.record_push(run.run, os.getcwd(), opts.refspec, sha)
    return 0


@cliargs.command("doctor")
def cmd_doctor(opts):
    cfg = config.load(os.getcwd())
    return doctor.main(_repo_name(), spike=cfg.data["spike"] if cfg is not None else None)


COMMANDS = {
    "doctor": cmd_doctor,
    "clean": cmd_clean,
    "sprint-metrics": cmd_sprint_metrics,
    "close": cmd_close,
    "answers": cmd_answers,
    "notify": cmd_notify,
    "open-questions": cmd_open_questions,
    "comment": cmd_comment,
    "exec": cmd_exec,
    "ready-check": cmd_ready_check,
    "config": cmd_config,
    "budget": cmd_budget,
    "compact-reminder": cmd_compact_reminder,
    "gate": cmd_gate,
    "hook": cmd_hook,
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
    "orchestrator-agent": cmd_orchestrator_agent,
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
