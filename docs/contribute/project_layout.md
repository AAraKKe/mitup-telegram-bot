---
icon: material/sitemap-outline
---

# Project layout

Where things live in `mitup_bot/`, what each part does, and who to ask when you change it. The "governing knowledge" column points at the skill that holds the conventions for that area (loaded automatically when you work there) and the specialist agent that owns it.

| Path | What it does | Governing knowledge |
|------|--------------|---------------------|
| [`app.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/mitup_bot/app.py) | The runtime entry point. `MitupRuntime` wires the PTB `Application` to the FastAPI app and serves it with uvicorn. | [web-conventions](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/web-conventions/SKILL.md) |
| [`web/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/mitup_bot/web) | FastAPI app factory and the `POST /telegram` webhook that feeds updates into PTB. Owns the lifespan that starts and stops the bot. | [web-conventions](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/web-conventions/SKILL.md) |
| [`handlers/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/mitup_bot/handlers) | Bot logic grouped by feature area. Each handler answers one Telegram event. | [handler-conventions](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/handler-conventions/SKILL.md) · `handler-expert` |
| [`views/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/mitup_bot/views) | The screens the user sees. `MitupView` and the `factory.py` catalogue build the message text and inline keyboard. | [views](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/views/SKILL.md) · `view-expert` |
| [`models/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/mitup_bot/models) + [`db.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/mitup_bot/db.py) | SQLModel tables and the engine and session decorators that give handlers a database session. | [database](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/database/SKILL.md) · `handler-expert` |
| [`utils/messages.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/mitup_bot/utils/messages.py) | Every user-facing string and button label. Nothing shown to a user is hardcoded elsewhere. | [user-facing-text](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/user-facing-text/SKILL.md) · `bot-copywriter` |
| [`guards.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/mitup_bot/guards.py) | Input validation for handlers. Turns a raw `Update` into the typed pieces a handler needs, or raises. | [guards](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/guards/SKILL.md) · `handler-expert` |
| [`config.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/mitup_bot/config.py) | The configuration system. Merges TOML and environment providers with Pydantic validation. | [config](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/mitup-config/SKILL.md) |
| [`cli/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/mitup_bot/cli) | Production CLI commands. | [cli-conventions](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/cli-conventions/SKILL.md) · `cli-expert` |
| [`lambdas/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/mitup_bot/lambdas) | AWS Lambda functions that run outside the main bot process. | [lambda-conventions](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/lambda-conventions/SKILL.md) · `lambda-expert` |
| [`migrations/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/mitup_bot/migrations) | Alembic migration scripts. One per schema change, applied in order. | [new-migration](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/new-migration/SKILL.md) · `handler-expert` |
| [`tests/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/tests) | The test suite, mirroring the package layout. | [test-conventions](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/test-conventions/SKILL.md) · `test-expert` |

!!! tip "Golden rules"

    * Never run `python` directly. The system interpreter has none of the project's dependencies. Use `hatch run dev:python python <args>`.
    * No hardcoded user-facing strings. Every label and message lives in `utils/messages.py` and gets translated. Adding a string means adding it there.
    * Delegate by area. Each part of the tree has a specialist agent and a skill that carry its conventions. Reach for the matching one instead of editing blind.
    * Run `hatch run dev:validate` before you push. CI runs the same format, lint, type-check, and test gate and rejects merge requests that fail it.

The skill links above go to the full conventions for each area. Start there when a change grows past a one-line fix.
