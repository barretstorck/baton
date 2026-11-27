# Forgejo identities

On Forgejo, Baton can write as two bot users instead of as the maintainer. This guide sets them up.

## Roles

- The `reviewer` role writes as the reviewer bot.
- Every other role (developer, orchestrator, refiner, triager) writes as the baton bot. Refiner and triager are not separated from the developer yet.
- Reads, and writes made with no role, still use the maintainer's token (`FORGEJO_TOKEN`, `[forgejo] token_path` or the `forgejo-token` file). Keep that token configured.

## Setup

1. Create two ordinary Forgejo users, one for `baton` and one for `reviewer`.
2. Add each as a collaborator on the repository with write permission. The collaborator list is the real boundary, because token scopes are coarse.
3. For each user, create a token with scopes `write:issue,write:repository`.
4. Store each token in its own file, readable only by you.
5. In `~/.baton/<owner>-<repo>/identity.toml`, point each table at its file:

   ```toml
   [forgejo.baton]
   token_path = "/path/to/baton-token"

   [forgejo.reviewer]
   token_path = "/path/to/reviewer-token"
   ```

   Each table holds only `token_path`. A `[forgejo]` table may also hold the maintainer's `token_path`.

## Behaviour

- With no role table, role writes use the maintainer's token, as before.
- Once either role table exists, a role whose table is missing, or whose token file is unreadable or empty, fails the write. Baton never retries with the maintainer's token.
- Error messages name the table and the file path, never the token.
- `baton exec` runs only `gh` and refuses on Forgejo; use the `baton` subcommands.

## Warning: never use Sudo

Do not use an admin token with the `Sudo` header to act as another user. One leaked token could then act as anyone. Baton never sends that header.
