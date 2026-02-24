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
| `preparation` | Verifies Python, pip, hatch, and prints environment info | Always |
| `build-translations` | Compiles locale `.mo` files | Always |
| `test` | Runs `hatch run dev:test-cov` with coverage and JUnit reports | Master + changes to `mitup_bot/`, `tests/`, `pyproject.toml`, `dev/` |
| `format-check` | `ruff format --check` | Always |
| `linter` | `ruff check` | Always |
| `type-check` | `ty check` (via `hatch run dev:type-check`) | Always |
| `check-ty-ignores` | Scans for stale `ty: ignore` suppressions | Merge requests only (`allow_failure: true`) |
| `validate-migrations` | Validates Alembic migration graph | Always |
| `validate-locales` | Ensures all messages have locale entries | Always |
| `validate-local-setup` | Tests the contributor setup script on a clean image | Always |

## Running validation locally

Before pushing, run the full local validation suite:

```bash
hatch run dev:validate
```

This runs formatting, linting, type checking, and tests sequentially. Individual commands are available — see `pyproject.toml` under `[tool.hatch.envs.dev.scripts]`.

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
