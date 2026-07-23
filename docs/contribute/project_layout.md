---
icon: material/sitemap-outline
---

# Project layout

The repository is a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/). The root builds no package of its own; every shippable unit is a workspace member under `apps/`, `libs/`, or `tools/`. All of them share one import namespace, `mitup_bot`, assembled as a single [PEP 420](https://peps.python.org/pep-0420/) namespace package: no member ships a `mitup_bot/__init__.py`, so `from mitup_bot.config import ...` resolves into `libs/core` and `from mitup_bot.guards import ...` into `libs/telegram` without either member knowing about the other. Imports read the same no matter which member owns the module.

## The three kinds of member

**`apps/`** holds the deployable applications. Each one ships its own image or Lambda and never imports another app.

* [`apps/bot`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/apps/bot) (`mitup-bot-app`): the PTB runtime and the FastAPI webhook host, the handlers, the timezone API, the update processor, and the `mitup launch` CLI.
* [`apps/events`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/apps/events) (`mitup-events-app`): the recurrent-events runner and its jobs, plus the `mitup recurrent-events` CLI.
* [`apps/lambda-migrations`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/apps/lambda-migrations) (`mitup-lambda-migrations-app`): the Alembic-runner Lambda. PTB-free.
* [`apps/lambda-alarm`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/apps/lambda-alarm) (`mitup-lambda-alarm-app`): the CloudWatch-to-GitLab alarm Lambda.

**`libs/`** holds the shared libraries the apps build on. They carry no deployment of their own.

* [`libs/core`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/libs/core) (`mitup-core`): config, logging, the i18n engine and locale catalogs, base exceptions, callback data, handler ids, supporter and limits logic, the keyboard schema, and emojis.
* [`libs/data`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/libs/data) (`mitup-data`): the SQLModel tables, the async engine and session lifecycle, and the Alembic migrations tree.
* [`libs/telegram`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/libs/telegram) (`mitup-telegram`): the PTB API wrapper, the view layer, the message and callback utilities, and the shared glue both apps register at startup (guards, custom context, reconciliation).
* [`libs/monitoring`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/libs/monitoring) (`mitup-monitoring`): CloudWatch EMF metrics emission.
* [`libs/patreon`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/libs/patreon) (`mitup-patreon`): the Patreon API client, OAuth flow, membership webhooks, and encrypted token storage.

**`tools/`** holds dev-only members that never ship: the [`mb`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/tools/mb) developer CLI and the one-off rails-migration tool. [`tests/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/tests) is a single suite covering every member.

## Who owns what

When a change grows past a one-line fix, the conventions for the area live in a skill under [`.agents/skills/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/.agents/skills), and a specialist agent owns that area. The table below maps each part of the tree to both.

| Path | What it does | Governing knowledge |
|------|--------------|---------------------|
| [`apps/bot/mitup_bot/app.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/apps/bot/mitup_bot/app.py) + [`web/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/apps/bot/mitup_bot/web) | The runtime entry point and the FastAPI webhook host that feeds updates into PTB. | [web-conventions](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/web-conventions/SKILL.md) |
| [`apps/bot/mitup_bot/handlers/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/apps/bot/mitup_bot/handlers) | Bot logic grouped by feature area. Each handler answers one Telegram event. | [handler-conventions](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/handler-conventions/SKILL.md) · `handler-expert` |
| [`libs/telegram/mitup_bot/views/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/libs/telegram/mitup_bot/views) | The screens the user sees. `MitupView` and the factory catalogue build the message text and inline keyboard. | [views](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/views/SKILL.md) · `view-expert` |
| [`libs/data/mitup_bot/models/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/libs/data/mitup_bot/models) + [`db.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/libs/data/mitup_bot/db.py) | SQLModel tables and the engine and session decorators that give handlers a database session. | [database](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/database/SKILL.md) · `handler-expert` |
| [`libs/telegram/mitup_bot/utils/messages.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/libs/telegram/mitup_bot/utils/messages.py) | Every user-facing string and button label. Nothing shown to a user is hardcoded elsewhere. | [user-facing-text](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/user-facing-text/SKILL.md) · `bot-copywriter` |
| [`libs/telegram/mitup_bot/guards.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/libs/telegram/mitup_bot/guards.py) | Input validation for handlers. Turns a raw `Update` into the typed pieces a handler needs, or raises. | [guards](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/guards/SKILL.md) · `handler-expert` |
| [`libs/core/mitup_bot/config.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/libs/core/mitup_bot/config.py) | The configuration system. Merges TOML and environment providers with Pydantic validation. | [mitup-config](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/mitup-config/SKILL.md) |
| App CLI entry modules ([`bot_cli.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/apps/bot/mitup_bot/bot_cli.py), [`events_cli.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/apps/events/mitup_bot/events_cli.py)) | The `mitup launch` and `mitup recurrent-events` production commands. | [cli-conventions](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/cli-conventions/SKILL.md) · `cli-expert` |
| [`apps/lambda-migrations/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/apps/lambda-migrations) + [`apps/lambda-alarm/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/apps/lambda-alarm) | AWS Lambda functions that run outside the main bot process. | [lambda-conventions](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/lambda-conventions/SKILL.md) · `lambda-expert` |
| [`libs/data/mitup_bot/migrations/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/libs/data/mitup_bot/migrations) | Alembic migration scripts. One per schema change, applied in order. | [new-migration](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/new-migration/SKILL.md) · `handler-expert` |
| [`tests/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/tests) | The test suite, covering every workspace member. | [test-conventions](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/test-conventions/SKILL.md) · `test-expert` |

## How images map to deployables

Each `apps/` member carries its own `Dockerfile`, built from the repo root so the whole uv workspace is the build context. Four members, four images, four running things:

| Member | Image | Runs as |
|--------|-------|---------|
| [`apps/bot`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/apps/bot/Dockerfile) | `mitup-bot-app` | An ECS service: the webhook host and PTB handlers. |
| [`apps/events`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/apps/events/Dockerfile) | `mitup-events-app` | An ECS service: the recurrent-events worker. |
| [`apps/lambda-migrations`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/apps/lambda-migrations/Dockerfile) | `mitup-lambda-migrations-app` | A Lambda that applies Alembic migrations on deploy. |
| [`apps/lambda-alarm`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/apps/lambda-alarm/Dockerfile) | `mitup-lambda-alarm-app` | A Lambda that forwards CloudWatch alarms to GitLab. |

Each image resolves only its own member's locked dependency closure, so the alarm Lambda never carries PTB and the migrations Lambda never carries the web stack.

!!! tip "Golden rules"

    * Never run `python` directly. The system interpreter has none of the project's dependencies. Use `uv run python <args>`.
    * No hardcoded user-facing strings. Every label and message lives in `libs/telegram/mitup_bot/utils/messages.py` and gets translated. Adding a string means adding it there.
    * Delegate by area. Each part of the tree has a specialist agent and a skill that carry its conventions. Reach for the matching one instead of editing blind.
    * Run `uv run mb validate` before you push. CI runs the same format, lint, type-check, and test gate and rejects merge requests that fail it.

The skill links above go to the full conventions for each area. Start there when a change grows past a one-line fix.
