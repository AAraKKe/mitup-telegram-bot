# Agent Instructions

Instructions for any AI coding agent working in this repository. Harness-specific
instructions (Claude Code subagent delegation, etc.) live in `CLAUDE.md`, which imports
this file.

## Role

Act as an experienced Python engineer. Write idiomatic, modern Python using features available in the version declared in `pyproject.toml` (see `requires-python`).

## Skills

Domain knowledge lives in skills under `.agents/skills/` (one directory per skill, each with a `SKILL.md` following the Agent Skills format). Conventions live in skills, not in this file — **before working in an area, load the skill(s) that govern it**. Each skill's frontmatter `description` states when it applies; skills with `user-invocable: true` can also be run explicitly as `/<skill-name>` in harnesses that support it.

## Important rules

- **Validation is explicit — run it when it earns its cost.** There are no automatic validation hooks. During work, use targeted runs: `uv run mb test <paths or pytest args>` for the tests you're touching (fast, no coverage; run `uv run mb --help` for the full command surface, defined in `tools/mb/`), `uv run mb fix` after finishing a coherent batch of edits (not after every file). Before declaring a task done or opening/updating an MR, run the full gate: `uv run mb validate` (format + lint + type-check + tests). Never hand work back as finished without that full run having passed — CI is the backstop, not the first line.
- **Never run `python` directly.** The system Python has no project dependencies. Use `uv run python <args>` to execute Python in the project's managed environment.
- **Prefer scripts for bulk edits.** When the same change needs to land across many files, write a small script rather than editing each one by hand — it's faster and saves the conversation context for actual reasoning.
- **Editing dependencies keeps the lock in sync.** After changing any `pyproject.toml` (root or a `tools/*` workspace member), run `uv sync` to regenerate `uv.lock`, then stage **both** the `pyproject.toml` and `uv.lock` in the same commit. A stale lock is caught by a local pre-commit hook (`mb ci check-lock`) and by `uv lock --check` in CI, so committing the edit without the refreshed lock will be rejected. Git hooks themselves never regenerate the lock — they run `uv run --no-sync --frozen`, which requires the dev env to already exist (`uv sync` once after `pre-commit install`).

## Maintaining these instructions

When editing any instruction file, follow these rules:

1. **Never hardcode versions.** Refer to the source of truth instead (e.g., "`requires-python` in `pyproject.toml`").
2. **Never enumerate things that change.** Point to the canonical file/directory instead of listing items.
3. **Describe rules, not snapshots.** Capture *how* to do something and *why*, not a frozen state.
4. **Keep domain knowledge in skills.** Rules specific to one area belong in the appropriate skill under `.agents/skills/`, not in the instruction files.
5. **Update instructions when changing conventions.** If your change alters a documented pattern, update the relevant skill in the same commit.

## Repository

- **Hosted on GitLab** at <https://gitlab.com/meetupbot/mitup-telegram-bot>. All URLs must follow GitLab conventions, not GitHub's.
- **Interacting with repo**: you can use the `glab` cli.
  - **Exceptions**: do not use the `glab` cli to post comments or replies to merge request discussion threads. Use the `comment-mr` skill instead.
- **Creating issues**: use the `create-issue` skill. It prompts for the issue type, fills in the correct template, and creates the issue via `glab`. Never invent labels — each template already embeds the correct `/label` quick-action lines.
- **Merge request template** is at `.gitlab/merge_request_templates/Default.md`. When asked to produce an MR description, follow that template and output plain Markdown the user can copy-paste directly.
- **Commit message format** — Every commit message must be prepended with an emoji that matches the commit type. The mapping is defined in `commits_check_config.yaml`. See `docs/contribute/commit_message_format.md` for full rules.
  - **With pre-commit hooks installed** (local dev): Write commits in conventional format (`Type[(scope)]: description`). The commit-msg hook rewrites the subject to emoji form in place and the commit completes in a single `git commit` — no retry, no `--no-verify`.
  - **Without pre-commit hooks** (CI agents, etc.): Use the emoji directly (e.g., `✨ Add user authentication`).
  - The check is **idempotent**: a subject already in valid emoji form is accepted byte-unchanged, so the two shapes are interchangeable with hooks on and `git commit --amend --no-edit` on an emojified commit succeeds. If `mb` itself is mid-refactor and won't import, the hook can't run — run the gates manually and commit the emoji form with `--no-verify` until it imports again.

## External documentation

When validating Telegram API behaviour or the PTB library, consult:

- **Telegram Bot API** — <https://core.telegram.org/bots/api>
- **python-telegram-bot (PTB)** — <https://docs.python-telegram-bot.org/en/stable/index.html>

Only access those URLs when necessary. Avoid loading them into context if not needed.

## Tech stack

Versions and pins are defined in `pyproject.toml`. Always check that file — do not rely on version numbers written in documentation.

| Component | Tool |
|-----------|------|
| Language | Python (version in `requires-python`) |
| Bot SDK | [python-telegram-bot](https://docs.python-telegram-bot.org/en/stable/index.html) (PTB) |
| ORM | SQLModel (SQLAlchemy + Pydantic) |
| Migrations | Alembic |
| Package/env manager | [uv](https://docs.astral.sh/uv/) (hatchling remains the wheel build backend) |
| Dev CLI | `mb` (uv workspace member in `tools/mb/`; run `uv run mb --help`) |
| Type checker | [ty](https://github.com/astral-sh/ty) (version pinned in `[dependency-groups]` in `pyproject.toml`) |
| Linter / formatter | Ruff |
| Testing | pytest + pytest-asyncio |
| CI/CD | GitLab CI |
| Infrastructure | AWS (Lambda, ECS, CloudWatch, ECR) |

## Project structure

The repository is a uv **virtual workspace**: the root builds no wheel and every shippable unit is a
workspace member under `apps/`, `libs/` or `tools/`. The `mitup_bot.*` import namespace is a single
PEP 420 namespace package assembled from all of them — no member ships a `mitup_bot/__init__.py`.
Imports are unchanged regardless of which member owns a module (e.g. `from mitup_bot.config import ...`
resolves into `libs/core`, `from mitup_bot.guards import ...` into `libs/telegram`).

```
apps/                   # Deployable applications (each ships its own image / lambda; never imports another app)
├── bot/                # mitup-bot-app: the PTB runtime + FastAPI webhook (app.py, web/), handlers/,
│                       #   timezone_api, update_processor, and the `mitup launch` CLI (bot_cli.py)
├── events/             # mitup-events-app: the recurrent-events runner + jobs (events/) and the
│                       #   `mitup recurrent-events` CLI (events_cli.py)
├── lambda-migrations/  # mitup-lambda-migrations-app: the Alembic-runner Lambda (lambdas/migrations.py); PTB-free
└── lambda-alarm/       # mitup-lambda-alarm-app: the CloudWatch→GitLab alarm Lambda (lambdas/alarm_action.py)

libs/                   # uv workspace libraries sharing the mitup_bot namespace
├── core/               # mitup-core: config, logging, i18n engine + locales, base exceptions,
│                       #   callback_data, handler_id, supporter, limits, keyboard schema, emojis
├── monitoring/         # mitup-monitoring: CloudWatch EMF metrics emission
├── data/               # mitup-data: SQLModel tables (models/), the async engine and session
│                       #   lifecycle (db.py), and the Alembic migrations tree (migrations/)
├── telegram/           # mitup-telegram: the PTB api wrapper (api_wrapper.py), the view layer (views/),
│                       #   the message/entity/callback utilities (utils/), docs_links, and the shared
│                       #   glue both apps register at startup (guards, custom_context, mitup_types,
│                       #   reconcile, api_guards)
└── patreon/            # mitup-patreon: the Patreon API client, OAuth flow, membership webhooks,
                        #   and encrypted token storage (shared by the bot and events apps)

tools/                  # Dev-only members: the mb CLI (tools/mb/) and the one-off rails-migration tool
tests/                  # Test suite (single suite covering every member)
.agents/skills/         # Domain knowledge skills (cross-harness; .claude/skills symlinks here)
.claude/agents/         # Claude Code specialist agents
```
