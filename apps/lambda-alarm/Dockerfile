# Per-app image for the alarm-action Lambda (forwards CloudWatch alarm state changes to GitLab).
# PTB-free, SQLAlchemy-free and FastAPI-free by construction: its only deps are boto3, httpx and
# mitup-core. Same export + pip-install-target pattern as the migrations image.

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
    --package mitup-lambda-alarm-app --format requirements.txt -o requirements.txt && \
    pip install --no-cache-dir --target /install -r requirements.txt

# In-repo members as wheels: the handler plus the single library it imports (core).
COPY libs/core ./libs/core
COPY apps/lambda-alarm ./apps/lambda-alarm
RUN uv build --wheel --package mitup-core -o /wheels && \
    uv build --wheel --package mitup-lambda-alarm-app -o /wheels && \
    pip install --no-cache-dir --target /install --no-deps /wheels/*.whl

# ---- Stage 2: runtime ----------------------------------------------------------------------
FROM public.ecr.aws/lambda/python:3.14 AS runtime

WORKDIR ${LAMBDA_TASK_ROOT}
ENV PYTHONPATH="${LAMBDA_TASK_ROOT}"

COPY --from=builder /install ${LAMBDA_TASK_ROOT}

# Acceptance guard: this image must never carry PTB, SQLAlchemy or FastAPI. Fail loudly if it does.
RUN if python -c "import telegram" 2>/dev/null; then echo "FORBIDDEN: python-telegram-bot present"; exit 1; fi; \
    if python -c "import sqlalchemy" 2>/dev/null; then echo "FORBIDDEN: sqlalchemy present"; exit 1; fi; \
    if python -c "import fastapi" 2>/dev/null; then echo "FORBIDDEN: fastapi present"; exit 1; fi; \
    echo "OK: no PTB / SQLAlchemy / FastAPI in the alarm image"

CMD ["mitup_bot.lambdas.alarm_action.handler"]
