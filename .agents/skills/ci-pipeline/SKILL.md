---
name: ci-pipeline
description: GitLab CI pipeline structure and validation jobs. Auto-load when working on .gitlab-ci.yml, CI scripts, or understanding pipeline stages.
user-invocable: false
---

# CI Pipeline

The CI runs on GitLab CI/CD. The root `.gitlab-ci.yml` includes component files from `.gitlab/ci/`.

## Stages

```txt
build-ci → pre-flight → analysis → build → test → prepare-deployment → push-ecr → deploy → documentation → validate
```

Most development work interacts with the **build** and **test** stages.

## Key test jobs

| Job | What it does | Runs on |
|-----|-------------|---------|
| `preparation` | Verifies Python and uv, times `uv sync --frozen`, prints the dependency tree | Always |
| `build-translations` | Compiles locale `.mo` files (`uv run mb locales build`) | Always |
| `test` | Runs raw `uv run pytest` with the coverage/JUnit/JSON report flags the local `mb test` omits | Master + changes to `mitup_bot/`, `tests/`, `tools/`, `pyproject.toml`, `uv.lock`, `dev/` |
| `format-check` | `uv run mb format --check` | Always |
| `linter` | `uv run mb lint` | Always |
| `type-check` | `uv run mb typecheck` (root project + `tools/mb`) | Always |
| `check-ty-ignores` | `uv run mb ci check-ty-ignores` — scans for stale `ty: ignore` suppressions | Merge requests only (`allow_failure: true`) |
| `validate-migrations` | Validates Alembic migration graph (`uv run mb db migrate validate`) | Always |
| `validate-locales` | Ensures all messages have locale entries (`uv run mb locales validate`) | Always |
| `validate-local-setup` | Proves the fresh-contributor bootstrap (install uv → `uv sync` → `mb` smoke tests) on a clean image | Always |

## Running validation locally

Before pushing, run the full local validation suite:

```bash
uv run mb validate
```

This runs formatting, linting, type checking, and tests, then prints a summary table. Individual commands are available — run `uv run mb --help` for the full surface (defined in `tools/mb/`).

## Issue and MR templates

GitLab templates are in `.gitlab/issue_templates/` and `.gitlab/merge_request_templates/`:

- **Bug** — bug report with repro steps
- **Feature Proposal** — new feature discussion
- **Task** — general task with acceptance criteria
- **New Language Request** — translation request
- **Improve Documentation** — docs improvement
- **Translation** — fix wrong/missing translations
- **Service Desk Request** — external request with maintainer checklist

When linking to a new issue, use the template URL format:

```
https://gitlab.com/meetupbot/mitup-telegram-bot/-/issues/new?issuable_template=TemplateName
```
