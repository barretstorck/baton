## Pattern

The local brief `~/.baton/<owner>-<repo>/brief.local.md` is created by `skills/init/SKILL.md` step 5
(line 31) and read by developers in `skills/develop/SKILL.md:31`. Project-level template files live
in `templates/` and are checked for by `tests/test_template.py`. Worktree cleanup is
`merge._after_merge` (`lib/baton/merge.py:169`) with tests in `tests/test_merge.py` (dirty-worktree
tests at lines 310 and 340, `FakeRunner` from `tests/fakes.py`).

## Verification

`PYTHONPATH=lib python3 -m unittest discover -s tests -v` (what CI runs).

## Design notes

- The way to reach local data is the local brief, which lives outside every checkout and so is
  readable from any worktree with an absolute path. No environment variables: nothing sets them in
  a subagent's shell.
- Cleanup already removes a clean worktree with `git worktree remove --force`, deleting an ignored
  `.env` with it. The gap is a worktree that is *kept* (uncommitted changes), so those get a
  warning naming the secret file. Baton does not delete files from a worktree it keeps.
- Only worktrees `merge-pr` already finds (the branch's and its tasks') are covered; spike
  worktrees are not.

## Plan

Written against: dc7d89f8e49ade16665665d7312fe65a2c9ac435

- [ ] **1. A local brief template**
  - Files: `templates/brief.local.md` (new), `tests/test_template.py`
  - Provides: a template with headings `## Hosts`, `## Paths`, `## Local data`, `## Credentials`. Under `## Local data`, a comment says: one `NAME: /absolute/path` per line, data agents may read in place, read-only, never copy; under `## Credentials`: agents never read or copy this section. No real values. Add a test that the template exists and contains `## Local data`.
  - Check: `PYTHONPATH=lib python3 -m unittest tests.test_template -v`

- [ ] **2. `baton init` creates the local brief from it**
  - Files: `skills/init/SKILL.md`
  - Uses: the template from task 1
  - Provides: step 5 (line 31) says to copy `${CLAUDE_PLUGIN_ROOT}/templates/brief.local.md` to `~/.baton/<owner>-<repo>/brief.local.md` when missing, replacing the sentence that lists the headings, and to ask the maintainer which local data paths (test footage, reference images) agents may use and write them under `## Local data`.
  - Check: `rg -n "Local data" skills/init/SKILL.md` matches

- [ ] **3. Developer rules for local data and secrets**
  - Files: `skills/develop/SKILL.md`, `agents/baton-developer.md`, `tests/test_template.py`
  - Uses: `skills/develop/SKILL.md:31` (the "What to trust" bullet about the local brief)
  - Provides: the bullet says to read the local brief's Hosts, Paths and Local data sections, never its Credentials section; a new paragraph says local data is reached only through the absolute paths listed under `## Local data`, never by copying or linking `.env` or any other secret file (API keys, tokens) into a worktree, and that if needed data is not listed or not reachable the developer returns `QUESTION` (return form in the skill's "## Return"); a `## Never` bullet repeats the no-copy rule; one sentence in `agents/baton-developer.md`. Add a test that the skill contains `Local data` and `.env`.
  - Check: `PYTHONPATH=lib python3 -m unittest tests.test_template -v`

- [ ] **4. merge-pr warns about a secret file in a kept worktree**
  - Files: `lib/baton/merge.py`, `tests/test_merge.py`
  - Uses: `_after_merge(pr, cfg, runner, notes)` (`lib/baton/merge.py:169`), its two "kept ... uncommitted changes" notes (lines 183 and 191)
  - Provides: `def _secret_files(path) -> list[str]`, the names of `.env` and `.env.*` files directly in `path`, excluding names ending `.example`, `.sample` or `.template`; when a branch or task worktree is kept, its note also says `it holds <names>; delete it by hand`.
  - Check: `PYTHONPATH=lib python3 -m unittest tests.test_merge -v`, with new tests that use a temporary directory as the worktree path (replace `/wt/issue-4` in the fake replies) containing `.env` and `.env.example`: the note names `.env` only; no note mention without it.

- [ ] **5. Full suite**
  - Files: none
  - Check: `PYTHONPATH=lib python3 -m unittest discover -s tests -v`
