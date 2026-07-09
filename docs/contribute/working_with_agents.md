---
icon: material/robot-outline
---

# Working with agents

This repository is built to be worked on with [Claude Code](https://claude.com/claude-code), and its conventions live in two directories: `.agents/skills/` for the domain knowledge and `.claude/agents/` for the specialist agents. You do not need Claude Code to contribute, but knowing how the two directories fit together tells you where the real rules are written down.

## Skills hold the conventions

Domain knowledge lives in [`.agents/skills/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/.agents/skills). Each skill is a folder with a `SKILL.md` that captures how one area of the codebase works and why. There is a skill for handlers, one for the view layer, one for user-facing text, one for tests, one for these docs, and more.

Before you change code in an area, read the skill that governs it. The `SKILL.md` files are plain Markdown, so you can read them straight from GitLab without any tooling. A few to start with:

* [`handler-conventions`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/handler-conventions/SKILL.md) for bot logic under `mitup_bot/handlers/`.
* [`views`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/views/SKILL.md) for screens and keyboards under `mitup_bot/views/`.
* [`user-facing-text`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/user-facing-text/SKILL.md) for message and button copy.
* [`test-conventions`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/test-conventions/SKILL.md) for the test suite.

The [skills directory](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/.agents/skills) is the full list. It grows over time, so browse it rather than trusting a fixed set of names.

## Specialists own each area

Work is delegated by area to specialist agents defined in [`.claude/agents/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/.claude/agents), one file per agent. A handler change goes to the handler specialist, a view change to the view specialist, message copy to the copywriter, and so on. Each agent carries the skills for its area, which is why the skills are the source of truth and the agents are the routing.

You do not have to think in terms of agents when you contribute by hand. Treat the mapping as a map of who owns what: it tells you which `SKILL.md` to read for the files you are touching.

## The reviewer runs before every merge request

Before any merge request opens, the [`convention-reviewer`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.claude/agents/convention-reviewer.md) agent checks the branch diff against `main` for convention breaches, regardless of who wrote the code. It reads the same skills and flags anything that drifts from them.

You can do the reviewer's job yourself. Read the skills for the areas your branch touches, then diff your branch against `main` and check your changes against those rules. CI runs the automated checks after that, so run [testing and validation](testing.md) before you push.
