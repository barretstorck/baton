# GitLab identities

On GitLab, Baton can write as two bot accounts instead of as the maintainer. This guide sets them up.

## Roles

- The `reviewer` role writes with the `[gitlab.reviewer]` token.
- Every other role (developer, orchestrator, refiner, triager) writes with the `[gitlab.baton]` token. Refiner and triager are not separated from the developer yet; Planner tokens wait for the role-plumbing issue.
- Reads, and writes made with no role, still use the maintainer's token (`GITLAB_TOKEN`, `[gitlab] token_path` or the `gitlab-token` file). Keep that token configured.

## Setup

Create one token per role, `baton` and `reviewer`, in one of two ways.

**Self-managed GitLab and paid gitlab.com:**

1. In the project, create one project access token per role, with role Developer and scope `api`.

**gitlab.com Free** (project access tokens are paid-only there):

1. Create one ordinary machine-user account per role.
2. Add each account to the project as Developer.
3. For each account, create a personal access token with scope `api`.

Then, for both variants:

1. Store each token in its own file, readable only by you.
2. In `~/.baton/<owner>-<repo>/identity.toml` (for nested groups, the folder name joins the group path with `-`), point each table at its file:

   ```toml
   [gitlab.baton]
   token_path = "/path/to/baton-token"

   [gitlab.reviewer]
   token_path = "/path/to/reviewer-token"
   ```

   Each table holds only `token_path`. A `[gitlab]` table may also hold the maintainer's `token_path`.

The reviewer account must differ from the account that opens merge requests, so that it can approve them.

Tokens expire. Renewal by `baton identity rotate` comes in #429; until then renew each token by hand and replace the file's contents.

## Behaviour

- With no role table, role writes use the maintainer's token, as before.
- Once either role table exists, a role whose table is missing, or whose token file is unreadable or empty, fails the write. Baton never retries with the maintainer's token.
- Error messages name the table and the file path, never the token.
- Baton sends the token as the `PRIVATE-TOKEN` header.

## Warning: never use Sudo

Do not use an admin token with the `Sudo` header to act as another user. One leaked token could then act as anyone. Baton never sends that header.
