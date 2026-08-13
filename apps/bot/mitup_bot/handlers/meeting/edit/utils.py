import structlog
from telegram import Message, MessageEntity
from telegram.ext import filters

from mitup_bot.custom_context import ContextId
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import User
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.utils.entities import FormattedText

log = structlog.get_logger(__name__)

# One name for every free-text meeting edit turned away by a character cap, whichever field it was,
# so a single filter answers "how often do the caps bite?" and `field` breaks it down.
EDIT_INPUT_REJECTED_EVENT = "Meeting edit input rejected"


def log_length_rejection(context: TMitupContext, user: User, *, field: str, length: int, limit: int):
    """Record a free-text meeting edit refused for exceeding its character cap.

    The refused text is the owner's own, so only its length travels. `length` is the count the user
    is shown, so the line and the message the user reads always name the same number.
    """
    log.info(
        EDIT_INPUT_REJECTED_EVENT,
        user_id=user.db_id,
        field=field,
        reason="too_long",
        input_length=length,
        limit=limit,
    )
    context.put_feature_metric(Feature.EDIT_MEETING, name=MetricKey.ERROR, properties={"reason": "too_long"})


def prepend_error(base: str | FormattedText, error: str | FormattedText) -> FormattedText:
    """Prepend an error paragraph (error text, blank line) to a prompt body, preserving entities.

    Used when a message-triggered edit fails validation: the prompt the user was answering is resent
    with the error on top, so the prompt's buttons stay reachable instead of the user being stranded
    with a bare error and no controls to continue.
    """
    prefix = error if isinstance(error, FormattedText) else FormattedText(error)
    return prefix.append("\n\n").append(base)


class DateTimeEntityFilter(filters.MessageFilter):
    """Accept messages that contain at least one `date_time` entity."""

    def filter(self, message: Message) -> bool:
        return any(e.type == MessageEntity.DATE_TIME for e in (message.entities or []))


def cleanup_states(context: TMitupContext):
    context.clean_user_data(
        [
            ContextId.EDIT_MEETING_TITLE,
            ContextId.EDIT_MEETING_DESCRIPTION,
            ContextId.EDIT_MEETING_LOCATION_NAME,
            ContextId.EDIT_MEETING_LOCATION_COORDINATES,
            ContextId.EDIT_MEETING_START,
            ContextId.EDIT_MEETING_END,
        ]
    )
