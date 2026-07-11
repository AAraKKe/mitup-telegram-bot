---
name: ci-pipeline
description: GitLab CI pipeline structure and validation jobs. Auto-load when working on .gitlab-ci.yml, CI scripts, or understanding pipeline stages.
user-invocable: false
---

# CI Pipeline

The CI runs on GitLab CI/CD. The root `.gitlab-ci.yml` only `include`s the component files under
`.gitlab/ci/` (plus the GitLab-provided SAST / Dependency-Scanning / Secret-Detection templates).
Every job runs on a project-built image and drives the repo through the `mb` dev CLI (`tools/mb/`),
never `python` directly.

## Stages

```txt
build-ci → pre-flight → analysis → build → test → prepare-deployment → push-ecr → deploy → documentation → validate
```

Most development work interacts with the **build** and **test** stages. The stages from
`prepare-deployment` onward only do work on the default branch, the `release` branch, or tags.

## The CI image and the MR-image mechanism (`base.yml`, `docker.yml`)

Jobs run on `registry.gitlab.com/meetupbot/mitup-telegram-bot/ci-python:${CI_IMAGE_TAG}`, built from
`dev/docker/Dockerfile.ci`. That image pre-installs every locked dependency so jobs only sync the
workspace sources. Two Dockerfile details matter:

- `ENV UV_PROJECT_ENVIRONMENT=/opt/uv-venv` keeps the venv **outside** the checkout, which GitLab
  wipes on every job.
- `ENV UV_FROZEN=1` makes every job-time `uv run` / `uv sync` respect `uv.lock` instead of
  re-resolving.

Because the image bakes in dependencies, it must be rebuilt when dependency metadata changes. The
`workflow.rules` in `base.yml` watch the `.ci-docker-files` anchor (the CI Dockerfile, `uv.lock`,
the root and every member `pyproject.toml`, and the CI YAML). When an MR touches one of those,
`CI_IMAGE_TAG` is set to `mr-${CI_MERGE_REQUEST_IID}` and the `build-docker-ci` job (stage
`build-ci`) builds and pushes that MR-scoped image before the rest of the pipeline uses it.
Otherwise `CI_IMAGE_TAG` stays `latest`. On `main`/`release`, a change to those files rebuilds and
republishes `:latest`; `[build-ci]` in a commit title forces the build; a manual trigger is the
fallback.

`FORCE_COLOR: "1"` is a global variable in `base.yml`: GitLab renders ANSI in job logs, and `mb`
and pytest key their color output off it (animations stay off — `mb` only animates on a TTY).

## Build stage (`test.yml`)

| Job | What it does |
|-----|-------------|
| `preparation` | Verifies Python and uv, times `uv sync --frozen`, then runs `uv lock --check` to reject a stale lock, and prints `uv tree` |
| `build-translations` | Compiles the locale `.mo` catalogs (`uv run mb locales build`) and publishes `libs/core/mitup_bot/locales` as a 1-day artifact that later jobs consume |
| `validate-ci-languages` | `uv run mb ci check-languages` — the CI language matrix matches the supported languages |

## Test stage (`test.yml`)

| Job | What it does | Notable rules |
|-----|-------------|---------------|
| `test-suite` | The whole suite as one `parallel:matrix` job running `uv run mb test --member $TARGET --cov` per entry: every member runs once, and the language-rendering members (`apps/bot`, `libs/telegram`, `apps/events`) split into a `-m "not i18n"` run plus a `-m i18n` run per supported language. `mb test` builds stale locales itself | Default branch always; otherwise one deliberately broad `changes:` trigger (`apps/**`, `libs/**`, `tools/**`, `tests/**`, the lock, the root `pyproject.toml`, the job YAML) — no per-member path list to forget, so a new test is never silently skipped |
| `coverage-report` | `uv run coverage combine .coverage.*` over the raw data files from every `test-suite` entry and `test-db`, then `coverage xml` + `coverage report`. The **only** job carrying the coverage regex and the Cobertura artifact: GitLab's headline number averages per-job percentages, so the matrix entries publish raw data files and never report their partial slices | Same trigger as `test-suite` |
| `format-check` | `uv run mb format --check` | `allow_failure` on the default branch |
| `linter` | `uv run mb lint` | `allow_failure` on the default branch |
| `type-check` | `uv run mb typecheck` (project + `tools/mb`) | always |
| `check-ty-ignores` | `uv run mb ci check-ty-ignores` — every `ty` suppression carries a live issue URL | MRs only, `allow_failure` |
| `validate-migrations` | `uv run mb db migrate validate` (single head, clean upgrade path) | always |
| `check-import-isolation` | `uv run mb ci check-import-isolation` — each library imports with only its own declared deps | always |
| `validate-ids` | `uv run mb locales validate-ids` — every message in code exists in the English catalog | always |
| `validate-locales` | `uv run mb locales validate` — every non-English catalog carries the same msgids as English | always |
| `test-db` | `uv run mb test --db --cov tests/data/db_behavior/` against a `docker:dind` Postgres service — serial (shared Postgres), with its raw coverage file feeding `coverage-report` | Same trigger as `test-suite` |
| `validate-local-setup` | On a clean `python:3.14-bookworm` image, proves the fresh-contributor bootstrap (install uv → `uv sync --frozen` → `mb --help`) and that the commit-message hook (`mb ci check-commit`) rejects/accepts/idempotently re-validates | `allow_failure` on the default branch |

The deployable-image builds are one matrixed job (`validate-docker-build.yml`): `validate-docker`
builds all four `apps/X/Dockerfile`s in a `parallel:matrix`, **only on MRs**, behind one broad
trigger (`apps/**`, `libs/**`, `uv.lock`, the root `pyproject.toml`, `.dockerignore`, the job's own
YAML) — any of those can affect any image, and a curated per-app list risks a green pipeline with an
unvalidated build. The non-deployed `validate-docker-ci` / `validate-docker-dev` jobs keep their own
single-file triggers (`dev/docker/Dockerfile.{ci,dev}`). These jobs never push; they only prove the
image still builds before the change reaches `main`. All four matrix entries pull the
`build-translations` artifact: the bot/events images bake in the compiled catalogs, and the Lambda
entries just ignore it since they localize nothing.

## Deploy flow (`deploy-config.yml`, `ecr-push.yml`, `deploy.yml`)

Deployment runs on `main` (→ staging) and `release` (→ production). Four images are deployed: **bot**,
**events**, **migrations-lambda**, and **alarm-action-lambda**.

1. **`config-{staging,prod}`** (`prepare-deployment`) authenticate via a job-scoped OIDC token
   (`.aws-prep`), describe the ECR repos, and write each image's URI — tagged `:ci-${CI_COMMIT_SHORT_SHA}` —
   into a `dotenv` artifact (`BOT_IMAGE_TAG`, `EVENTS_IMAGE_TAG`, `MIGRATIONS_IMAGE_TAG`,
   `ALARM_ACTION_IMAGE_TAG`). Every downstream job authenticates with **its own** OIDC token rather
   than inheriting credentials, because a job can queue for a long time behind a deploy bake.
2. **`push-*-{staging,prod}`** (`push-ecr`) build each app's `apps/X/Dockerfile` and push the
   sha-tagged image plus `:latest` to ECR. These push jobs stay **grouped** (not per-path): a deploy
   resolves every image by the current commit sha, so each `:ci-<sha>` tag must exist whenever a
   deploy runs. The registry build-cache makes unchanged images near-instant to re-push. (Path
   gating lives on the MR-only `validate-docker` matrix job, where there is no deploy to satisfy.)
3. **`deploy-{staging,production}`** (`deploy`) run `uv run mb deploy --migrations-image … --bot-image …
   --alarm-action-image … --events-image …`, which applies the Alembic migrations (migrations
   Lambda) and rolls out the ECS services.

`config-staging` blocks on the quality gates via `needs:` (`test-suite`, `test-db`, `type-check`,
`validate-locales`, `validate-migrations`, `semgrep-sast`, `secret_detection`) — formatting/style jobs are deliberately
excluded so they never block a deploy.

## Commit-title deploy switches

The push and deploy jobs read the commit title:

- **`[no-deploy]`** — hard `when: never` on every push/deploy/docs job. Use it on cleanup or
  infra-only merges that must not trigger a staging roll.
- **`[force-deploy]`** — on the default branch, deploy even when no `.deployment-files` path changed
  (the normal trigger is a change under `.deployment-files`: `apps/**`, `libs/**`, `uv.lock`, the
  root `pyproject.toml`, `.dockerignore`, and the deploy-related CI YAML).

## Resource groups

Each environment-touching job declares a `resource_group` (e.g. `deploy-staging`, `bot-tagged-prod`,
`docs-staging`) so GitLab serializes concurrent pipelines onto the same target and a new deploy
cannot overtake an in-flight one.

## Documentation (`docs.yml`)

`build-docs` (stage `prepare-deployment`, `uv run mb docs build`) runs when `.docs-files` change
(`docs/**`, `zensical.toml`, `tools/mb/src/mb/docs_ops.py`, and the docs CI YAML). `push-docs-*`
(stage `documentation`, `uv run mb docs publish`) sync the built `site/` to S3 and invalidate
CloudFront — **after** the deploy, so docs never describe a bot that has not rolled out yet.
`publish_docs_manual` is an on-demand default-branch job.

## Running validation locally

Before pushing, run the full local gate — it mirrors the test-stage jobs:

```bash
uv run mb validate
```

This runs formatting, linting, type checking, and tests, then prints a summary table. Individual
commands are available — run `uv run mb --help` for the full surface (defined in `tools/mb/`).

## Issue and MR templates

GitLab templates live in `.gitlab/issue_templates/` and `.gitlab/merge_request_templates/`. When
linking to a new issue, use the template URL format:

```
https://gitlab.com/meetupbot/mitup-telegram-bot/-/issues/new?issuable_template=TemplateName
```
