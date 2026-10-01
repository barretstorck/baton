import json
import os
import sys

from . import config, gate, githooks, scan


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


COMMANDS = {
    "config": cmd_config,
    "gate": cmd_gate,
    "git-hook": cmd_git_hook,
    "install-hooks": cmd_install_hooks,
}


def main(argv):
    if not argv or argv[0] not in COMMANDS:
        print("usage: baton <" + "|".join(sorted(COMMANDS)) + "> [args]", file=sys.stderr)
        return 64
    try:
        return COMMANDS[argv[0]](argv[1:])
    except config.ConfigError as error:
        print(f"baton: {error}", file=sys.stderr)
        return 2
