from telegram import Update
from telegram.ext import ContextTypes

from mitup_bot import guards
from mitup_bot.views import MitupView


async def send_message(context: ContextTypes.DEFAULT_TYPE, update: Update, view: MitupView | str):
    chat_id = guards.chat(update).id

    if isinstance(view, str):
        message = view
        reply_markup = None
    else:
        message = view.description
        reply_markup = view.markup

    await context.bot.send_message(chat_id=chat_id, text=message, reply_markup=reply_markup, parse_mode="MarkdownV2")


async def edit_message(context: ContextTypes.DEFAULT_TYPE, update: Update, view: MitupView | str):
    tg_message = guards.message(update)

    if isinstance(view, str):
        message = view
        reply_markup = None
    else:
        message = view.description
        reply_markup = view.markup

    await context.bot.edit_message_text(
        message,
        tg_message.chat.id,
        message_id=tg_message.id,
        reply_markup=reply_markup,
        parse_mode="MarkdownV2",
    )
