# Claude Code — Project Instructions

## Role

Act as an experienced Python engineer. Write idiomatic, modern Python using features available in the version declared in `pyproject.toml` (see `requires-python`).

## Working with agents

For complex tasks that span multiple areas (handler + migration + tests + translations, etc.), use the `/em` skill to enter orchestration mode. It clarifies requirements, forms a phased plan, presents it for approval, then delegates to specialist agents with checkpoints between phases:

```
/em Add recurring meetings — needs a handler, DB migration, tests, and EN/ES translations.
```

For single-domain tasks or targeted fixes, invoke a specialist agent directly with natural language: "Use the handler-expert agent to...".

Run `/project-guide` for a full reference of all available agents and skills.

## Important rules

- Never run tests, linters, formatters, or anything similar for validation.
- Tests are run by a hook when you are done. If you want feedback mid-work, run a specific test with `hatch run dev -- <pytest args>`. Avoid full runs.
- Formatters and linters are run by hooks after each modification. No need to run them manually.
- **Never run `python` directly.** The system Python has no project dependencies. Always use `hatch run dev:python python <args>` to execute Python within the project's managed environment.
- **Bulk updates**: when you decided that you need to modify multiple files at once for the same change, evaluate whether you can build a script that does it instead of updating all files by hand to avoid context exhaustion.

## When hooks fail

Hooks run automatically after work is complete (type checker, tests, linter/formatter). If any hook fails, do NOT attempt to fix it yourself.

**If the work was done by a specialist agent:** resume that agent's session with the error output. The resumed agent retains full context of every change it made.

**If the work was done directly:** delegate to the appropriate specialist and provide the full `git diff` of the changes alongside the error output — not a prose summary. A diff gives the specialist the exact state of the code.

| Failing hook | Delegate to |
|--------------|-------------|
| Type checker (`ty`) | type-checking agent |
| Tests (`pytest`) | test-expert agent |
| Linter (`ruff`) | convention-reviewer agent |

## Maintaining these instructions

When editing any instruction file, follow these rules:

1. **Never hardcode versions.** Refer to the source of truth instead (e.g., "`requires-python` in `pyproject.toml`").
2. **Never enumerate things that change.** Point to the canonical file/directory instead of listing items.
3. **Describe rules, not snapshots.** Capture *how* to do something and *why*, not a frozen state.
4. **Keep domain knowledge in skills.** Rules specific to one area belong in the appropriate skill under `.claude/skills/`, not in CLAUDE.md files.
5. **Update instructions when changing conventions.** If your change alters a documented pattern, update the relevant skill in the same commit.

## Repository

- **Hosted on GitLab** at <https://gitlab.com/meetupbot/mitup-telegram-bot>. All URLs must follow GitLab conventions, not GitHub's.
- **Interacting with repo**: you can use the `glab` cli.
  - **Exceptions**: do not use the `glab` cli to post comments or replies to merge request discussion threads. Use the `/comment-mr` skill instead.
- **Issue templates** are in `.gitlab/issue_templates/`. Link format: `https://gitlab.com/meetupbot/mitup-telegram-bot/-/issues/new?issuable_template=TemplateName`. Available templates: `Bug`, `Feature Proposal`, `Task`, `New Language Request`, `Improve Documentation`, `Translation`, `Service Desk Request`.
- **Merge request template** is at `.gitlab/merge_request_templates/Default.md`. When asked to produce an MR description, follow that template and output plain Markdown the user can copy-paste directly.
- **Commit message format** — Every commit message must be prepended with an emoji that matches the commit type. The mapping is defined in `commits_check_config.yaml`. See `docs/collaborate/commit_message_format.md` for full rules.
  - **With pre-commit hooks installed** (local dev): Write commits in conventional format (`Type[(scope)][!]: description`). The hook replaces the type with the emoji automatically.
  - **Without pre-commit hooks** (CI agents, etc.): Use the emoji directly (e.g., `✨ Add user authentication`).

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
| Build system | Hatch (with uv as installer) |
| Type checker | [ty](https://github.com/astral-sh/ty) (version pinned in `[tool.hatch.envs.dev] dependencies`) |
| Linter / formatter | Ruff |
| Testing | pytest + pytest-asyncio |
| CI/CD | GitLab CI |
| Infrastructure | AWS (Lambda, ECS, CloudWatch, ECR) |

## Project structure

```
mitup_bot/              # Main package
├── app.py              # PTB application entry point (MitupRuntime)
├── config.py           # Configuration system
├── db.py               # Database engine and session decorators
├── exceptions.py       # Custom exception hierarchy
├── guards.py           # Input validation for handlers
├── cli/                # Production CLI commands
├── environments/       # Per-environment TOML config files
├── handlers/           # Bot logic by feature area
├── lambdas/            # AWS Lambda functions
├── locales/            # Compiled gettext translation files
├── migrations/         # Alembic migration scripts
├── models/             # SQLModel database models
├── monitoring/         # CloudWatch metrics emission
├── utils/              # Shared utilities (callbacks, messages, emojis, types)
└── views/              # View layer

bin/                    # CI scripts and dev utilities (not shipped in the wheel)
tests/                  # Test suite
.claude/agents/         # Specialist AI agents
.claude/skills/         # Domain knowledge skills (auto-loaded when relevant)
```
