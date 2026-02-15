from sqlmodel import Session
from telegram import Update

from mitup_bot.db import with_async_session
from mitup_bot.guards import user_language
from mitup_bot.utils import ButtonMessages, InlineViewMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import ButtonConfig, MitupView

from ..registry import HandlersRegistry
from .enums import SEARCH_QUERY_PREFIX, InlineQueryId


@HandlersRegistry.register_callback_query(
    InlineQueryId.LOAD_CHAT_MEETINGS, callback_data=cb.LOAD_CHAT_MEETINGS, auto_answer=False
)
@with_async_session
async def load_chat_meetings(session: Session, update: Update, context: TMitupContext):
    """Handle the 'Load meetings' button click on the inline search message.

    Captures the ``chat_instance`` from the callback query and edits the message
    to show a ``switch_inline_query_current_chat`` button so the user can search
    for meetings attached to this chat.
    """
    lang = user_language(update, session)
    chat_instance = update.callback_query.chat_instance if update.callback_query else None

    view = MitupView(
        description=InlineViewMessages.READY_TO_SEARCH_MESSAGE.get(lang=lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.SEARCH_CHAT_MEETINGS.get(lang=lang, plain=True),
                    switch_inline_query_current_chat=f"{SEARCH_QUERY_PREFIX}{chat_instance}",
                )
            ],
        ],
    )

    await context.api.edit_message(update=update, view=view)
