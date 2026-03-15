---
name: project-guide
description: Quick reference guide for the mitup_bot AI setup — agents, skills, and how to write effective prompts.
disable-model-invocation: true
---

Print the following guide in full as formatted markdown. Do not summarize, paraphrase, or condense any section.

---

# mitup_bot — AI Setup Guide

This guide covers all agents and skills available in this project, when to use each, and how to write effective prompts.

---

## Agents

Agents are specialist AI assistants that work in isolated contexts with preloaded domain knowledge. For multi-domain work, use `/em` (a skill that turns the main agent into an orchestrator). For targeted work, invoke a specialist agent directly by name in natural language.

### `handler-expert`

Writes and reviews Telegram handlers in `mitup_bot/handlers/`.

**Use when:** adding or modifying handlers, conversation flows, HandlerId enums, or PTB filters.

### `view-expert`

Builds and modifies screens in `mitup_bot/views/`.

**Use when:** creating or updating MitupView, PaginatedMitupView, ButtonConfig, or factory functions.

### `lambda-expert`

Writes and maintains AWS Lambda functions in `mitup_bot/lambdas/`.

**Use when:** working on Lambda handlers, BotAdapter usage, or cold-start concerns.

### `cli-expert`

Writes and maintains CLI commands in `mitup_bot/cli/`.

**Use when:** adding Click commands, operational scripts, or anything in the production CLI.

### `bot-copywriter`

Writes and reviews all user-facing text in the bot interface.

**Use when:** adding or updating messages, button labels, prompts, or notification text in `messages.py`.

### `docs-writer`

Writes and maintains documentation in `docs/`.

**Use when:** creating or editing MkDocs documentation pages.

### `test-expert`

Writes and reviews pytest tests.

**Use when:** any test needs to be written or modified.

### `translator`

Translates new message catalog strings into all supported languages.

**Use when:** new strings have been added to `messages.py` and `hatch run dev:update-locales` has been run.

### `convention-reviewer`

Audits code for convention violations before CI.

**Use when:** you want a sanity check after implementing something — catches guard misuse, hardcoded text, wrong session decorator, etc.

---

## Skills

Skills are knowledge documents that Claude loads automatically when relevant. You can also invoke user-invocable skills with `/skill-name`.

### Reference skills (auto-loaded, not user-invocable)

| Skill | Loads when… |
|-------|-------------|
| `handler-conventions` | Working on handlers |
| `view-conventions` | Working on views |
| `view-factory` | Building a new screen |
| `lambda-conventions` | Working on Lambda functions |
| `cli-conventions` | Working on CLI commands |
| `bot-copy-style` | Writing bot interface text |
| `docs-style` | Writing documentation |
| `guards` | Writing handler validation |
| `messages` | Adding user-facing strings |
| `database` | Working with models or sessions |
| `api-wrapper` | Using TelegramApiWrapper or BotAdapter |
| `error-handling` | Adding exceptions or error handlers |
| `monitoring` | Adding CloudWatch metrics |
| `translations` | Working with locale files |
| `type-checking` | Writing code with potential type errors |
| `mitup-config` | Working with config fields or environments |
| `ci-pipeline` | Working on `.gitlab-ci.yml` or CI scripts |

### User-invocable task skills

| Skill | Purpose |
|-------|---------|
| `/em <task>` | Orchestration mode — clarify, plan, delegate, checkpoint across multiple agents |
| `/git` | Branching, staging, committing, pushing, rebasing |
| `/mr` | Generate a GitLab MR description from the project template |
| `/new-handler` | Scaffold a new bot handler package |
| `/new-migration` | Generate and validate an Alembic migration |
| `/translate` | Add a new user-facing string to the message catalog |
| `/ty-ignore` | Insert a `ty: ignore` comment with the required issue URL |
| `/create-issue` | Create a GitLab issue using the correct project template and labels |
| `/comment-mr` | Reply to a GitLab MR discussion thread |
| `/project-guide` | This guide |

---

## How to write effective prompts

### Simple, single-domain task — go direct

> Add a /help command that shows a short usage guide.

Describe the outcome. Claude loads the relevant skills and delegates automatically. No need to name agents or describe steps.

### Multi-domain feature — use `/em`

```
/em Add recurring meetings — needs a handler, DB migration, tests, and EN/ES translations.
```

Give the full feature intent after `/em`. State constraints that matter:
- "Keep the conversation flow consistent with how `create_meeting` works."
- "The migration must be reversible."

The main agent enters orchestration mode: clarifies, forms a phased plan, presents it for approval, then delegates to specialist agents with checkpoints.

### Targeted fix — invoke an agent directly

```
Use the handler-expert agent to refactor the registration_process handlers
to use the new UserExistFilter. Entry point is registration_process/entry.py.
```

Use natural language to name the agent explicitly and skip the EM overhead. Provide the specific entry-point file.

### What to avoid

- Don't describe implementation steps — agents have their conventions preloaded.
- Don't pre-assign agents if going through the EM — let it decide.
- Don't paste large code snippets; reference file paths instead.
- Don't add "make sure to use `@with_async_session`" — the handler-expert knows this.
- Don't add "make sure to translate" — the EM will delegate to the translator when needed.
