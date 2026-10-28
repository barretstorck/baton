## Developer Plan

Written against: (full commit sha of the default branch the plan was written from)

### Verification

The exact command that proves it, or "repo default".

### Pattern

The existing pattern to follow, with file names.

### Design notes

Choices and reasons that do not belong in the issue.

- [ ] Task 1: One unit of work with its own check
  <details>
  <summary>Click to see plan details</summary>

  - Files: `path/one` (new), `path/two`, `path/old` (deleted), `path/moved` (moved to `path/new`)
  - Uses: the exact signatures this task relies on, with `file:line`
  - Provides: the exact signatures later tasks rely on
  - Provides (as built): (optional; the developer adds it under Provides only when the code differs from it, and keeps the original line)
  - Port from: `path:first-last` (when porting; leave out otherwise)
  - Check: the test or subset that proves this task done, a build, or "none"

  </details>

(Optional, on its own line in a task's details: `Allows: forbid_added "<glob>"` lets that task add lines the brief's `forbid_added` rule for `<glob>` otherwise refuses. A task that is risky, or whose check is slow, says so in a note in its details.)

(A task whose Files line marks a path `(deleted)` or `(moved to <path>)`, or that adds a public name to a package `__init__`, uses the whole-suite check described in the refine skill as its `Check:`.)

(Not for spikes. This section is written into the issue body by `baton plan set`. A brief holds no heading of level 1 or 2. Tasks run in order, one developer each. No code here; the developer writes it.)
