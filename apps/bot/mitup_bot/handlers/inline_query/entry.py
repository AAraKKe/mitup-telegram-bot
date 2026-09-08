import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.exceptions import (
    MeetingAccessError,
    MeetingGoneError,
    MeetingInactiveOwnerError,
    MeetingNotOwnedError,
)
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import Feature
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils import ButtonMessages, InlineQueryMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import MitupInlineView
from mitup_bot.views import meeting as meeting_views

from .enums import InlineQueryId, ShareRejectionReason
from .utils import inline_results_button, sort_meetings

log = structlog.get_logger(__name__)

SHARE_REJECTION_REASONS: dict[type[MeetingAccessError], ShareRejectionReason] = {
    MeetingGoneError: ShareRejectionReason.MEETING_NOT_FOUND,
    MeetingNotOwnedError: ShareRejectionReason.MEETING_NOT_SHAREABLE,
    MeetingInactiveOwnerError: ShareRejectionReason.MEETING_FINISHED,
}


def default_results_reason(user: User | None, *, suppressed: bool) -> str:
    """Name the fork that decided which of the three default-screen shapes was answered."""
    if suppressed:
        return "deletion_requested"
    return "registered_user" if user else "unregistered_user"


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
    suppressed = False
    if user is not None and user.status is UserStatus.DELETION_REQUESTED:
        # A user marked for deletion must not surface their meetings for sharing; treat them as
        # unregistered so this view offers nothing tied to the dying account.
        log.info("Inline results suppressed", user_id=user.db_id, reason="deletion_requested")
        user = None
        suppressed = True
    lang = user.lang if user else TranslationEngine.FALLBACK_LANG

    results: list[MitupInlineView] = [
        MitupInlineView(
            message=InlineQueryMessages.CHAT_MEETINGS_MESSAGE.rich(lang=lang),
            menu=[
                [
                    ButtonConfig(
                        text=ButtonMessages.LOAD_CHAT_MEETINGS.text(lang=lang),
                        callback_data=cb.LOAD_CHAT_MEETINGS,
                    )
                ],
            ],
            id="meetings_in_this_chat",
            title=InlineQueryMessages.CHAT_MEETINGS_TITLE.text(lang=lang),
            inline_description=InlineQueryMessages.CHAT_MEETINGS_DESCRIPTION.text(lang=lang),
        ),
    ]

    own_active_meetings = [m for m in user.meetups if m.active] if user else []
    if own_active_meetings:
        results.extend(meeting_views.inline_view(meeting) for meeting in sort_meetings(own_active_meetings))

    log.info(
        "Inline default results answered",
        user_id=user.db_id if user else None,
        registered=user is not None,
        lang=lang,
        own_active_meetings=len(own_active_meetings),
        results=len(results),
        reason=default_results_reason(user, suppressed=suppressed),
    )
    await context.api.answer_inline_query(
        update=update, results=results, button=inline_results_button(user), cache_time=0
    )


async def answer_share_rejection(
    update: Update,
    context: TMitupContext,
    user: User | None,
    reason: ShareRejectionReason,
    meeting_id: int | None = None,
):
    """Answer a share query that resolves no card, and record which rejection it was.

    All four rejections get the same empty panel under the same button, so nothing on the wire
    separates an id nobody has from one somebody else keeps private.
    """
    log.info(
        "Meeting share rejected",
        user_id=user.db_id if user else None,
        meeting_id=meeting_id,
        reason=reason.value,
    )
    await context.api.answer_inline_query(update=update, results=[], button=inline_results_button(user), cache_time=0)


@HandlersRegistry.register_inline_handler(InlineQueryId.SHARE_MEETING, pattern=r"\d+")
@with_session
async def share_meeting(session: AsyncSession, update: Update, context: TMitupContext):
    """Answer an inline query that shares one meeting, identified by its id in the query.

    This handler can be triggered by any Telegram user, whether or not they have a mitup profile:
    a public meeting resolves for anyone, a non-public one only for its owner, and everything else
    is answered with the empty panel. The shared card renders in the meeting's own language, so the
    sharer's language only drives that card.
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
        log.info("Inline results suppressed", user_id=user.db_id, reason="deletion_requested")
        user = None

    meeting_id = guards.shareable_meeting_id(update)
    if meeting_id is None:
        await answer_share_rejection(update, context, user, ShareRejectionReason.NON_NUMERIC_INLINE_QUERY)
        return

    # Telegram sends a query on every keystroke, so a backspace routinely addresses somebody else's
    # meeting: that is a normal interaction, not a rejection worth the error handler's screen.
    try:
        meeting = await guards.meeting(
            session, user, meeting_id, "share meeting", context, access=guards.MeetingAccess.OWNER_OR_PUBLIC
        )
    except MeetingAccessError as rejection:
        await answer_share_rejection(
            update, context, user, SHARE_REJECTION_REASONS[type(rejection)], meeting_id=meeting_id
        )
        return

    log.info(
        "Meeting shared",
        user_id=user.db_id if user else None,
        meeting_id=meeting.db_id,
        public=meeting.public,
        is_owner=user is not None and meeting.is_owned_by(user),
    )
    context.put_feature_metric(Feature.SHARE_MEETING)
    await context.api.answer_inline_query(update=update, results=[meeting_views.inline_view(meeting)], cache_time=0)
