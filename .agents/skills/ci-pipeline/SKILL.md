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
`prepare-deployment` onward run the deploy and docs-publish work only on `v*` tag pipelines.

## The CI image and the MR-image mechanism (`base.yml`, `docker.yml`)

Jobs run on `registry.gitlab.com/meetupbot/mitup-telegram-bot/ci-python:${CI_IMAGE_TAG}`, built from
`dev/docker/Dockerfile.ci`. That image pre-installs every locked dependency so jobs only sync the
workspace sources. Two Dockerfile details matter:

- `ENV UV_PROJECT_ENVIRONMENT=/opt/uv-venv` keeps the venv **outside** the checkout, which GitLab
  wipes on every job.
- `ENV UV_FROZEN=1` makes every job-time `uv run` / `uv sync` respect `uv.lock` instead of
  re-resolving. One caveat: for `uv lock` the same flag maps to `--check-exists`, so a bare
  `uv lock --check` job would only confirm the lock file exists, never that it is up to date. The
  `validate-lock` job goes through `mb ci check-lock`, which strips `UV_FROZEN` to force the real
  drift comparison.

Because the image bakes in dependencies, it must be rebuilt when dependency metadata changes. The
`workflow.rules` in `base.yml` watch the `.ci-docker-files` anchor (the CI Dockerfile, `uv.lock`,
the root and every member `pyproject.toml`, and the CI YAML). When an MR touches one of those,
`CI_IMAGE_TAG` is set to `mr-${CI_MERGE_REQUEST_IID}` and the `build-docker-ci` job (stage
`build-ci`) builds and pushes that MR-scoped image before the rest of the pipeline uses it.
Otherwise `CI_IMAGE_TAG` stays `latest`. On `main`, a change to those files rebuilds and
republishes `:latest`; `[build-ci]` in a commit title forces the build; a manual trigger is the
fallback.

`FORCE_COLOR: "1"` is a global variable in `base.yml`: GitLab renders ANSI in job logs, and `mb`
and pytest key their color output off it (animations stay off — `mb` only animates on a TTY).

## DAG: jobs start on `needs`, not stages

Stages exist for display grouping; execution order comes from explicit `needs`. Most jobs carry
`needs: [{job: build-docker-ci, optional: true}]` — the one real prerequisite every ci-image job
has. `build-docker-ci` exists in the pipeline only when the image must actually be rebuilt (an MR
touching `.ci-docker-files`, `main` with such changes, a `[build-ci]` title); then everything
waits for the fresh image. In every other pipeline the job is absent and `optional: true` lets
dependents start immediately. The on-demand rebuild is the separate `build-docker-ci-manual` job,
which nothing `needs` — an unplayed manual job in a `needs` list silently SKIPS all its dependents
while the pipeline still reports success, so never point `needs` at a manual job.
Artifact consumers name their producers instead (`test-suite` → `build-translations`, the pushes →
`build-translations` + `deploy-config`, `deploy` → the four pushes, `push-docs` → `deploy`).
When adding a job, give it explicit `needs` — a job with none waits for every earlier stage and
re-serializes the pipeline.

All jobs are `interruptible: true` via `default:` (with `workflow.auto_cancel.on_new_commit:
interruptible`), so a force-push cancels the superseded pipeline's runs. The deploy-path jobs
(`deploy-config`, `.push-ecr`, `deploy`, `deploy:refresh`, `.push-docs`) opt out with
`interruptible: false` — a production roll-out must never be cancelled by a newer commit.

## Pre-flight stage

| Job | What it does |
|-----|-------------|
| `validate-lock` (`test.yml`) | `uv run mb ci check-lock` — fails when `uv.lock` has drifted from any workspace `pyproject.toml`. Sits in `pre-flight` (after the CI image exists, before `build`/`test`) so a stale lock short-circuits the pipeline before any translation build or the matrix runs |
| `auto-format` (`update-renovate.yml`) | On `renovate/*` MR branches, runs `mb fix` and pushes a formatting-fix commit back to the branch |

## Build stage (`test.yml`)

| Job | What it does |
|-----|-------------|
| `preparation` | Visibility canary: verifies Python and uv, times `uv sync --frozen`, and prints `uv tree`. Nothing consumes its output — it is `allow_failure: true` and no job `needs` it |
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

Every test-stage job — plus `validate-ci-languages` — carries the shared `.skip-on-tags` rule
anchor (`{ if: $CI_COMMIT_TAG, when: never }`, defined at the top of `test.yml`), so **none of them
run on a `v*` tag pipeline**. A tag pipeline is deploy-only (see [Deploy flow](#deploy-flow-deploy-configyml-ecr-pushyml-deployyml)); only `preparation`,
`build-translations`, and `build-docs` from the earlier stages run on a tag, because the deploy needs
their artifacts. The "always"/trigger notes above therefore describe MR and `main` pipelines.

Every repo-owned job outside the tag-gated deploy path also carries a **schedule exclusion**
(`.skip-on-schedules` in `test.yml`, inline `when: never` rules in `docker.yml`/`docs.yml`):
scheduled pipelines exist solely for the hourly `crowdin-pull` (see [Crowdin sync](#crowdin-sync-crowdinyml)).
`rules:changes` evaluates to **true** on scheduled pipelines and the default-branch/`on_success`
rules match them too, so a job added without the exclusion silently rides the hourly schedule —
give every new job either a tag/MR `if` gate or the schedule splice.

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

Deployment targets the production account only and runs on **`v*` tag pipelines** — a release tag is
cut with `uv run mb release`. Four images are deployed: **bot**, **events**, **migrations-lambda**,
and **alarm-action-lambda**.

There is a single set of deploy jobs (no staging/prod pairs): they still carry
`environment: name: production` for GitLab deployment tracking, but the names are unqualified.

1. **`deploy-config`** (`prepare-deployment`) authenticates via a job-scoped OIDC token
   (`.aws-prep`), describes the ECR repos, and writes each image's URI — tagged `:ci-${CI_COMMIT_SHORT_SHA}` —
   into a `dotenv` artifact (`BOT_IMAGE_TAG`, `EVENTS_IMAGE_TAG`, `MIGRATIONS_IMAGE_TAG`,
   `ALARM_ACTION_IMAGE_TAG`). Every downstream job authenticates with **its own** OIDC token rather
   than inheriting credentials, because a job can queue for a long time behind a deploy bake.
2. **`push-bot` / `push-migrations-lambda` / `push-alarm-action-lambda` / `push-events`**
   (`push-ecr`) build each app's `apps/X/Dockerfile` and push the sha-tagged image plus `:latest` to
   ECR. They pull only the `build-translations` (compiled catalogs baked into the bot/events images)
   and `deploy-config` artifacts. These push jobs stay **grouped** (not per-path): a deploy resolves every
   image by the current commit sha, so each `:ci-<sha>` tag must exist whenever a deploy runs. The
   registry build-cache makes unchanged images near-instant to re-push. (Path gating lives on the
   MR-only `validate-docker` matrix job, where there is no deploy to satisfy.)
3. **`deploy`** (`deploy`) runs `uv run mb deploy --migrations-image … --bot-image …
   --alarm-action-image … --events-image …`, which applies the Alembic migrations (migrations
   Lambda) and rolls out the ECS services.

Every deploy-path job gates on `$CI_COMMIT_TAG =~ /^v/`, and the whole `test` stage is skipped on
tags, so a tag pipeline runs only `preparation` → `build-translations`/`build-docs` → `deploy-config` →
`push-*` → `deploy` → `push-docs`. The quality gates are **not** re-run on the tag: `mb release`
refuses to cut the tag unless `main`'s pipeline for that commit was already green, and the
`GitLabCI-Service` OIDC trust is scoped to `v*` tag refs so only a tag pipeline can assume the deploy
role.

## Manual jobs on `main` must carry `allow_failure: true`

An unplayed `when: manual` job without `allow_failure: true` pins its pipeline's overall status at
`manual` instead of `success` — and `mb release` refuses to cut a tag unless the latest `main`
pipeline reports `success`. Every manual job on default-branch pipelines (`deploy:refresh`,
`build-docker-ci`'s fallback rule) therefore sets `allow_failure: true` on the manual rule. Apply
the same to any new manual job.

## Commit-title deploy switch

The push and deploy jobs read the tagged commit's title:

- **`[no-deploy]`** — hard `when: never` on every push/deploy/docs job. On a `v*` tag pipeline it
  suppresses the roll-out even though the tag matched; use it only when a tagged commit must not deploy.

## Resource groups

Each deploy-touching job declares a `resource_group` (e.g. `deploy`, `bot`, `docs`) so GitLab
serializes concurrent pipelines onto the same target and a new deploy cannot overtake an in-flight
one.

## Documentation (`docs.yml`)

`build-docs` (stage `prepare-deployment`, `uv run mb docs build`) runs when `.docs-files` change
(`docs/**`, `zensical.toml`, `tools/mb/src/mb/docs_ops.py`, and the docs CI YAML) **and on every
`v*` tag** — the tag trigger is required because `push-docs` runs on the tag and depends on the
`build-docs` artifact. `push-docs` (stage `documentation`, `uv run mb docs publish`) syncs the built
`site/` to S3 and invalidates CloudFront — **after** the deploy, so docs never describe a bot that
has not rolled out yet. There is no out-of-band docs publish job: docs go live with the next
release, or run `uv run mb docs build && uv run mb docs publish` locally under operator
credentials.

## Crowdin sync (`crowdin.yml`)

Two jobs drive the Crowdin round-trip through `mb locales push` / `mb locales pull` (see the
`translations` skill for the sync semantics):

- **`crowdin-push`** (stage `validate`) runs on every default-branch push, after
  `validate-locales`, and uploads the English catalog plus any repo translations Crowdin lacks.
  Needs the masked `CROWDIN_API_KEY` CI variable.
- **`crowdin-pull`** (stage `build`, `needs: []`) runs only on schedules that set
  `SCHEDULE_REASON="crowdin-sync"` — every scheduled job gates on its own `SCHEDULE_REASON`
  value, so adding a new schedule for another purpose never triggers this one. It
  pulls approved translations, validates the catalogs, and runs `mb locales create-mr`
  (`tools/mb/src/mb/crowdin_mr_ops.py`), which — when the result differs from the scheduled
  ref — force-pushes the `crowdin-translations` branch with push options that create or update
  a single open MR. Needs `CROWDIN_API_KEY` plus `CROWDIN_GIT_TOKEN` — a PAT of the
  crowdin-sync service account (Developer on the project, `api` + `write_repository`).
  `create-mr` commits as the token's owner (fetched from `GET /user`) because the project's
  committer-email push rule rejects any identity the pushing user doesn't own — never
  hardcode a committer identity in a CI push job. Labeling the open MR `crowdin::hold` makes
  `create-mr` exit before touching anything (for manual pre-merge fixes on the branch); removing
  the label resumes the hourly rebuilds — which discard any manual commits on the branch.

The hourly pipeline schedule targets the default branch and must define
`SCHEDULE_REASON="crowdin-sync"` plus `SAST_DISABLED`, `DEPENDENCY_SCANNING_DISABLED` and
`SECRET_DETECTION_DISABLED` (all `"true"`) as schedule variables — the GitLab security templates
don't honor the repo's schedule exclusions, and those documented kill-switch variables are what
keeps the scanners off the hourly run.

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
