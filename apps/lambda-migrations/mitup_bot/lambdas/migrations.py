import os
from enum import StrEnum, auto
from typing import Any

import structlog
from alembic import command
from alembic.config import Config
from pydantic import BaseModel, ValidationError

from mitup_bot.config import Env
from mitup_bot.logging_config import Component, configure_logging

log = structlog.get_logger(__name__)


class AlembicActions(StrEnum):
    UPGRADE = auto()
    DOWNGRADE = auto()


class MigrationEvent(BaseModel):
    action: AlembicActions
    revision: str


def run_migrations(event: dict[str, Any], context: Any) -> int:
    """Run Alembic upgrade or downgrade from a Lambda event.

    Invokes Alembic programmatically — equivalent to the CLI but usable from a Lambda handler.
    See: https://alembic.sqlalchemy.org/en/latest/api/commands.html
    """
    configure_logging(Env.PROD, Component.LAMBDA, os.environ.get("LOG_LEVEL", "INFO"))

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

        log.info("Migration completed")

    return 0
