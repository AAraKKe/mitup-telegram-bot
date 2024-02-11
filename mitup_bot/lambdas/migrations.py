from enum import StrEnum, auto
from typing import Any

from alembic import command
from alembic.config import Config
from pydantic import BaseModel


class AlembicActions(StrEnum):
    UPGRADE = auto()
    DOWNGRADE = auto()


class MigrationEvent(BaseModel):
    action: AlembicActions
    revision: str


def run_migrations(event: dict[str, Any], context: Any) -> int:
    """This a handler for the migrations lambda.

    With this lambda we run migrations through alembic as if we were executing the CLI but
    programamtically. This can be adchieved through the command module of alembic.

    The event sent to the lambda triggers what type of migration we want to achieve and run
    it accordingly.

    More information can be found here: https://alembic.sqlalchemy.org/en/latest/api/commands.html

    Args:
        event (dict[str, Any]): This is the event provided to the lambda as a JSON object.
        context (lambda context): Context injected by the lambda runtime with information about the
            lambda being executed.

    Returns:
        int: Return 0 on sucess and 1 on failure. When it fails the lambda execution will also
            provide exception information if uncaught.
    """

    event_object = MigrationEvent.model_validate(event)

    # File directly available in the lambda root directory
    config = Config("alembic.ini")

    if event_object.action is AlembicActions.UPGRADE:
        command.upgrade(config, event_object.revision)

    if event_object.action is AlembicActions.DOWNGRADE:
        command.downgrade(config, event_object.revision)

    return 0
