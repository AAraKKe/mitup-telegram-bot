from sqlmodel import Session
from telegram import Update

from mitup_bot.db import with_async_session
from mitup_bot.guards import current_user, valid_inline_query
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.models import Meetup, User
from mitup_bot.monitoring import Feature
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils import ButtonMessages, InlineQueryMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import ButtonConfig, InlineResultsButton, MitupInlineView

from .enums import InlineQueryId
from .utils import sort_meetings


@HandlersRegistry.register_inline_handler(InlineQueryId.INLINE_VIEW, pattern=r"^\s*$")
@with_async_session
async def inline_view(session: Session, update: Update, context: TMitupContext):
    """Show the default inline view when a user invokes the bot in any chat without a specific query.

    This handler can be triggered by any Telegram user, whether or not they have a mitup profile.
    If the user is registered, their preferred language is used; otherwise the fallback language is applied.
    """
    user = User.by_tg_user_id(session, update.effective_user.id) if update.effective_user else None
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

    if meeting and meeting.active and (meeting.public or meeting.is_owned_by(user)):
        view = meeting.inline_view()
        await context.api.answer_inline_query(update=update, results=[view])
        context.put_feature_metric(Feature.SHARE_MEETING)
