---
developer:
  model: sonnet
  max_parallel: 1
  effort: medium
reviewer:
  model: sonnet
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
merge:
  allow_no_ci: false          # true only for repositories with no CI at all
protected_paths: [".github/", ".baton/", ".claude/"]
fixtures_paths: []            # the only places new image files may be added
privacy_allow: []             # literal strings the privacy scan should ignore
budgets: {}
---
# Baton brief

Rules for agents working in this repository. Keep private details (hosts, paths,
credentials) out of this file; they belong in the local brief outside the repository.

## Verification

The exact command that must pass before a pull request is opened:

    (fill in, for example: scripts/test.sh)

## Definition of done

- The verification command passes.
- New behaviour has tests.
- (add project rules)

## Conventions

- (branching, commit style, documentation rules)
