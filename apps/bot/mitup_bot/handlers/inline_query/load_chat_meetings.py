import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot.db import with_session
from mitup_bot.guards import callback_query, user_language
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.utils import InlineQueryMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import MitupView

from ..registry import HandlersRegistry
from .enums import InlineQueryId
from .utils import search_chat_meetings_button

log = structlog.get_logger(__name__)


@HandlersRegistry.register_callback_query(
    InlineQueryId.LOAD_CHAT_MEETINGS, callback_data=cb.LOAD_CHAT_MEETINGS, auto_answer=False
)
@with_session
async def load_chat_meetings(session: AsyncSession, update: Update, context: TMitupContext):
    """Handle the 'Load meetings' button click on the inline search message.

    Captures the `chat_instance` from the callback query and edits the message
    to show a `switch_inline_query_current_chat` button so the user can search
    for meetings attached to this chat.
    """
    lang = await user_language(update, session)
    chat_instance = callback_query(update).chat_instance

    # `chat_instance` is the only key that joins this tap to the `search:<chat_instance>` query it
    # arms, which arrives as a separate inline update from a different handler.
    log.info("Chat meeting search armed", chat_instance=chat_instance, lang=lang)

    view = MitupView(
        description=InlineQueryMessages.READY_TO_SEARCH_MESSAGE.get(lang=lang),
        keyboard=[[search_chat_meetings_button(lang=lang, chat_instance=chat_instance)]],
    )

    await context.api.edit_message(update=update, view=view)
