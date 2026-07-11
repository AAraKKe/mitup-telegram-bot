from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot.db import with_session
from mitup_bot.guards import current_user, shareable_meeting_id
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup, User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import Feature
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils import ButtonMessages, InlineQueryMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import InlineResultsButton, MitupInlineView

from .enums import InlineQueryId
from .utils import meeting_unavailable_view, sort_meetings


@HandlersRegistry.register_inline_handler(InlineQueryId.INLINE_VIEW, pattern=r"^\s*$")
@with_session
async def inline_view(session: AsyncSession, update: Update, context: TMitupContext):
    """Show the default inline view when a user invokes the bot in any chat without a specific query.

    This handler can be triggered by any Telegram user, whether or not they have a mitup profile.
    If the user is registered, their preferred language is used; otherwise the fallback language is applied.
    """
    user = await User.by_tg_user_id(session, update.effective_user.id) if update.effective_user else None
    if user is not None and user.status is UserStatus.DELETION_REQUESTED:
        # A user marked for deletion must not surface their meetings for sharing; treat them as
        # unregistered so this view offers nothing tied to the dying account.
        user = None
    lang = user.lang if user else TranslationEngine.FALLBACK_LANG

    button_text = InlineQueryMessages.CREATE_MEETING_BUTTON if user else InlineQueryMessages.EXPLORE_BUTTON
    button = InlineResultsButton(
        text=button_text.get_text(lang=lang),
        start_parameter="inline",
    )

    results: list[MitupInlineView] = [
        MitupInlineView(
            description=InlineQueryMessages.CHAT_MEETINGS_MESSAGE.get(lang=lang),
            keyboard=[
                [
                    ButtonConfig(
                        text=ButtonMessages.LOAD_CHAT_MEETINGS.get(lang=lang),
                        callback_data=cb.LOAD_CHAT_MEETINGS,
                    )
                ],
            ],
            id="meetings_in_this_chat",
            title=InlineQueryMessages.CHAT_MEETINGS_TITLE.get(lang=lang),
            inline_description=InlineQueryMessages.CHAT_MEETINGS_DESCRIPTION.get(lang=lang),
        ),
    ]

    if user and (active_meetings := [m for m in user.meetups if m.active]):
        results.extend(meeting.inline_view() for meeting in sort_meetings(active_meetings))

    await context.api.answer_inline_query(update=update, results=results, button=button, cache_time=0)


@HandlersRegistry.register_inline_handler(InlineQueryId.SHARE_MEETING, pattern=r"\d+")
@with_session
async def share_meeting(session: AsyncSession, update: Update, context: TMitupContext):
    """
    Handle an inline query to share a meeting.
    TODO: Right now we are only allowing existing users to share a meeting. We should allow non-existing users to
    share a meeting as well when public meeting feature is implemented.
    """
    # Reads `user.lang` and passes the user to `meeting.is_owned_by`, which compares `user.db_id`
    # against the meeting's own owner — neither touches the user's meetups/joined_links collections,
    # so skip loading them.
    user = await current_user(update, session, load_collections=False)
    meeting_id = await shareable_meeting_id(update, context)
    if meeting_id is None:
        return

    meeting = await Meetup.by_id(session, meeting_id)

    if meeting and meeting.active and (meeting.public or meeting.is_owned_by(user)):
        results = [meeting.inline_view()]
        context.put_feature_metric(Feature.SHARE_MEETING)
    else:
        results = [meeting_unavailable_view(user.lang)]

    await context.api.answer_inline_query(update=update, results=results, cache_time=0)
