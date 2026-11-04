# Default branch red, pending or none

Read this when `default_branch.state` is `red`, `pending` or `none` (`skills/sprint/SKILL.md`, step 1 of a pass).

If `default_branch.state` is `red`, the repair comes before everything else
([GitLab: broken master](https://handbook.gitlab.com/handbook/engineering/workflow/#broken-master)).

- If `repair_issue` is null: create an issue describing the failure (failing job, first bad
  commit, a short log excerpt with private details replaced), with labels
  `broken-default-branch`, `deliverable` and `type:bug`, in the sprint milestone. Then dispatch
  `baton:baton-refiner` with `Refine issues <N>.` to diagnose it and write its plan.
- Dispatch a developer to the repair issue at once, even if that exceeds the parallel limit by one.
- Until the branch is green again: dispatch nothing else and merge nothing else. Developers
  already running may continue; their pull requests wait.
- If the cause is a pull request you just merged, reverting it is the preferred repair.

`pending` or `none` is not red; carry on, but do not mark anything `status:complete` on `pending`.
`none` usually means CI runs only on pull requests; a red default branch then shows up as
pull requests failing on code they did not touch. When two or more do, treat it as red.
