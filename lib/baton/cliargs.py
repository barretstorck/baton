"""Argparse declarations for every baton command.

The layer keeps today's behaviour: usage errors print the command's usage text
and exit 64, ``--help`` prints help to stdout and exits 0, and no
``SystemExit`` escapes.  Later tasks add one ``_declare_<command>`` per command
to ``build()``.
"""
from __future__ import annotations

import argparse
import functools
import sys
from typing import Callable

USAGE: dict[str, str] = {}


class Usage(Exception):
    """Raised instead of exiting; ``code`` is the exit status."""

    def __init__(self, code: int):
        super().__init__(code)
        self.code = code


class BatonParser(argparse.ArgumentParser):
    def __init__(self, *args, usage_text: str = "", lenient: bool = False,
                 **kwargs):
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)
        self.usage_text = usage_text
        self.lenient = lenient

    def parse_known_args(self, args=None, namespace=None):
        """A leaf parser refuses extra words itself, so the usage printed is
        its own and not its parent's."""
        opts, extras = super().parse_known_args(args, namespace)
        if extras and not self.lenient and not _has_subparsers(self):
            self.error("unrecognized arguments")
        return opts, extras

    def error(self, message):
        sys.stderr.write((self.usage_text or self.format_usage()).rstrip("\n")
                         + "\n")
        raise Usage(64)

    def exit(self, status=0, message=None):
        if message:
            sys.stderr.write(message)
        raise Usage(status)


def digits(text: str) -> int:
    if not text.isdigit():
        raise argparse.ArgumentTypeError(f"not a number: {text!r}")
    return int(text)


def _subparsers(parser: argparse.ArgumentParser) -> list[BatonParser]:
    found = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                found.append(sub)
                found.extend(_subparsers(sub))
    return found


def _value_options(parser: argparse.ArgumentParser) -> set[str]:
    names: set[str] = set()
    for p in [parser, *_subparsers(parser)]:
        for action in p._actions:
            if action.option_strings and action.nargs != 0:
                names.update(o for o in action.option_strings
                             if o.startswith("--"))
    return names


def _has_own_options(parser: argparse.ArgumentParser) -> bool:
    return any(a.option_strings and "-h" not in a.option_strings
               for a in parser._actions)


def _has_subparsers(parser: argparse.ArgumentParser) -> bool:
    return any(isinstance(a, argparse._SubParsersAction)
               for a in parser._actions)


def _normalise(parser: BatonParser, argv: list[str]) -> list[str]:
    """Make today's hand parsing work: a value option takes the next word
    whatever it looks like, and a positional-only command takes every word as
    a value."""
    if (not _has_own_options(parser) and not _has_subparsers(parser)
            and argv and argv not in (["-h"], ["--help"])):
        return ["--", *argv]
    values = _value_options(parser)
    out: list[str] = []
    i = 0
    while i < len(argv):
        word = argv[i]
        if word == "--":
            out.extend(argv[i:])
            break
        if word in values and i + 1 < len(argv):
            out.append(f"{word}={argv[i + 1]}")
            i += 2
            continue
        out.append(word)
        i += 1
    return out


def parse_with(parser: BatonParser, argv: list[str]) -> argparse.Namespace:
    argv = _normalise(parser, list(argv))
    if parser.lenient:
        opts, _ = parser.parse_known_args(argv)
        return opts
    return parser.parse_args(argv)


def _add(subs, name: str, usage: str, lenient: bool = False) -> BatonParser:
    USAGE[name] = usage
    return subs.add_parser(name, usage_text=usage, lenient=lenient)


def _declare_config(subs):
    _add(subs, "config", "usage: baton config", lenient=True)


def _declare_budget(subs):
    _add(subs, "budget", "usage: baton budget", lenient=True)


def _declare_gate(subs):
    _add(subs, "gate", "usage: baton gate", lenient=True)


def _declare_install_hooks(subs):
    _add(subs, "install-hooks", "usage: baton install-hooks", lenient=True)


def _declare_labels(subs):
    p = _add(subs, "labels", "usage: baton labels [--create]", lenient=True)
    p.add_argument("--create", action="store_true")


def _declare_set_status(subs):
    p = _add(subs, "set-status",
             "usage: baton set-status <issue-number> <status:name>")
    p.add_argument("issue", type=digits)
    p.add_argument("status")


def _declare_set_group(subs, group: str):
    p = _add(subs, f"set-{group}", f"usage: baton set-{group} <issue-number> <1-4>")
    p.add_argument("issue", type=digits)
    p.add_argument("value", type=digits)


def _declare_merge_pr(subs):
    p = _add(subs, "merge-pr", "usage: baton merge-pr <pull-request-number>")
    p.add_argument("pr", type=digits)


def _declare_comment(subs):
    p = _add(subs, "comment", "usage: baton comment <issue|pr> <number> <role> "
             "<kind> <body-file | ->")
    p.add_argument("target")
    p.add_argument("number", type=digits)
    p.add_argument("role")
    p.add_argument("kind")
    p.add_argument("body")


def _declare_answers(subs):
    p = _add(subs, "answers", "usage: baton answers <issue-number>")
    p.add_argument("issue", type=digits)


def _declare_open_questions(subs):
    _add(subs, "open-questions", "usage: baton open-questions")


def _declare_ready_check(subs):
    p = _add(subs, "ready-check", "usage: baton ready-check <issue-number>")
    p.add_argument("issue", type=digits)


def _declare_sprint_status(subs):
    p = _add(subs, "sprint-status", "usage: baton sprint-status [milestone]",
             lenient=True)
    p.add_argument("milestone", nargs="?")


def _declare_sprint_order(subs):
    p = _add(subs, "sprint-order", "usage: baton sprint-order <issue>...")
    p.add_argument("issues", nargs="+", type=digits)


def _declare_pr_state(subs):
    p = _add(subs, "pr-state", "usage: baton pr-state <PR>")
    p.add_argument("pr")


def _declare_verify(subs):
    p = _add(subs, "verify", "usage: baton verify <PR> <worktree>")
    p.add_argument("pr", type=digits)
    p.add_argument("worktree")


def _declare_clean(subs):
    _add(subs, "clean", "usage: baton clean", lenient=True)


def _declare_identity(subs):
    p = _add(subs, "identity", "usage: baton identity worktree")
    p.add_argument("action", choices=["worktree"])


def _declare_push(subs):
    p = _add(subs, "push", "usage: baton push <refspec>")
    p.add_argument("refspec")


def _declare_doctor(subs):
    _add(subs, "doctor", "usage: baton doctor")


def nonempty(text: str) -> str:
    if not text:
        raise argparse.ArgumentTypeError("empty")
    return text


_ISSUE_CREATE = ("usage: baton issue create --title <t> [--label <l>]... "
                 "[--milestone <title>] <body-file | ->")
_EDIT = ("usage: baton {t} edit <N> [--title <t>] [--body-file <file | ->] "
         "[--add-label <l>]... [--remove-label <l>]... "
         "[--milestone <title> | --remove-milestone]")
_PR_CREATE = "usage: baton pr create --title <t> [--draft] <body-file | ->"
_PR_READY = "usage: baton pr ready <N> [--undo]"
_MS_CREATE = ("usage: baton milestone create --title <t> [--description <d>] "
              "[--due-on <ISO date>]")
_MS_EDIT = ("usage: baton milestone edit <number | title> [--title <t>] "
            "[--description <d>] [--due-on <date>] [--state open|closed]")
_ISSUE_MISSED = ("usage: baton issue missed <issue number> "
                 "<milestone title or number>")
_ISSUE_READ = "usage: baton issue read <issue>"


def _action(subs, key: str, usage: str) -> BatonParser:
    """A nested action parser; ``key`` is "<command> <action>"."""
    USAGE[key] = usage
    return subs.add_parser(key.split()[1], usage_text=usage)


def _actions(subs, name: str, usage: str) -> argparse._SubParsersAction:
    parser = _add(subs, name, usage)
    return parser.add_subparsers(dest="action", required=True,
                                 parser_class=BatonParser)


def _declare_edit(actions, target: str):
    p = _action(actions, f"{target} edit", _EDIT.format(t=target))
    p.add_argument("number", type=digits)
    p.add_argument("--title")
    p.add_argument("--body-file", dest="body_source")
    p.add_argument("--add-label", dest="add_labels", action="append",
                   default=[])
    p.add_argument("--remove-label", dest="remove_labels", action="append",
                   default=[])
    group = p.add_mutually_exclusive_group()
    group.add_argument("--milestone")
    group.add_argument("--remove-milestone", action="store_true")


def _declare_issue(subs):
    actions = _actions(subs, "issue", _ISSUE_CREATE + "\n" + _EDIT.format(t="issue"))
    p = _action(actions, "issue create", _ISSUE_CREATE)
    p.add_argument("--title", type=nonempty, required=True)
    p.add_argument("--label", dest="labels", action="append", default=[])
    p.add_argument("--milestone")
    p.add_argument("body_source")
    _declare_edit(actions, "issue")
    _action(actions, "issue read", _ISSUE_READ).add_argument("number", type=digits)
    p = _action(actions, "issue missed", _ISSUE_MISSED)
    p.add_argument("number", type=digits)
    p.add_argument("milestone")


def _declare_pr(subs):
    actions = _actions(subs, "pr", "\n".join(
        [_PR_CREATE, _EDIT.format(t="pr"), _PR_READY]))
    p = _action(actions, "pr create", _PR_CREATE)
    p.add_argument("--title", type=nonempty, required=True)
    p.add_argument("--draft", action="store_true")
    p.add_argument("body_source")
    _declare_edit(actions, "pr")
    p = _action(actions, "pr ready", _PR_READY)
    p.add_argument("number", type=digits)
    p.add_argument("--undo", action="store_true")


def _declare_milestone(subs):
    actions = _actions(subs, "milestone", _MS_CREATE + "\n" + _MS_EDIT)
    p = _action(actions, "milestone create", _MS_CREATE)
    p.add_argument("--title", type=nonempty, required=True)
    p.add_argument("--description")
    p.add_argument("--due-on")
    p = _action(actions, "milestone edit", _MS_EDIT)
    p.add_argument("ref")
    p.add_argument("--title")
    p.add_argument("--description")
    p.add_argument("--due-on")
    p.add_argument("--state", choices=["open", "closed"])


def _nested(subs, name: str, usage: str):
    """A command with actions; wrong actions print the command's usage."""
    parser = _add(subs, name, usage)
    return parser.add_subparsers(dest="action", required=True,
                                 parser_class=BatonParser)


def _leaf(actions, name: str, usage: str) -> BatonParser:
    return actions.add_parser(name, usage_text=usage)


def _declare_state_change(subs, name: str):
    usage = f"usage: baton {name} <issue|pr> <number> <role> <reason-file | ->"
    if name == "close":
        usage += (" [--reason spam|duplicate|support|wont-do|completed]"
                  " [--duplicate-of <N>] [--lock]")
    p = _add(subs, name, usage)
    if name == "close":
        p.add_argument("--reason", dest="close_reason")
        p.add_argument("--duplicate-of", dest="duplicate_of", type=digits)
        p.add_argument("--lock", action="store_true")
    p.add_argument("target")
    p.add_argument("number", type=digits)
    p.add_argument("role")
    p.add_argument("reason_file")


_REVIEW_USAGE = ("usage: baton review state <pr> | baton review post <pr> "
                 "<approve|changes|blocked|replan> <full commit sha> "
                 "<body-file | ->")


def _declare_review(subs):
    actions = _nested(subs, "review", _REVIEW_USAGE)
    _leaf(actions, "state", _REVIEW_USAGE).add_argument("pr", type=digits)
    p = _leaf(actions, "post", _REVIEW_USAGE)
    p.add_argument("pr", type=digits)
    p.add_argument("verdict")
    p.add_argument("commit")
    p.add_argument("body")


def _declare_tasks(subs):
    p = _add(subs, "tasks", "usage: baton tasks <issue|pr> <number>")
    p.add_argument("target", choices=["issue", "pr"])
    p.add_argument("number", type=digits)


def _declare_task(subs):
    usage = ("usage: baton task done <issue|pr> <number> <task> "
             "[--as-built <text>]")
    actions = _nested(subs, "task", usage)
    p = _leaf(actions, "done", usage)
    p.add_argument("--as-built", dest="as_built")
    p.add_argument("target", choices=["issue", "pr"])
    p.add_argument("number", type=digits)
    p.add_argument("task")


_PLAN_USAGE = (
    "usage: baton plan replace <issue> <from-task> <file | -> [--drop]\n"
    "       baton plan insert <issue> <before-task> <file | ->\n"
    "       baton plan set <issue> <file | ->\n"
    "       baton plan against <issue> <sha>\n"
    "       baton plan commit <issue> <path> <file | ->   (deprecated, does nothing)\n"
    "       baton plan pin <issue>   (deprecated, does nothing)\n"
    "       baton plan link <issue> ...   (deprecated, does nothing)\n"
    "       baton plan migrate <issue>\n")


def _declare_plan(subs):
    actions = _nested(subs, "plan", _PLAN_USAGE)
    for name in ("replace", "insert"):
        p = _leaf(actions, name, _PLAN_USAGE)
        p.add_argument("issue", type=digits)
        p.add_argument("task", type=digits)
        p.add_argument("file")
        p.add_argument("--drop", action="store_true")
    p = _leaf(actions, "set", _PLAN_USAGE)
    p.add_argument("issue", type=digits)
    p.add_argument("file")
    p = _leaf(actions, "against", _PLAN_USAGE)
    p.add_argument("issue", type=digits)
    p.add_argument("sha")
    for name in ("commit", "pin", "link"):
        p = _leaf(actions, name, _PLAN_USAGE)
        p.add_argument("issue", type=digits)
        p.add_argument("rest", nargs=argparse.REMAINDER)
    _leaf(actions, "migrate", _PLAN_USAGE).add_argument("issue", type=digits)


def _declare_notify(subs):
    from . import comment
    p = _add(subs, "notify",
             "usage: baton notify <issue> <cause> <text-file | ->  |  "
             "baton notify --rules\n"
             f"causes: {', '.join(comment.NOTIFY_CAUSES)}")
    p.add_argument("--rules", action="store_true")
    p.add_argument("issue", nargs="?", type=digits)
    p.add_argument("cause", nargs="?", choices=list(comment.NOTIFY_CAUSES))
    p.add_argument("file", nargs="?")


def _declare_triage(subs):
    usage = ("usage: baton triage apply <issue> <decision.json>  |  "
             "baton triage queue")
    actions = _nested(subs, "triage", usage)
    p = _leaf(actions, "apply", usage)
    p.add_argument("issue", type=digits)
    p.add_argument("decision")
    _leaf(actions, "queue", usage)


def _declare_git_hook(subs):
    usage = ("usage: baton git-hook pre-commit | commit-msg <message-file> | "
             "pre-push")
    actions = _nested(subs, "git-hook", usage)
    _leaf(actions, "pre-commit", usage)
    _leaf(actions, "commit-msg", usage).add_argument("message_file")
    _leaf(actions, "pre-push", usage)


@functools.lru_cache(maxsize=None)
def build() -> BatonParser:
    top = BatonParser(prog="baton", usage_text="usage: baton <command> [args]")
    subs = top.add_subparsers(dest="command", parser_class=BatonParser)
    for declare in (_declare_config, _declare_budget, _declare_gate,
                    _declare_install_hooks, _declare_labels, _declare_set_status,
                    _declare_merge_pr, _declare_comment, _declare_answers,
                    _declare_open_questions, _declare_ready_check,
                    _declare_sprint_status, _declare_sprint_order,
                    _declare_pr_state, _declare_verify, _declare_clean,
                    _declare_identity, _declare_push, _declare_doctor,
                    _declare_issue, _declare_pr, _declare_milestone,
                    _declare_review, _declare_tasks, _declare_task,
                    _declare_plan, _declare_notify, _declare_triage,
                    _declare_git_hook):
        declare(subs)
    _declare_state_change(subs, "close")
    _declare_state_change(subs, "reopen")
    _declare_set_group(subs, "priority")
    _declare_set_group(subs, "severity")
    return top


def _commands() -> dict[str, BatonParser]:
    for action in build()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


def parse(command: str, argv: list[str]) -> argparse.Namespace:
    return parse_with(_commands()[command], argv)


def command(name: str) -> Callable:
    def decorate(handler: Callable) -> Callable:
        @functools.wraps(handler)
        def wrapper(args: list[str]) -> int:
            try:
                opts = parse(name, args)
            except Usage as exc:
                return exc.code
            return handler(opts)
        return wrapper
    return decorate


def fail(name: str) -> int:
    print(USAGE[name], file=sys.stderr)
    return 64
