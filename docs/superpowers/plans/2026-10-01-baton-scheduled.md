# Baton plan 4: scheduled runs and the pilot

**Spec:** `docs/superpowers/specs/2026-10-01-baton-design.md` (sections 5, 6, 12).
**Part 1 executed 2026-10-01**, inline. Part 2 needs the maintainer.

## Part 1: scheduled runs (built)

| File | Purpose |
|---|---|
| `baton lock <acquire\|release>` | Run lock in the repository's `.git` directory; stale after six hours |
| `skills/sprint/SKILL.md`, "Scheduled runs" | The stricter unattended behaviour |
| `docs/scheduling.md` | cron and routine setup, with the settings an unattended run needs |

## Part 2: pilot sprint (not started)

The skills are prose and have only been read, not exercised. The pilot is their first real test.

- [ ] Maintainer installs the plugin (`claude --plugin-dir <path to baton>` or a marketplace entry).
- [ ] Maintainer chooses the pilot repository and fills in `~/.baton/private-terms.txt`.
- [ ] `/baton:init` in that repository; maintainer merges the brief pull request.
- [ ] `/baton:refine` on three to five small issues.
- [ ] `/baton:sprint` with two developers, interactive, maintainer watching.
- [ ] Check each scenario from the spec's testing section as it occurs: a clean issue, a question
      and its cited decision, overlapping files, an untrusted comment, a resumed session.
- [ ] First retrospective; turn its lessons into changes to the skills.
- [ ] Only then: one scheduled run.
