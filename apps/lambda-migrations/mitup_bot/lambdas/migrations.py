import os
import re
from enum import StrEnum, auto
from typing import Any

import psycopg
import structlog
from alembic import command
from alembic.config import Config
from psycopg import sql
from pydantic import BaseModel, ValidationError

from mitup_bot.config import MITUP_ENV_VAR, DbConfig, Env, EnvVariablesConfigProvider
from mitup_bot.config import Config as MitupConfig
from mitup_bot.db import build_db_url
from mitup_bot.logging_config import Component, configure_logging

log = structlog.get_logger(__name__)

# This Lambda is deployed to production only; nothing about an invocation can change that.
LAMBDA_ENV = Env.PROD

APP_DB_USERNAME_ENV = "MITUP_APP_DB_USERNAME"
APP_DB_PASSWORD_ENV = "MITUP_APP_DB_PASSWORD"

# A Postgres role name we are willing to create: lowercase start, lowercase/digit/underscore body,
# capped at 63 bytes (Postgres' identifier limit). A value outside this shape is a misconfiguration
# that must fail the deploy rather than be quietly quoted into existence.
APP_ROLE_NAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class AlembicActions(StrEnum):
    UPGRADE = auto()
    DOWNGRADE = auto()


class MigrationEvent(BaseModel):
    action: AlembicActions
    revision: str


def app_role_conninfo(db_config: DbConfig) -> str:
    """Render a libpq conninfo URL from the shared `DbConfig`.

    `build_db_url` yields a SQLAlchemy URL whose drivername is the async scheme
    (`postgresql+psycopg`); psycopg's own `connect` wants a plain libpq URL, so the driver suffix
    is dropped and the URL rendered with its (percent-encoded) password intact.
    """
    return build_db_url(db_config).set(drivername="postgresql").render_as_string(hide_password=False)


def app_role_statements(username: str, password: str) -> list[sql.Composed]:
    """Compose the idempotent role-bootstrap statements, executed as the RDS master user.

    Every identifier and literal is quoted through psycopg's `sql` module — the password is a
    `sql.Literal`, never interpolated into a string. Grants and default privileges are issued
    without a `FOR ROLE` clause so they attach to the current (master) user, which owns every
    Alembic-created table and sequence.
    """
    role = sql.Identifier(username)
    return [
        sql.SQL(
            "DO $$ BEGIN "
            "IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = {name}) THEN "
            "CREATE ROLE {role} WITH LOGIN; "
            "END IF; END $$"
        ).format(name=sql.Literal(username), role=role),
        sql.SQL("ALTER ROLE {role} WITH LOGIN PASSWORD {password}").format(role=role, password=sql.Literal(password)),
        sql.SQL("GRANT USAGE ON SCHEMA public TO {role}").format(role=role),
        sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}").format(role=role),
        sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}").format(role=role),
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}"
        ).format(role=role),
        sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {role}").format(
            role=role
        ),
    ]


def bootstrap_app_role() -> None:
    """Create and maintain the least-privilege application role, as the RDS master user.

    Driven by two plain env vars (`MITUP_APP_DB_USERNAME` / `MITUP_APP_DB_PASSWORD`). When either
    is unset or empty the bootstrap is a no-op so local runs and pre-app-role deployments are
    unaffected. Otherwise the role is created if absent, its password reset on every run (so
    rotation is "change the value, redeploy"), and read/write grants plus default privileges
    applied. The password never appears in a log line or exception message.
    """
    username = os.environ.get(APP_DB_USERNAME_ENV, "").strip()
    password = os.environ.get(APP_DB_PASSWORD_ENV, "")

    if not username or not password:
        log.info("App role bootstrap skipped: app credentials not configured")
        return

    if APP_ROLE_NAME_PATTERN.fullmatch(username) is None:
        log.error("App role name is invalid", app_role=username)
        raise ValueError(f"{APP_DB_USERNAME_ENV} must match {APP_ROLE_NAME_PATTERN.pattern}")

    # The Lambda environment carries the full MITUPBOT__DB__* set; only the db section is needed.
    db_config = MitupConfig.db_from_providers(EnvVariablesConfigProvider())

    try:
        with psycopg.connect(app_role_conninfo(db_config), autocommit=True) as connection:
            with connection.cursor() as cursor:
                for statement in app_role_statements(username, password):
                    cursor.execute(statement)
    except Exception:
        log.exception("App role bootstrap failed")
        raise

    log.info("App role ensured", app_role=username)


def run_migrations(event: dict[str, Any], context: Any) -> int:
    """Run Alembic upgrade or downgrade from a Lambda event.

    Invokes Alembic programmatically — equivalent to the CLI but usable from a Lambda handler.
    See: https://alembic.sqlalchemy.org/en/latest/api/commands.html
    """
    # Alembic's `env.py` runs inside this process and reads MITUP_ENV to pick its config providers.
    os.environ[MITUP_ENV_VAR] = LAMBDA_ENV
    configure_logging(LAMBDA_ENV, Component.LAMBDA, os.environ.get("LOG_LEVEL", "INFO"))

    try:
        event_object = MigrationEvent.model_validate(event)
    except ValidationError:
        # A structured record of the bad invocation; re-raised so the Lambda invocation still fails.
        log.exception("Invalid migration event", invocation_event=event)
        raise

    ctx_fields: dict[str, object] = {
        "flow": "migrations",
        "action": event_object.action,
        "revision": event_object.revision,
    }
    if hasattr(context, "aws_request_id"):
        ctx_fields["aws_request_id"] = context.aws_request_id

    with structlog.contextvars.bound_contextvars(**ctx_fields):
        log.info("Migration started")

        # File directly available in the lambda root directory
        config = Config("alembic.ini")

        try:
            if event_object.action is AlembicActions.UPGRADE:
                command.upgrade(config, event_object.revision)

            if event_object.action is AlembicActions.DOWNGRADE:
                command.downgrade(config, event_object.revision)
        except Exception:
            # Without this line a failed migration leaves only the runtime's plain-text traceback,
            # with no structured record of which action/revision was in flight.
            log.exception("Migration failed")
            raise

        # Bootstrap runs after the Alembic command so `GRANT ... ON ALL TABLES` covers tables the
        # first-ever bootstrap run's migration just created; on later runs the default privileges
        # cover newly added tables.
        bootstrap_app_role()

        log.info("Migration completed")

    return 0
