import subprocess


class RunError(Exception):
    pass


def run(argv, cwd=None):
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RunError(f"{' '.join(argv[:3])} failed: {proc.stderr.strip()}")
    return proc.stdout
