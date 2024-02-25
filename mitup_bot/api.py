from telegram import Update
from telegram.ext import ContextTypes

from mitup_bot.views import MitupView


async def send_message(context: ContextTypes.DEFAULT_TYPE, update: Update, view: MitupView | str):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    if isinstance(view, str):
        message = view
        reply_markup = None
    else:
        message = view.description
        reply_markup = view.markup

    await context.bot.send_message(
        chat_id=update.effective_chat.id, text=message, reply_markup=reply_markup, parse_mode="MarkdownV2"
    )


async def edit_message(context: ContextTypes.DEFAULT_TYPE, update: Update, view: MitupView | str):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    if update.effective_message is None:
        raise RuntimeError("Effective message not set")

    message_id = update.effective_message.message_id

    if isinstance(view, str):
        message = view
        reply_markup = None
    else:
        message = view.description
        reply_markup = view.markup

    await context.bot.edit_message_text(
        message,
        update.effective_chat.id,
        message_id=message_id,
        reply_markup=reply_markup,
        parse_mode="MarkdownV2",
    )
