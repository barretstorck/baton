# Forgejo notes for Baton skills

Read this after `commands.md` when the repository is on Forgejo. It holds only what the `baton` commands cannot hide.

- A draft pull request is a title prefix: `WIP:`, unless `forge.draft_prefix` in `.baton/brief.md` names another. `baton pr create --draft` adds it and `baton pr ready` removes it.
- There is no issue lock, so `baton close --lock` refuses.
- `baton run log` refuses (exit 2): read a failed check's log from its url.
- `baton issue list --search` takes plain words only; GitHub qualifiers such as `in:body` are refused.
- A plain `/baton ready` does not count, because Forgejo keeps no edit history; the maintainer posts the hashed form.
