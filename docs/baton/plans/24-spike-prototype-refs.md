## Pattern

Pinning by commit SHA, not by ref name: `docs/superpowers/specs/2026-10-14-plans-in-repo-design.md`,
"The `Plan:` link". Skill text is edited in place, keeping each file's numbering and voice. The
only code change is the `type:spike` label description in `lib/baton/labels.py:16`; the rest is
text in skills, a template, the design spec and the README.

## Verification

`PYTHONPATH=lib python3 -m unittest discover -s tests -v` (the README's "Tests" section).

## Design notes

- No new Python helper: the developer writes the `Prototype:` line from a template in the skill,
  so the only thing to unit-test is the label description.
- Draft pull requests of ordinary issues stay: `skills/develop/SKILL.md` lines 71 and 83,
  `skills/sprint/SKILL.md` lines 103 and 118, the design spec lines 123 and 339. Only spike uses change.
- A custom ref has no remote-tracking ref, so a bare `--force-with-lease` cannot work. The skill
  must give the explicit form `--force-with-lease=refs/spikes/<N>:<old sha>`, with the old sha
  from `git ls-remote origin refs/spikes/<N>`, or the plain push when `ls-remote` prints nothing.
- Order inside the spike: push the ref first, then post the findings (the `Prototype:` link needs
  the SHA), then delete the remote branch, then set `status:in-review`. The link uses the full
  SHA; commit pages can 404 for a few seconds after the push.
- The pre-push privacy scan (`lib/baton/githooks.py` `pre_push`) scans the commits new to the remote for any ref name, so the `refs/spikes/<N>` push is covered.
- References: GitLab keep-around refs (docs.gitlab.com/development/merge_request_concepts/keep_around_refs/), GitHub "removing sensitive data" and rulesets docs.
- #25 extends the README section from task 5 with `refs/plans/*`; keep its checklist generic enough.

## Plan

Written against: dc7d89f8e49ade16665665d7312fe65a2c9ac435

- [x] **1. Label description names no pull request**
  - Files: `lib/baton/labels.py`, `tests/test_labels.py`
  - Uses: the `"type:spike": ("5319e7", "Research; a pull request is optional")` entry (`lib/baton/labels.py:16`); `make_config` and the `LabelsTest` class (`tests/test_labels.py:9`, `:19`)
  - Provides: description `"Research; findings, optional prototype ref"`
  - Check: `PYTHONPATH=lib python3 -m unittest tests.test_labels -v`; add one test that the `type:spike` description contains no "pull request"

- [x] **2. Developer skill: spike flow without a draft pull request**
  - Files: `skills/develop/SKILL.md`
  - Uses: section "## Spikes" (`skills/develop/SKILL.md:170-186`), the spike report block (`:245-252`)
  - Provides: Spikes steps are: 1 set `status:in-dev` (unchanged); 2 investigate, prototype commits go on `issue-<N>-<slug>`, no pull request; 3 status comments and pushes (unchanged); 4 when the findings are ready and there is prototype code: `git push --force-with-lease=refs/spikes/<N>:<old sha> origin HEAD:refs/spikes/<N>` (see Design notes for the old sha); 5 post the findings, ending with `Prototype: [<short sha>](https://github.com/<owner>/<repo>/tree/<full sha>) · ` followed by the code span `git fetch origin refs/spikes/<N>`, or `Prototype: none`; 6 `git push origin --delete issue-<N>-<slug>` (a no-prototype spike has no branch), then `baton set-status <N> status:in-review`; 7 return `DONE`. Report block: replace `Draft pull request: none | #<PR>` with `Prototype: none | refs/spikes/<N> (<sha>)`; the `DONE` sentence at the end of the Spikes list names the prototype ref, not a pull request
  - Check: `rg -n "Draft pull request|Spike: <title>" skills/develop/SKILL.md` prints nothing and `rg -n "refs/spikes" skills/develop/SKILL.md` shows the push, the line and the report

- [x] **3. Sprint skill and retro template: spike prototypes**
  - Files: `skills/sprint/SKILL.md`, `templates/retro.md`
  - Uses: "### 4. Review and merge", the Spike bullet (`skills/sprint/SKILL.md:98-100`); retrospective step 4 (`skills/sprint/SKILL.md:314`); `## Open spike drafts` and its sentence (`templates/retro.md:52-54`)
  - Provides: the Spike bullet no longer says to leave a draft open; when the report's `Prototype:` is not `none` the orchestrator runs `git ls-remote origin refs/spikes/<N>` and `git ls-remote --heads origin 'issue-<N>-*'`, deletes the branch with `git push origin --delete <branch>` if it still exists, and if the ref is missing reports it to the maintainer instead of closing the issue. Retro step 4 reads "List spike prototypes in the retrospective." The template heading is `## Spike prototypes`, one line per spike (issue, ref, link), for reference only, nothing for the maintainer to do
  - Check: `rg -in "spike draft|leave any draft" skills templates` prints nothing

- [ ] **4. Refine skill and design spec**
  - Files: `skills/refine/SKILL.md`, `docs/superpowers/specs/2026-10-01-baton-design.md`
  - Uses: refine step 2 "Investigate the code" and the Pointers rule in step 5 (`skills/refine/SKILL.md:56`, `:104`); design spec "### Spikes" (`docs/superpowers/specs/2026-10-01-baton-design.md:199-206`) and the developer section's spike paragraph (`:360-362`)
  - Provides: refine: one sentence that for an issue that comes from a spike, Pointers name the spike's `Prototype:` link as reference material and never as a base branch. Design spec: "Spikes" describes `refs/spikes/<N>`, a findings link by SHA, no pull request, no branch left; remove the "draft stays open ... build on that branch" reasoning and say prototypes are listed in the retrospective for reference; the developer-section paragraph says "an optional prototype ref" instead of "optional draft pull request"
  - Check: `rg -n "draft" docs/superpowers/specs/2026-10-01-baton-design.md` leaves only the ordinary-issue uses (lines 123 and 339)

- [ ] **5. README note on spike prototypes**
  - Files: `README.md`
  - Uses: README headings (`## How a pull request gets merged` at line 46, `## Skills` at line 59)
  - Provides: a `## Spike prototypes` section between those two with the bullets of #24's last acceptance criterion: where prototypes live and the fetch command; a normal clone does not fetch them and `git push --mirror` from such a clone deletes them on the remote; rulesets cover only branches and tags so anyone with write access can delete a spike ref; a "before making a repository public" checklist (`git ls-remote origin 'refs/spikes/*'`, review or `git push origin --delete refs/spikes/<N>`; a deleted ref breaks its findings link but not the findings text; deleting does not erase the commits, which GitHub can serve by SHA from cached views, forks and old `refs/pull/*` refs, including earlier spike drafts, until GitHub Support runs a garbage collection)
  - Check: none

- [ ] **6. Full verification**
  - Files: none
  - Check: `PYTHONPATH=lib python3 -m unittest discover -s tests -v` passes
