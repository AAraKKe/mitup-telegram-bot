# Claude Code — Project Instructions

## Role

Act as an experienced Python engineer. Write idiomatic, modern Python using features available in the version declared in `pyproject.toml` (see `requires-python`).

## Working with agents

For complex tasks that span multiple areas (handler + migration + tests + translations, etc.), use the `/em` skill to enter orchestration mode. It clarifies requirements, forms a phased plan, presents it for approval, then delegates to specialist agents with checkpoints between phases:

```
/em Add recurring meetings — needs a handler, DB migration, tests, and EN/ES translations.
```

For single-domain tasks or targeted fixes, **always delegate to the appropriate specialist agent** — do not implement it directly. Specialists carry their area's conventions and stay in sync with the skills they own; the orchestrator session does not. Check the table below to find the right agent:

| Work involves | Delegate to |
|---|---|
| Files in `mitup_bot/handlers/` or `mitup_bot/utils/callbacks.py` | `handler-expert` |
| Files in `mitup_bot/views/` | `view-expert` |
| Files in `tests/` | `test-expert` |
| Files in `mitup_bot/lambdas/` | `lambda-expert` |
| Files in `mitup_bot/cli/` | `cli-expert` |
| User-facing message text / button labels | `bot-copywriter` |
| Translation `.po`/`.pot` files | `translator` |
| Documentation in `docs/` | `docs-writer` |

**After any specialist agent finishes**, run the `convention-reviewer` agent on the files it touched before considering the task done. If the reviewer reports violations, **resume the specialist agent that made the changes** and pass it the full violation report — the specialist already has the full context of every change it made, while you would be reconstructing it from a diff.

The canonical reference for available agents is `.claude/agents/` (one file per agent). The canonical reference for available skills is `.claude/skills/` (one directory per skill, each with a `SKILL.md`). Skills with `user-invocable: true` in their frontmatter can be run as `/<skill-name>`; the rest auto-load when their `description` triggers match the current work.

## Important rules

- **Hooks own validation.** Tests, linters, formatters, and the type checker run automatically via hooks — once after each modification (lint/format/type-check) and once when work is complete (tests). Running them manually duplicates work and floods the conversation with output you don't need. If you want feedback mid-work on a specific test, run it directly: `hatch run dev -- <pytest args>`. Avoid full runs.
- **Never run `python` directly.** The system Python has no project dependencies. Use `hatch run dev:python python <args>` to execute Python in the project's managed environment.
- **Prefer scripts for bulk edits.** When the same change needs to land across many files, write a small script rather than editing each one by hand — it's faster and saves the conversation context for actual reasoning.

## When hooks fail

If a hook fails, don't try to fix it yourself — the failure usually involves area-specific conventions a specialist already knows.

**If the work was done by a specialist agent:** resume that agent's session with the error output. The resumed agent retains full context of every change it made.

**If the work was done directly:** delegate to the appropriate specialist and pass it the full `git diff` of the changes alongside the error output — not a prose summary. The diff is the only way the specialist sees the exact state of the code.

| Failing hook | Delegate to |
|--------------|-------------|
| Type checker (`ty`) | the specialist that owns the failing file (see the table above), with an explicit instruction to load the `type-checking` skill |
| Tests (`pytest`) | `test-expert` |
| Linter (`ruff`) | the specialist that owns the failing file (see the table above), with an explicit instruction to load the `coding-standards` skill |

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
- **Creating issues**: use the `/create-issue` skill. It prompts for the issue type, fills in the correct template, and creates the issue via `glab`. Never invent labels — each template already embeds the correct `/label` quick-action lines.
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
