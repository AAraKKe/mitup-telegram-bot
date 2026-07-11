# Per-app image for the migrations Lambda (runs Alembic upgrade/downgrade against RDS).
# PTB-free and FastAPI-free by construction. Built from the repo root as the uv workspace context.
#
# Pattern: export the locked third-party closure of the app to a requirements file and pip-install
# it into the Lambda task root, then install the in-repo namespace members as built wheels. This
# keeps the image to exactly the declared dependency set (alembic + mitup-core + mitup-data).

# ---- Stage 1: builder ----------------------------------------------------------------------
FROM public.ecr.aws/lambda/python:3.14 AS builder

RUN pip install --no-cache-dir uv

WORKDIR /src

# Resolve the workspace: the lockfile plus every member's pyproject.
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

# Third-party closure only (--no-emit-workspace drops the in-repo members).
RUN uv export --frozen --no-emit-workspace --no-hashes --no-default-groups \
    --package mitup-lambda-migrations-app --format requirements.txt -o requirements.txt && \
    pip install --no-cache-dir --target /install -r requirements.txt

# In-repo members as wheels: the handler plus its full workspace closure — core, monitoring
# (mitup-data imports mitup_bot.monitoring at runtime), and data.
COPY libs/core ./libs/core
COPY libs/monitoring ./libs/monitoring
COPY libs/data ./libs/data
COPY apps/lambda-migrations ./apps/lambda-migrations
RUN uv build --wheel --package mitup-core -o /wheels && \
    uv build --wheel --package mitup-monitoring -o /wheels && \
    uv build --wheel --package mitup-data -o /wheels && \
    uv build --wheel --package mitup-lambda-migrations-app -o /wheels && \
    pip install --no-cache-dir --target /install --no-deps /wheels/*.whl

# ---- Stage 2: runtime ----------------------------------------------------------------------
FROM public.ecr.aws/lambda/python:3.14 AS runtime

WORKDIR ${LAMBDA_TASK_ROOT}
ENV PYTHONPATH="${LAMBDA_TASK_ROOT}"

COPY --from=builder /install ${LAMBDA_TASK_ROOT}

# The handler loads alembic.ini from the task root. The repo-root ini points at the source tree
# (libs/data/mitup_bot/migrations); rewrite it to the installed namespace path inside the image.
COPY alembic.ini ${LAMBDA_TASK_ROOT}/alembic.ini
RUN sed -i 's#^script_location = .*#script_location = mitup_bot/migrations#' ${LAMBDA_TASK_ROOT}/alembic.ini

# Acceptance guard: this image must never carry PTB or FastAPI. Fail the build loudly if it does.
RUN if python -c "import telegram" 2>/dev/null; then echo "FORBIDDEN: python-telegram-bot present"; exit 1; fi; \
    if python -c "import fastapi" 2>/dev/null; then echo "FORBIDDEN: fastapi present"; exit 1; fi; \
    echo "OK: no PTB / FastAPI in the migrations image"

# Positive guard: the handler's REAL import graph includes alembic's env.py, which loads
# mitup_bot.db (and through it monitoring and the models) only at run time — a bare handler
# import would pass with those missing. Import everything env.py imports so a missing member
# fails the build instead of the deploy.
RUN python -c "import mitup_bot.lambdas.migrations; import mitup_bot.db; import mitup_bot.models.users" && \
    echo "OK: handler + alembic env import graph resolves"

CMD ["mitup_bot.lambdas.migrations.run_migrations"]
