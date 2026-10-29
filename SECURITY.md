# Security

## Reporting a problem

Report security problems privately through
[GitHub's private vulnerability reporting](https://github.com/barretstorck/baton/security/advisories/new),
not in a public issue. That includes:

- a way past the publish gate or the git hooks (a command or payload that publishes private
  details, or closes, reopens or merges without the checks described in `README.md`);
- a way for someone other than the maintainer to steer agents, for example a comment or issue that
  Baton treats as the maintainer's answer, approval or review;
- anything that leaks credentials, tokens or private files.

Please include the command or text that reproduces it.

## What Baton does and does not protect

Baton's agents run with your own `gh` and `git` credentials and can do anything those credentials
allow. The gate and the git hooks catch mistakes: private details in published text and git hook
bypasses. They are not a sandbox. They see only the forge CLIs (`FORGE_CLIS`) and `git` commands an
agent runs through Claude Code's Bash tool, not scripts that do HTTP themselves, and they cannot
stop a determined attacker who controls your machine or your Claude Code session.

Only the latest release is supported.
