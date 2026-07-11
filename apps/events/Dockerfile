# Per-app image for the recurrent-events worker ECS service (the periodic scheduler and its
# maintenance/notification/broadcast jobs). Same two-stage uv pattern as the bot image.
#
# NOTE: this image is built and validated in CI but is not yet deployed. The events ECS service
# still runs the bot image today; giving it this slim image is an infra follow-up (dedicated ECR
# repo + task-definition image swap + an --events-image flag on `mb deploy`). See MR / issue #247.

# ---- Stage 1: builder ----------------------------------------------------------------------
FROM python:3.14.0-slim-bookworm AS builder

RUN pip install --no-cache-dir uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_FROZEN=1

WORKDIR /src

# Dependency layer, cached on the locked dependency closure (see apps/bot/Dockerfile for details).
COPY uv.lock pyproject.toml ./
COPY libs/core/pyproject.toml libs/core/pyproject.toml
COPY libs/monitoring/pyproject.toml libs/monitoring/pyproject.toml
COPY libs/data/pyproject.toml libs/data/pyproject.toml
COPY libs/telegram/pyproject.toml libs/telegram/pyproject.toml
COPY libs/patreon/pyproject.toml libs/patreon/pyproject.toml
COPY apps/bot/pyproject.toml apps/bot/pyproject.toml
COPY apps/events/pyproject.toml apps/events/pyproject.toml
COPY apps/lambda-migrations/pyproject.toml apps/lambda-migrations/pyproject.toml
COPY apps/lambda-alarm/pyproject.toml apps/lambda-alarm/pyproject.toml
COPY tools/mb/pyproject.toml tools/mb/pyproject.toml
COPY tools/rails-migration/pyproject.toml tools/rails-migration/pyproject.toml
RUN uv sync --frozen --no-default-groups --no-install-workspace --package mitup-events-app

# Application layer. The worker localizes the notifications it sends, so it needs the compiled .mo
# catalogs (landed under libs/core by CI's build-translations stage); drop only the .po/.pot sources.
COPY libs ./libs
COPY apps/events ./apps/events
RUN find libs/core/mitup_bot/locales -type f \( -name "*.po" -o -name "*.pot" \) -delete
RUN uv sync --frozen --no-default-groups --no-editable --package mitup-events-app

# ---- Stage 2: runtime ----------------------------------------------------------------------
FROM python:3.14.0-slim-bookworm AS runtime

WORKDIR /mitup-telegram-bot

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Fail the build loudly if the worker console command cannot resolve its full import graph.
RUN mitup recurrent-events --help > /dev/null

CMD ["mitup", "recurrent-events", "--env", "prod"]
