# Per-app image for the bot ECS service (the PTB runtime + FastAPI webhook server).
# Built from the repo root as the uv workspace context. Two-stage:
#   stage 1 resolves the locked dependency closure of mitup-bot-app, then bakes the app;
#   stage 2 carries only the resulting virtualenv onto a clean base.

# ---- Stage 1: builder ----------------------------------------------------------------------
FROM python:3.14.6-slim-bookworm AS builder

# uv drives the install. UV_PYTHON_DOWNLOADS=never keeps it on this base image's single system
# interpreter, so the venv we copy to stage 2 links to a python that also exists there.
# UV_COMPILE_BYTECODE trades build time for faster cold starts.
RUN pip install --no-cache-dir uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_FROZEN=1

WORKDIR /src

# Dependency layer: copy only the lockfile and every workspace member's pyproject so uv can resolve
# the workspace. This layer is cached and only busts when the locked dependencies change, not when
# application source changes. --no-install-workspace installs the third-party closure but none of
# the in-repo members.
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
RUN uv sync --frozen --no-default-groups --no-install-workspace --package mitup-bot-app

# Application layer: copy the sources the bot needs (its own tree plus every library it imports).
# CI's build-translations stage lands the compiled .mo catalogs under libs/core before this build,
# so drop the .po/.pot sources and keep only the compiled catalogs in the image.
COPY libs ./libs
COPY apps/bot ./apps/bot
RUN find libs/core/mitup_bot/locales -type f \( -name "*.po" -o -name "*.pot" \) -delete
RUN uv sync --frozen --no-default-groups --no-editable --package mitup-bot-app

# ---- Stage 2: runtime ----------------------------------------------------------------------
FROM python:3.14.6-slim-bookworm AS runtime

WORKDIR /mitup-telegram-bot

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Fail the build loudly if the bot console script cannot resolve its full import graph.
RUN mitup --help > /dev/null

CMD ["mitup", "launch", "--env", "prod"]
