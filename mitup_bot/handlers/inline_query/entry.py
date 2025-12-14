from sqlmodel import Session
from telegram import Update

from mitup_bot.db import with_async_session
from mitup_bot.guards import current_user, valid_inline_query
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.models import Meetup
from mitup_bot.monitoring import Feature
from mitup_bot.utils.mitup_types import TMitupContext

from .enums import InlineQueryId


@HandlersRegistry.register_inline_handler(InlineQueryId.SHARE_MEETING, pattern=r"\d+")
@with_async_session
async def share_meeting(session: Session, update: Update, context: TMitupContext):
    """
    Handle an inline query to share a meeting.
    TODO: Right now we are only allowing existing users to share a meeting. We should allow non-existing users to
    share a meeting as well when public meeting feature is implemented.
    """
    user = current_user(update, session)
    query = valid_inline_query(update).query
    meeting_id = int(query)
    # Pass redirect false since an inline query does not have a message we can edit.
    meeting = Meetup.by_id(session, meeting_id)

    if meeting and (meeting.public or meeting.is_owned_by(user)):
        view = meeting.inline_view
        await context.api.answer_inline_query(update=update, results=[view])
        context.put_feature_metric(Feature.SHARE_MEETING)
