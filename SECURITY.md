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

At identity tier 1 the App private keys and `identity.toml` sit under `~/.baton/`, readable by the
OS user the agents run as. File modes do not stop an agent running as that same user. The `Read`
deny rules in the README ([Identity tiers](README.md#identity-tiers)) are a first layer: they stop
Claude Code's file tools and the Bash file commands it recognises, but not a command that reads a
file without naming it, or a script that opens it. The stronger option, not implemented, is a
signing helper running as another OS user that holds the keys and hands Baton only short-lived
installation tokens.

Only the latest release is supported.
