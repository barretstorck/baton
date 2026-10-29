---
triager:
  model: haiku
  effort: medium
refiner:
  model: opus
  effort: medium
researcher:
  model: opus
  effort: medium
developer:
  model: sonnet
  max_parallel: 1
  effort: medium
reviewer:
  model: opus
  effort: medium
review:
  required: true              # false: merge-pr does not ask for a reviewer's approval
  max_rounds: 2               # review rounds before a disagreement goes to the maintainer
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
    priority:1: "priority:1"
    priority:2: "priority:2"
    priority:3: "priority:3"
    priority:4: "priority:4"
    severity:1: "severity:1"
    severity:2: "severity:2"
    severity:3: "severity:3"
    severity:4: "severity:4"
merge:
  allow_no_ci: false          # true only for repositories with no CI at all; the reviewer then starts without waiting for CI
verify:
  command: ""                 # the full check the reviewer runs on the pull request head, e.g. "cd {worktree} && make test"; {worktree} is the quoted worktree path; empty: no local verification
  timeout_minutes: 10         # the run is killed after this many minutes and counts as a timeout
docs:
  dir: docs/baton             # where plans and retrospectives live: relative, no "..", not protected
maintainers: []              # logins whose comments count as the maintainer's; empty: the repository owner
protected_paths: [".github/", ".baton/", ".claude/"]
fixtures_paths: []            # the only places new image files may be added
privacy_allow: []             # literal strings the privacy scan should ignore
                              # (Python dependency files are already exempt from private-ip: see README)
forbid_added: {}              # path glob: regex; a commit or pull request adding a matching line to a matching file is refused (see Conventions)
budgets: {}
work_mix: {}                  # optional target share of work by type, shown by sprint-plan, never enforced; e.g. feature: 60, maintenance: 30, bug: 10 (must add up to 100; empty: no target)
---
# Baton brief

Rules for agents working in this repository. Keep private details (hosts, paths,
credentials) out of this file; they belong in the local brief outside the repository.

## Verification

The exact command that must pass before a pull request is opened:

    (fill in, for example: scripts/test.sh)

## Collect-only check

Optional. A command that imports every test module without running it, for example
`pytest --co -q` (fill in, or delete this section):

    (fill in, or leave empty)

Architecture and import-rule tests, if the project has any (optional):

    (fill in, or leave empty)

The refiner puts the collect-only command into every plan task's `Check:`, and the whole `verify`
command into tasks that delete or move modules.

## Definition of done

- The verification command passes.
- New behaviour has tests.
- (add project rules)

## Conventions

- (branching, commit style, documentation rules)

Forbidden diff patterns go in the front matter as `forbid_added`, a mapping from a path glob to a
regular expression matched against each added line of the files the glob matches. Quote the regex.
For example, to refuse a new ruff per-file ignore for a `src/` module:

    forbid_added:
      "pyproject.toml": '^\s*"src/[^"]*"\s*=\s*\['

A plan task can allow a glob with a line `Allows: forbid_added "<glob>"`.

Local verification and CI are two settings, `verify.command` and `merge.allow_no_ci`, which give four cases:

| `verify.command` | `merge.allow_no_ci` | What a merge needs |
| --- | --- | --- |
| set | false | a green local verification recorded in the review, and green CI |
| set | true | a green local verification recorded in the review; no CI exists |
| empty | false | green CI only |
| empty | true | neither (not recommended) |

With `review.required: false` no review carries the verification result, so `verify` reads `none` and
`baton merge-pr` does not check it. A repository that wants verification enforced keeps reviews on.
