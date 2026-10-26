---
developer:
  model: sonnet
  max_parallel: 2
reviewer:
  model: sonnet
review:
  required: true              # false: merge-pr does not ask for a reviewer's approval
  max_rounds: 10              # review rounds before a disagreement goes to the maintainer
refine:
  may_mark_ready: true        # false: refine proposes, the maintainer applies the label
labels:
  auto_create: true           # false: a missing label stops the run
  map:                        # Baton's name: the label this repository uses
    status:refinement: "status:refinement"
    status:ready: "status:ready"
    status:in-dev: "status:in-dev"
    status:blocked: "status:blocked"
    status:in-review: "status:in-review"
    status:verification: "status:verification"
    status:complete: "status:complete"
    type:feature: "type:feature"
    type:bug: "type:bug"
    type:maintenance: "type:maintenance"
    type:spike: "type:spike"
    deliverable: "deliverable"
    stretch: "stretch"
    broken-default-branch: "broken-default-branch"
    needs-maintainer: "needs-maintainer"
merge:
  allow_no_ci: false          # true only for repositories with no CI at all
protected_paths: [".github/", ".baton/", ".claude/", "hooks/", "lib/baton/gate.py", "lib/baton/scan.py", "lib/baton/githooks.py"]
fixtures_paths: []            # the only places new image files may be added
privacy_allow: []             # literal strings the privacy scan should ignore
budgets: {}
---
# Baton brief

Rules for agents working in this repository. Keep private details (hosts, paths,
credentials) out of this file; they belong in the local brief outside the repository.

This repository is Baton itself: a Claude Code plugin (skills, agents, hooks) plus the
`baton` command-line tool in `lib/baton/`.

## Verification

The exact command that must pass before a pull request is opened:

    PYTHONPATH=lib python3 -m unittest discover -s tests

CI (`.github/workflows/ci.yml`) runs the same command on pull requests.

## Definition of done

- The verification command passes.
- New behaviour has tests in `tests/`, using `tests/fakes.py` `FakeRunner` instead of real
  `gh` or `git` calls.
- Skills, agents, `references/github.md` and `README.md` describe any changed command or flow.
- A release bumps `version` in `.claude-plugin/plugin.json`.

## Conventions

- Python is standard library only (no PyYAML or other packages); it must run with `python3` alone.
- Pure text functions with thin `runner`-based wrappers, as in `lib/baton/checklist.py`.
- Commit subjects are short sentences in the imperative or present tense, no prefixes
  (for example "Add baton usage: quota use per issue and role").
- Skill and agent text is plain, direct English: short sentences, exact commands.
- Designs live in `docs/superpowers/specs/`, implementation plans in `docs/superpowers/plans/`.
- Nothing private in any file: no hostnames, IP addresses, absolute home paths or people's names.
- The publish gate, git hooks and privacy scan (`hooks/`, `lib/baton/gate.py`,
  `lib/baton/scan.py`, `lib/baton/githooks.py`) are protected: changes need the maintainer's
  `/baton approve`.
