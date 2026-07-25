import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot.db import with_session
from mitup_bot.guards import chosen_inline_result
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import Meetup, Message
from mitup_bot.views import meeting as meeting_views

from ..registry import HandlersRegistry
from .enums import InlineQueryId

log = structlog.get_logger(__name__)


@HandlersRegistry.register_chosen_inline_result(InlineQueryId.SHARED_MEETING, pattern=r"^\d+$")
@with_session
async def chosen_inline_result_handler(session: AsyncSession, update: Update, context: TMitupContext):
    """Track the meeting card a user just shared, binding its inline message to that meeting.

    The binding is what authorizes later interactions with the card (see
    `guards.meeting_interaction_allowed`), so it has to exist before anyone can tap a button on it —
    recording it on first interaction instead would leave every freshly shared card unclaimed, and an
    unclaimed card is one an attacker can point at any meeting id through forged callback data.

    `result_id` is the id the bot put on the answered inline result, and Telegram resolves it against
    the result set it answered that user's query with in order to build the message it sends. It
    therefore names a meeting the bot already offered this user to share; the pattern narrows this
    handler to the meeting cards, whose result id is the meeting id (the other inline results the bot
    answers with carry non-numeric ids).

    Only meeting cards carry an inline keyboard, and Telegram allocates an `inline_message_id` only
    for results that have one.
    """
    result = chosen_inline_result(update)
    inline_message_id = result.inline_message_id
    if inline_message_id is None:
        return

    meeting_id = int(result.result_id)
    if (meeting := await Meetup.by_id(session, meeting_id)) is None:
        log.warning("Shared a meeting that no longer exists", meetup_id=meeting_id)
        return

    if await Message.meetup_id_for_inline_message(session, inline_message_id) is not None:
        # Already tracked: either a redelivery (a webhook call that missed its 2xx) — which carries
        # the same sharer, so the per-user update lock ordered it behind the first run — or a
        # stakeholder's first tap on the card raced this handler and recorded it via
        # `Message.from_update`.
        return

    session.add(Message.for_shared_card(inline_message_id, meeting, meeting_views.inline_view(meeting).keyboard))
