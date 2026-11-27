"""The forge a repository lives on; GitHub is the only backend for now."""
from .. import run
from .github import GitHub


def for_repo(cwd=None, runner=None):
    """The backend for this repository. `cwd` is accepted and unused for now."""
    return GitHub(runner or run.run)
