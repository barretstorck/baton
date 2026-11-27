# GitLab notes for Baton skills

Read this after `commands.md` when the repository is on GitLab. It holds only what the `baton` commands cannot hide.

- A merge request may be written `!N` in prompts and replies. Issues and merge requests are numbered apart, so `#1` and `!1` can both exist.
- A draft merge request is a `Draft:` title prefix. `baton pr create --draft` adds it and `baton pr ready` removes it, along with a leading `[Draft]` or `(Draft)`. `forge.draft_prefix` is not used on GitLab.
- Baton keeps its single-colon `status:` labels. `status::` scoped labels are not exclusive on GitLab Free, so they would not replace each other.
- Native approvals do not reset on a push on GitLab Free, and the author may approve. `baton merge-pr` therefore counts only Baton's review marker on the head commit.
- Project access tokens are paid-only on gitlab.com, so gitlab.com Free uses one machine-user account per role; see `docs/gitlab-identity.md`.
- `baton run log` refuses (exit 2): read a failed check's log from its url.
- `baton issue list --search` takes plain words only; GitHub qualifiers such as `in:body` are refused.
- `baton` cannot edit a review comment's body on GitLab; post a new review instead.
