import logging

from rich.console import Console
from sqlmodel import Session, select
from telegram.error import BadRequest

from mitup_bot import db
from mitup_bot.config import Env
from mitup_bot.exceptions import InactiveUserInteraction
from mitup_bot.models import User
from mitup_bot.monitoring import MetricKey
from mitup_bot.utils.mitup_types import TMitupContext

console = Console()

# Errors that can be suppressed and ignored
SUPPRESSED_EXCEPTIONS: dict[type, set[str]] = {
    BadRequest: {
        # This happens when a message is deleted
        "Message to edit not found",
    }
}


@db.with_async_session
async def handle_inactive_user(session: Session, context: TMitupContext, user_id: int):
    if user := session.exec(select(User).where(User.id == user_id)).first():
        user.is_active = False
        context.emit_metric(MetricKey.INACTIVE_USER_SET, 1, include_handler_dimensions=False)


def should_ignore_error(error: Exception) -> bool:
    if type(error) not in SUPPRESSED_EXCEPTIONS:
        return False

    return str(error) in SUPPRESSED_EXCEPTIONS[type(error)]


async def handler(context: TMitupContext, error: Exception, env: Env):
    # This is the error handler that will receive every exception that is raised

    if should_ignore_error(error):
        return

    if isinstance(error, InactiveUserInteraction) and error.private:
        await handle_inactive_user(context, error.user_id)  # ty: ignore[missing-argument]  # https://github.com/astral-sh/ty/issues/2759
        return

    # Emit an error metric for the current update both including the error type and a general
    # error metric to aggregate all error types
    error_class = error.__class__.__name__
    context.emit_metric(MetricKey.FAULT.with_prefix(error_class), 1)
    context.emit_metric(MetricKey.FAULT, 1, emit_global=True)

    context.metrics_engine.add_stack_trace()

    # If we are in development mode, lets print the exception when it happens
    if env is Env.DEV:  # pragma: no cover
        # Print exception with rich logger
        logging.exception("An error occurred while handling the update.")
