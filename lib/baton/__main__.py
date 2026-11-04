import sys

if sys.argv[1:2] == ["hook"]:
    # Fast path: the hook runs on every tool call, so skip importing every command module.
    from . import hook

    sys.exit(hook.main(sys.stdin.read()))

from . import cli

sys.exit(cli.main(sys.argv[1:]))
