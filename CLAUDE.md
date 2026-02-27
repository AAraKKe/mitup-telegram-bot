# Claude Code — Project Instructions

## Role

Act as an experienced Python engineer. Write idiomatic, modern Python using features available in the version declared in `pyproject.toml` (see `requires-python`). Before writing code, check `pyproject.toml` for the exact minimum Python version and use the latest syntax and stdlib capabilities available in that version. Examples of preferred patterns include the native `type X = ...` and generics syntax (`def f[T](x: T) -> T`), `StrEnum`, `match` statements, `TaskGroup`, `ExceptionGroup`, `tomllib`, f-strings, and walrus operators where they improve clarity. Avoid backports, `from __future__ import annotations`, and deprecated patterns.

When requested to write code, always second guess the user's request and look for ways to improve it. If the request is vague, ask clarifying questions. If the request is a code snippet, review it for correctness, style, and adherence to project conventions before accepting it. If you identify issues or areas for improvement, rewrite the code snippet with explanations of your changes. Any time you decide to deviate from the user's original request, provide a clear rationale for your choices and wait for user confirmation before proceeding.

## Maintaining these instructions

When editing any instruction file, follow these rules:

1. **Never hardcode versions.** Refer to the source of truth instead (e.g., "`requires-python` in `pyproject.toml`").
2. **Never enumerate things that change.** Point to the canonical file/directory instead of listing items.
3. **Describe rules, not snapshots.** Capture *how* to do something and *why*, not a frozen state.
4. **Keep instructions co-located.** Rules specific to one area belong in the `CLAUDE.md` next to that code.
5. **Update instructions when changing conventions.** If your change alters a documented pattern, update the relevant file in the same commit.

## Repository

- **Hosted on GitLab** at <https://gitlab.com/meetupbot/mitup-telegram-bot>. All URLs must follow GitLab conventions, not GitHub's.
- **Interacting with repo**: you can use the `glab` cli
- **Issue templates** are in `.gitlab/issue_templates/`. Link format: `https://gitlab.com/meetupbot/mitup-telegram-bot/-/issues/new?issuable_template=TemplateName`. Available templates: `Bug`, `Feature Proposal`, `Task`, `New Language Request`, `Improve Documentation`, `Translation`, `Service Desk Request`.
- **Merge request template** is at `.gitlab/merge_request_templates/Default.md`. When asked to produce an MR description, follow that template and output plain Markdown the user can copy-paste directly.
- **Commit message format** — Every commit message must be prepended with an emoji that matches the commit type. The mapping is defined in `commits_check_config.yaml`. See `docs/collaborate/commit_message_format.md` for full rules.
  - **With pre-commit hooks installed** (local dev): Write commits in conventional format (`Type[(scope)][!]: description`). The hook replaces the type with the emoji automatically.
  - **Without pre-commit hooks** (CI agents, etc.): Use the emoji directly (e.g., `✨ Add user authentication`).

## External documentation

When validating Telegram API behaviour, consult:

- **Telegram Bot API** — <https://core.telegram.org/bots/api>
- **python-telegram-bot (PTB)** — <https://docs.python-telegram-bot.org/en/stable/index.html>

Do not rely on assumptions or cached knowledge. Always verify against the current API specification.

## Tech stack

Versions and pins are defined in `pyproject.toml`. Always check that file — do not rely on version numbers written in documentation.

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
hatch run dev:validate         # Run all checks (format, lint, type-check, test)
hatch run dev:test             # Run tests
hatch run dev:type-check       # Run ty type checker
hatch run dev:format           # Format code with ruff
hatch run dev:lint             # Lint with ruff
hatch run dev:fix              # Auto-fix formatting + lint issues
```

## Project structure

```
mitup_bot/                     # Main package
├── app.py                     # PTB application entry point (MitupRuntime)
├── config.py                  # Configuration system
├── db.py                      # Database engine and session decorators
├── exceptions.py              # Custom exception hierarchy
├── guards.py                  # Input validation for handlers
├── cli/                       # Production CLI commands
├── environments/              # Per-environment TOML config files
├── handlers/                  # Bot logic by feature area
├── lambdas/                   # AWS Lambda functions
├── locales/                   # Compiled gettext translation files
├── migrations/                # Alembic migration scripts
├── models/                    # SQLModel database models (see models/__init__.py for exports)
├── monitoring/                # CloudWatch metrics emission
├── utils/                     # Shared utilities (callbacks, messages, emojis, types)
└── views/                     # View layer

bin/                           # CI scripts and dev utilities (not shipped in the wheel)
tests/                         # Test suite
```

## Core patterns

### Callback data

All button interactions use `CallbackData` (Pydantic model in `callback_data.py`). Format: `{action};{entity}:{id}`. Predefined instances live in `mitup_bot/utils/callbacks.py`. Variants include `DateCallbackData` and `MeetingCallbackData` for richer payloads.

### Guards

Functions in `guards.py` validate handler inputs and raise domain exceptions (`UserNotFound`, `MeetupNotFound`, `MalformedCallbackData`). Always use guards instead of manual validation. Key guards: `current_user()`, `meeting_accessible()`, `valid_callback_data()`, `valid_meeting_callback_data()`.

### Views

Views in `mitup_bot/views/` abstract Telegram API calls from presentation. `MitupView` renders a message with an inline keyboard. `PaginatedMitupView` adds pagination. `MitupInlineView` is for inline query results. Use `views/factory.py` to construct views for standard screens. See `mitup_bot/views/CLAUDE.md` for full conventions.

### Custom context

`MitupContext` (in `custom_context.py`) extends PTB's `CallbackContext` with:
- **User data registry** — `ContextId` enum keys mapping to `ContextData` (meeting ID + text). Access via context managers: `context.meeting_id()`, `context.text()`.
- **Metrics engine** — `emit_metric()`, `put_feature_metric()`, `with_time_metric()` for CloudWatch metrics. Handler metrics are prepared automatically by the registry.

### Configuration

Config is loaded from TOML files in `mitup_bot/environments/` and optionally overridden by environment variables. Pydantic models validate all config values. See the `config` reference skill for full details.

## Available skills

| Skill | Type | Purpose |
|-------|------|---------|
| `/git` | Task | Branching, staging, committing, pushing, and rebasing |
| `/mr` | Task | Generate an MR description from the GitLab template |
| `/new-handler` | Task | Scaffold a new bot handler package |
| `/translate` | Task | Add a new user-facing string to the message catalog |
| `/ty-ignore` | Task | Insert a `ty: ignore` comment with the required issue URL |
| `/new-migration` | Task | Generate and validate an Alembic migration |
| `type-checking` | Reference | ty suppression rules (auto-loaded when type errors appear) |
| `database` | Reference | Session decorator and migration patterns |
| `translations` | Reference | gettext message class conventions |
| `monitoring` | Reference | EMF metrics and CloudWatch patterns |
| `api-wrapper` | Reference | TelegramApiWrapper, BotAdapter, ContextOrBotAdapter |
| `error-handling` | Reference | Exception hierarchy and SUPPRESSED_EXCEPTIONS |
| `config` | Reference | Config provider system and SecretStr |
| `ci-pipeline` | Reference | Pipeline stages and validation jobs |
