---
name: init
description: Use when setting up a repository to work with Baton sprints, when .baton/brief.md is missing, or when Baton reports missing labels or git hooks.
---

# Baton init

Prepare the current repository for Baton. Work through every step; do not skip one because it
looks done. Workflow adapted from the
[GitLab Engineering Workflow](https://handbook.gitlab.com/handbook/engineering/workflow/).

## Steps

1. **Confirm the repository.** Run `gh repo view --json nameWithOwner,visibility,defaultBranchRef`.
   Tell the maintainer which repository will be set up and stop if it is not the one they meant.

2. **Write the repo brief.** If `.baton/brief.md` does not exist, copy
   `${CLAUDE_PLUGIN_ROOT}/templates/brief.md` to `.baton/brief.md`. Then fill in the body from the
   repository itself: find the test command (CI workflow, `Makefile`, `package.json`,
   `scripts/`), and the conventions in `AGENTS.md`, `CLAUDE.md` or `CONTRIBUTING.md`. Ask the
   maintainer only for what the repository does not show.

3. **Map existing labels.** Run `gh label list --limit 500 --json name,description`. If the
   repository already has labels that mean the same as a Baton label (for example `In Progress`
   for `status:in-dev`), propose the mapping to the maintainer and write the agreed names into
   `labels.map`. Never rename or delete an existing label.

4. **Create missing labels.** Run `baton labels`. With `labels.auto_create: false`, show the
   maintainer the missing list and run `baton labels --create` only after they agree.

5. **Create the local brief.** Create `~/.baton/<owner>-<repo>/brief.local.md` if it is missing,
   by copying `${CLAUDE_PLUGIN_ROOT}/templates/brief.local.md` to it. Ask the maintainer which
   local data paths (test footage, reference images) agents may use and write them, as absolute
   paths, under `## Local data`. Create
   `~/.baton/private-terms.txt` if it is missing, with one comment line explaining that each line
   is a name, hostname or folder name that must never be published. Ask the maintainer to add
   their terms. Do not suggest terms yourself and never print the file's contents.

6. **Install the git hooks.** Run `baton install-hooks`. If it reports an existing hook that is
   not Baton's, show the maintainer and stop; do not overwrite it. The hooks record the plugin
   version's `baton` and fall back to `baton` on `PATH`; when a plugin update removes that version,
   the gate refuses commits and pushes until `baton install-hooks` is run again.

7. **Verify.** Run `baton config` and confirm it prints the settings. Run `baton labels` and
   confirm it exits 0.

8. **Commit.** Commit `.baton/brief.md` on a branch and open a pull request. `.baton/` is a
   protected path, so the maintainer merges this one.

## Rules

- Nothing private goes in `.baton/brief.md`: no hostnames, IP addresses, absolute paths or names.
- If `baton` is not found, the plugin's `bin/` is not on `PATH`; call
  `${CLAUDE_PLUGIN_ROOT}/bin/baton` instead.
