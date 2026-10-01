import json
import os
import sys

from . import config, gate


def cmd_config(args):
    cfg = config.load(os.getcwd())
    if cfg is None:
        print("not a Baton repository: .baton/brief.md not found", file=sys.stderr)
        return 1
    print(json.dumps(cfg.data, indent=2))
    return 0


def cmd_gate(args):
    return gate.main(sys.stdin.read())


COMMANDS = {"config": cmd_config, "gate": cmd_gate}


def main(argv):
    if not argv or argv[0] not in COMMANDS:
        print("usage: baton <" + "|".join(sorted(COMMANDS)) + "> [args]", file=sys.stderr)
        return 64
    try:
        return COMMANDS[argv[0]](argv[1:])
    except config.ConfigError as error:
        print(f"baton: {error}", file=sys.stderr)
        return 2
