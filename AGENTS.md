# Agents

## Role

Act as an experienced Python engineer. Write idiomatic, modern Python using features available in the version declared in `pyproject.toml` (see `requires-python`). Before writing code, check `pyproject.toml` for the exact minimum Python version and use the latest syntax and stdlib capabilities available in that version. Examples of preferred patterns include the native `type X = ...` and generics syntax (`def f[T](x: T) -> T`), `StrEnum`, `match` statements, `TaskGroup`, `ExceptionGroup`, `tomllib`, f-strings, and walrus operators where they improve clarity. Avoid backports, `from __future__ import annotations`, and deprecated patterns.

When requested to write code, always second guess the user's request and look for ways to improve it. If the request is vague, ask clarifying questions. If the request is a code snippet, review it for correctness, style, and adherence to project conventions before accepting it. If you identify issues or areas for improvement, rewrite the code snippet with explanations of your changes. Any time you decide to deviate from the user's original request, provide a clear rationale for your choices and wait for user confirmation before proceeding.

## Maintaining these instructions

Agent instructions are split across multiple files (see "Detailed guidelines" below). When editing any of them, follow these rules to keep them accurate over time:

1. **Never hardcode versions.** Refer to the source of truth instead (e.g., "`requires-python` in `pyproject.toml`", "version pinned in `[tool.hatch.envs.dev] dependencies`"). This avoids stale version numbers in prose.
2. **Never enumerate things that change.** Instead of listing every model, handler, or template, point to the file or directory where the canonical list lives (e.g., "see `mitup_bot/models/__init__.py`"). Describe the *patterns* and *conventions* that are stable.
3. **Describe rules, not snapshots.** Instructions should capture *how* to do something and *why*, not a frozen picture of the current state. If a section would go stale when a file is added or removed, it needs a reference instead of an enumeration.
4. **Keep instructions co-located.** Rules specific to one area belong in the `AGENTS.md` next to that code (e.g., `tests/AGENTS.md`). Cross-cutting concerns go in `.agents/`. Only general rules and the index go in the root `AGENTS.md`.
5. **Update instructions when changing conventions.** If your change alters a pattern described in an agents file (new decorator, renamed directory, new CI job), update the relevant instructions file in the same commit.

## Repository

- **Hosted on GitLab** at <https://gitlab.com/meetupbot/mitup-telegram-bot>. All URLs (issues, MRs, links in docs) must follow GitLab URL conventions, not GitHub's.
- **Issue templates** are in `.gitlab/issue_templates/`. To link to a new issue, use: `https://gitlab.com/meetupbot/mitup-telegram-bot/-/issues/new?issuable_template=TemplateName`. Available templates: `Bug`, `Feature Proposal`, `Task`, `New Language Request`, `Improve Documentation`, `Translation`, `Service Desk Request`.

## Tech stack

Versions and pins are defined in `pyproject.toml`. Always check that file for the current values — do not rely on version numbers written in documentation.

| Component | Tool |
|-----------|------|
| Language | Python (version in `requires-python`) |
| Bot SDK | [python-telegram-bot](https://docs.python-telegram-bot.org/en/stable/index.html) (PTB) |
| ORM | SQLModel (SQLAlchemy + Pydantic) |
| Migrations | Alembic |
| Build system | Hatch (with uv as installer) |
| Type checker | [ty](https://github.com/astral-sh/ty) (version pinned in `[tool.hatch.envs.dev] dependencies`) |
| Linter / formatter | Ruff |
| Testing | pytest + pytest-asyncio |
| CI/CD | GitLab CI |
| Infrastructure | AWS (Lambda, ECS, CloudWatch, ECR) |

## Development commands

All dev commands run through Hatch in the `dev` environment:

```bash
hatch run dev:validate        # Run all checks (format, lint, type-check, test)
hatch run dev:test             # Run tests
hatch run dev:type-check       # Run ty type checker
hatch run dev:format           # Format code with ruff
hatch run dev:lint             # Lint with ruff
hatch run dev:fix              # Auto-fix formatting + lint issues
```

## Detailed guidelines

Domain-specific rules are maintained in separate files to keep this document focused. Read the relevant file before working in that area:

| Topic | File |
|-------|------|
| Type checking, `ty: ignore` conventions, suppression tracking | `.agents/type-checking.md` |
| Database layer, session decorators, models, migrations | `.agents/database.md` |
| CI pipeline, jobs, validation, issue/MR templates | `.agents/ci-pipeline.md` |
| Translations, locales, message definitions | `.agents/translations.md` |
| Monitoring, metrics emission, MetricKey/Feature enums | `.agents/monitoring.md` |
| Error handling, exception hierarchy, error flow | `.agents/error-handling.md` |
| Configuration system, providers, environments | `.agents/config.md` |
| Telegram API wrapper, BotAdapter, edit error handling | `.agents/api-wrapper.md` |
| Building and registering handlers | `mitup_bot/handlers/AGENTS.md` |
| View layer, MitupView, ButtonConfig, factory, calendar | `mitup_bot/views/AGENTS.md` |
| CLI commands, auto-discovery, operational scripts | `mitup_bot/cli/AGENTS.md` |
| Lambda functions, constraints, adding new lambdas | `mitup_bot/lambdas/AGENTS.md` |
| Testing conventions, fixtures, failure modes | `tests/AGENTS.md` |

## Project structure

```
mitup_bot/                     # Main package
├── app.py                     # PTB application entry point (MitupRuntime)
├── config.py                  # Configuration system (see .agents/config.md)
├── db.py                      # Database engine and session decorators (see .agents/database.md)
├── exceptions.py              # Custom exception hierarchy (see .agents/error-handling.md)
├── guards.py                  # Input validation for handlers
├── cli/                       # Production CLI commands (see cli/AGENTS.md)
├── environments/              # Per-environment TOML config files
├── handlers/                  # Bot logic by feature area (see handlers/AGENTS.md)
├── lambdas/                   # AWS Lambda functions (see lambdas/AGENTS.md)
├── locales/                   # Compiled gettext translation files (see .agents/translations.md)
├── migrations/                # Alembic migration scripts (see .agents/database.md)
├── models/                    # SQLModel database models (see models/__init__.py for exports)
├── monitoring/                # CloudWatch metrics emission (see .agents/monitoring.md)
├── utils/                     # Shared utilities (callbacks, messages, emojis, types)
└── views/                     # View layer (see views/AGENTS.md)

bin/                           # CI scripts and dev utilities (not shipped in the wheel)
tests/                         # Test suite (see tests/AGENTS.md)
```

For the full list of files within each directory, explore the directory itself. The co-located `AGENTS.md` files (linked in the table above) describe the patterns and conventions for each area.

## Core patterns

### Callback data

All button interactions use `CallbackData` (Pydantic model in `callback_data.py`). Format: `{action};{entity}:{id}`. Predefined instances live in `mitup_bot/utils/callbacks.py`. Variants include `DateCallbackData` and `MeetingCallbackData` for richer payloads.

### Guards

Functions in `guards.py` validate handler inputs and raise domain exceptions (`UserNotFound`, `MeetupNotFound`, `MalformedCallbackData`). Always use guards instead of manual validation. Key guards: `current_user()`, `meeting_accessible()`, `valid_callback_data()`, `valid_meeting_callback_data()`.

### Views

Views in `mitup_bot/views/` abstract Telegram API calls from presentation. `MitupView` renders a message with an inline keyboard. `PaginatedMitupView` adds pagination. `MitupInlineView` is for inline query results. Use `views/factory.py` to construct views for standard screens.

### Custom context

`MitupContext` (in `custom_context.py`) extends PTB's `CallbackContext` with:
- **User data registry** — `ContextId` enum keys mapping to `ContextData` (meeting ID + text). Access via context managers: `context.meeting_id()`, `context.text()`.
- **Metrics engine** — `emit_metric()`, `put_feature_metric()`, `with_time_metric()` for CloudWatch metrics. Handler metrics are prepared automatically by the registry.

### Configuration

Config is loaded from TOML files in `mitup_bot/environments/` and optionally overridden by environment variables. Pydantic models validate all config values. See `config.py` for the full schema.
