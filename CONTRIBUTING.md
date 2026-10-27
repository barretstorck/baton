# Contributing

Baton is developed with Baton: issues are refined into plans of small tasks and worked in sprints
by agents, and the maintainer merges. Contributions from people are welcome too.

## Issues

- Bug reports: what you ran, what you expected, what happened, and your Claude Code, `gh` and
  Python versions. Replace hosts, paths, names and tokens with placeholders.
- Feature ideas: the problem first, then the change you have in mind.
- Security problems: see [SECURITY.md](SECURITY.md), not a public issue.

Issues written by anyone other than the maintainer are never picked up by a sprint until the
maintainer has refined them and marked them ready. That is a safety rule, not a judgement.

## Pull requests

- Open an issue first for anything larger than a small fix.
- Run the verification before you open the pull request:

      PYTHONPATH=lib python3 -m unittest discover -s tests

- New behaviour has tests in `tests/`, using `tests/fakes.py` `FakeRunner` instead of real `gh` or
  `git` calls.
- Skills, agents, `references/github.md` and `README.md` describe any changed command or flow.
- Nothing personal: no real names, hosts, IP addresses or paths in code, tests, fixtures or commit
  messages. Run `baton install-hooks` to have the git hooks check for you.

By contributing you agree that your contribution is licensed under the [MIT License](LICENSE).
