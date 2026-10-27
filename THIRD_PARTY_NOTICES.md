# Third-party notices

Baton adapts material from the following project. Its licence is reproduced in full below, as it
requires.

## superpowers

- Project: [superpowers](https://github.com/obra/superpowers), a Claude Code plugin by Jesse Vincent
- Licence: MIT
- What Baton adapts from it:
  - `skills/review/SKILL.md` and `lib/baton/review.py`: the review loop, from its
    `requesting-code-review` and `receiving-code-review` skills.
  - `skills/refine/SKILL.md`: plans of small tasks, each a brief for a fresh agent, from its
    `writing-plans` skill (also reflected in `templates/plan.md`).
  - `skills/develop/SKILL.md`: a fresh developer per task working from a brief, from its
    plan-execution skills (`subagent-driven-development`, `executing-plans`).
  - `docs/superpowers/`: Baton's original design and implementation plans, written with its
    `brainstorming` and `writing-plans` skills and kept in the layout those skills use.

```
MIT License

Copyright (c) 2025 Jesse Vincent

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
