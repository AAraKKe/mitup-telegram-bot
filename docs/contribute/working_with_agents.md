---
icon: material/robot-outline
---

# Building with AI

AI-assisted contributions are welcome here. The repository is built to be worked on with an assistant: the conventions one needs to get a change right live as skills, so an assistant that reads them writes code that fits.

## Where the conventions live

Domain knowledge lives in [`.agents/skills/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/.agents/skills), one folder per area, each with a plain-Markdown `SKILL.md` that captures how that part of the codebase works and why. There is a skill for handlers, one for the view layer, one for user-facing text, one for tests, one for these docs, and more.

To feed them to your assistant:

* [Claude Code](https://claude.com/claude-code) picks them up on its own. `CLAUDE.md` and `AGENTS.md` at the repo root wire the skills in, so it loads the right one for the area you are changing.
* Any other assistant can be pointed at the `SKILL.md` for the area being changed. A few to start with:
    * [`handler-conventions`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/handler-conventions/SKILL.md) for bot logic under `apps/bot/mitup_bot/handlers/`.
    * [`views`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/views/SKILL.md) for screens and keyboards under `libs/telegram/mitup_bot/views/`.
    * [`user-facing-text`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/user-facing-text/SKILL.md) for message and button copy.
    * [`test-conventions`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/test-conventions/SKILL.md) for the test suite.

The [skills directory](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/.agents/skills) is the full list. It grows over time, so browse it rather than trusting a fixed set of names. These same skills are what the repository's own agents load, which is why they stay current.

## You own what you submit

You own every line you submit, however it was produced. Review the diff as if you had typed it, run the validation gate, and be ready to explain any change in review. Maintainers close merge requests that read as generated and unreviewed.

There is no disclosure requirement. The bar is ownership, not where the code came from.
