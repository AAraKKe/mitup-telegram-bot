from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import Feature
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils import ButtonMessages, InlineQueryMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import InlineResultsButton, MitupInlineView
from mitup_bot.views import meeting as meeting_views

from .enums import InlineQueryId
from .utils import sort_meetings


@HandlersRegistry.register_inline_handler(InlineQueryId.INLINE_VIEW, pattern=r"^\s*$")
@with_session
async def inline_view(session: AsyncSession, update: Update, context: TMitupContext):
    """Show the default inline view when a user invokes the bot in any chat without a specific query.

    This handler can be triggered by any Telegram user, whether or not they have a mitup profile.
    If the user is registered, their preferred language is used; otherwise the fallback language is applied.
    """
    # load_participants: this view renders full meeting cards (owner and every participant name)
    # straight off `user.meetups`, which the shallow default load does not reach.
    user = (
        await User.by_tg_user_id(session, update.effective_user.id, load_participants=True)
        if update.effective_user
        else None
    )
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
                        text=ButtonMessages.LOAD_CHAT_MEETINGS.get_text(lang=lang),
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
        results.extend(meeting_views.inline_view(meeting) for meeting in sort_meetings(active_meetings))

    await context.api.answer_inline_query(update=update, results=results, button=button, cache_time=0)


@HandlersRegistry.register_inline_handler(InlineQueryId.SHARE_MEETING, pattern=r"\d+")
@with_session
async def share_meeting(session: AsyncSession, update: Update, context: TMitupContext):
    """Answer an inline query that shares one meeting, identified by its id in the query.

    This handler can be triggered by any Telegram user, whether or not they have a mitup profile:
    a public meeting resolves for anyone, a non-public one only for its owner, and everything else
    is answered with the unavailable placeholder. The shared card renders in the meeting's own
    language, so the sharer's language only drives that card.
    """
    # load_collections=False: nothing here traverses the sharer's own meetings — ownership is decided
    # on the meeting's owner leaf.
    user = (
        await User.by_tg_user_id(session, update.effective_user.id, load_collections=False)
        if update.effective_user
        else None
    )
    if user is not None and user.status is UserStatus.DELETION_REQUESTED:
        # A user marked for deletion must not share meetings tied to the dying account; treating them
        # as unregistered still lets a public meeting through on its own flag.
        user = None

    meeting_id = await guards.shareable_meeting_id(update, context)
    if meeting_id is None:
        return

    # A meeting the caller may not put on a card — gone, finished, or somebody else's and not
    # public — is rejected by the guard, and the error handler answers the query with the
    # unavailable card that every one of those cases shows.
    meeting = await guards.meeting(
        session, user, meeting_id, "share meeting", context, access=guards.MeetingAccess.OWNER_OR_PUBLIC
    )

    context.put_feature_metric(Feature.SHARE_MEETING)
    await context.api.answer_inline_query(update=update, results=[meeting_views.inline_view(meeting)], cache_time=0)
