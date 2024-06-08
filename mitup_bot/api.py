from telegram import Message, Update

from telegram.error import TelegramError

from mitup_bot import guards
from mitup_bot.custom_context import MitupContext
from mitup_bot.views import MitupView


async def send_message(context: MitupContext, update: Update, view: MitupView | str) -> Message | None:
    chat_id = guards.chat(update).id

    if isinstance(view, str):
        message = view
        reply_markup = None
    else:
        message = view.description
        reply_markup = view.markup

    try:
        return await context.bot.send_message(chat_id=chat_id, text=message, reply_markup=reply_markup)
    except TelegramError:
        return None


async def edit_message(context: MitupContext, update: Update, view: MitupView | str) -> Message | bool:
    tg_message = guards.message(update)

    if isinstance(view, str):
        message = view
        reply_markup = None
    else:
        message = view.description
        reply_markup = view.markup

    try:
        return await context.bot.edit_message_text(
            message, tg_message.chat.id, message_id=tg_message.id, reply_markup=reply_markup
        )
    except TelegramError:
        return False
