import os
import subprocess


class RunError(Exception):
    pass


def run(argv, cwd=None, input=None, env=None):
    child_env = {**os.environ, **env} if env else None
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                          errors="replace", input=input, env=child_env)
    if proc.returncode != 0:
        raise RunError(f"{' '.join(argv[:3])} failed: {proc.stderr.strip()}")
    return proc.stdout
