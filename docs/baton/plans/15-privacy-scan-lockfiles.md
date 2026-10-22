## Pattern

`lib/baton/scan.py` holds the patterns and `scan_text(text, terms, allow)`; `lib/baton/githooks.py`
`_check` joins all added lines into one text and scans it once. Tests for the hooks build a real
repo with `make_repo`, `write` and `git` in `tests/test_githooks.py` (`PreCommitTest`,
`PrePushTest`); pure scan tests are in `tests/test_scan.py` (`ScanTest`). Only the two git hooks
change: `baton comment`, `plan replace` and the gate keep calling `scan.scan_text` as today.

## Verification

`PYTHONPATH=lib python3 -m unittest discover -s tests` (the command CI runs).

## Design notes

- Files are matched by basename with `fnmatch`: `uv.lock`, `poetry.lock`, `pdm.lock`,
  `Pipfile.lock`, `requirements*.txt`, `requirements*.in`, `constraints*.txt`, `pylock*.toml`.
- Only the kind `private-ip` is skipped for those files; email, host-path, internal-domain and
  private terms are still scanned on every added line of them.
- New file names and the commit message are always scanned in full, so the exemption never
  applies to them.
- The per-file split reads the `diff --git` header, then the `+++ ` line before the first `@@`.
  Do not treat a `+++ ` line inside a hunk as a header (an added line may start with `++ `).

## Plan

Written against: dc7d89f8e49ade16665665d7312fe65a2c9ac435

- [x] **1. Scan helper that can skip a kind**
  - Files: `lib/baton/scan.py`, `tests/test_scan.py`
  - Uses: `scan_text(text, terms, allow)` (`lib/baton/scan.py:51`)
  - Provides: `scan.PY_DEPENDENCY_FILES` (tuple of the fnmatch patterns above), `scan.skips_private_ip(path) -> bool` (basename match, any directory), and `scan_text(text, terms, allow, skip=())` where `skip` is a collection of kinds to leave out (default behaviour unchanged)
  - Check: `PYTHONPATH=lib python3 -m unittest tests.test_scan`

- [ ] **2. Split the patch per file and apply the exemption in the hooks**
  - Files: `lib/baton/githooks.py`, `tests/test_githooks.py`
  - Uses: `scan.skips_private_ip`, `scan.scan_text(..., skip=...)` (task 1); `added_text(patch)` (`lib/baton/githooks.py:19`, keep it working, its test stays); `_check(cfg, patch, new_files, terms, messages="")` (`lib/baton/githooks.py:38`)
  - Provides: `githooks.added_by_file(patch) -> list[tuple[str, str]]` (path of the new side, added lines joined by newline; deleted files and mode-only changes give no entry); `_check` scans each file's added lines with `skip={"private-ip"}` when `skips_private_ip(path)`, and scans messages and new file names in full
  - Check: `PYTHONPATH=lib python3 -m unittest tests.test_githooks`
  - Tests to add, using the existing `make_repo` (empty `privacy_allow`): a four-part version starting with `10.` (built in the test from parts, e.g. `".".join(["10", "1", "2", "3"])`) added to `uv.lock` and to `sub/requirements-dev.txt` passes `pre_commit`; the same line in `a.txt` is reported as `private-ip`; an email, a host path under the home or Users directory and a name ending in the internal-domain suffix `.internal` (both built from parts in the test so the test file itself passes the scan) added to `uv.lock` are each still reported; `pre_push` with a new branch containing the `uv.lock` change passes (model on `PrePushTest.test_new_branch_scans_commits_not_on_remote`); `added_by_file` unit test with two files in one patch, including a `+++ ` line inside a hunk

- [ ] **3. Document the exempt files**
  - Files: `README.md`, `templates/brief.md`
  - Uses: `scan.PY_DEPENDENCY_FILES` (task 1)
  - Provides: README sentence (near the rules list, `README.md:107`) saying the git hooks do not report `private-ip` in those basenames while other kinds are still checked; one-line comment next to `privacy_allow` in `templates/brief.md:37` pointing to it
  - Check: `PYTHONPATH=lib python3 -m unittest discover -s tests` (full suite; `tests/test_template.py` reads the brief template)
