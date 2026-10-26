## Pattern

The existing pattern to follow, with file names.

## Verification

The exact command that proves it, or "repo default".

## Design notes

Choices and reasons that do not belong in the issue.

## Plan

Written against: (full commit sha of the default branch the plan was written from)

- [ ] **1. One unit of work with its own check**
  - Files: `path/one` (new), `path/two`, `path/old` (deleted), `path/moved` (moved to `path/new`)
  - Uses: the exact signatures this task relies on, with `file:line`
  - Provides: the exact signatures later tasks rely on
  - Provides (as built): (optional; the developer adds it under Provides only when the code differs from it, and keeps the original line)
  - Port from: `path:first-last` (when porting; leave out otherwise)
  - Check: the test or subset that proves this task done, a build, or "none"

(Optional, on its own line in a task: `Allows: forbid_added "<glob>"` lets that task add lines the brief's `forbid_added` rule for `<glob>` otherwise refuses.)

(A task whose Files line marks a path `(deleted)` or `(moved to <path>)`, or that adds a public name to a package `__init__`, uses the whole-suite check described in the refine skill as its `Check:`.)

(Not for spikes. This file is committed to `refs/plans/<N>` by `baton plan commit`, not to a branch. Tasks run in order, one developer each. No code here; the developer writes it.)
