import os
from enum import StrEnum, auto
from typing import Any

import structlog
from alembic import command
from alembic.config import Config
from pydantic import BaseModel

from mitup_bot.config import Env
from mitup_bot.logging_config import configure_logging

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
    configure_logging(Env.PROD, os.environ.get("LOG_LEVEL", "INFO"))

    event_object = MigrationEvent.model_validate(event)

    ctx_fields: dict[str, object] = {
        "lambda": "migrations",
        "action": event_object.action,
        "revision": event_object.revision,
    }
    if hasattr(context, "aws_request_id"):
        ctx_fields["aws_request_id"] = context.aws_request_id

    with structlog.contextvars.bound_contextvars(**ctx_fields):
        log.info("migration.start")

        # File directly available in the lambda root directory
        config = Config("alembic.ini")

        if event_object.action is AlembicActions.UPGRADE:
            command.upgrade(config, event_object.revision)

        if event_object.action is AlembicActions.DOWNGRADE:
            command.downgrade(config, event_object.revision)

        log.info("migration.done")

    return 0
