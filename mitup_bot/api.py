from telegram import Update
from telegram.ext import ContextTypes

from mitup_bot.views import MitupView


async def send_message(context: ContextTypes.DEFAULT_TYPE, update: Update, message: str):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    await context.bot.send_message(chat_id=update.effective_chat.id, text=message, parse_mode="MarkdownV2")


async def edit_message(context: ContextTypes.DEFAULT_TYPE, update: Update, message: str):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    if update.effective_message is None:
        raise RuntimeError("Effective message not set")

    message_id = update.effective_message.message_id

    await context.bot.edit_message_text(
        message,
        update.effective_chat.id,
        message_id=message_id,
        parse_mode="MarkdownV2",
    )


async def send_message_view(
    context: ContextTypes.DEFAULT_TYPE,
    update: Update,
    view: MitupView,
):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    await context.bot.send_message(
        chat_id=update.effective_chat.id, text=view.description, reply_markup=view.markup, parse_mode="MarkdownV2"
    )


async def edit_message_view(
    context: ContextTypes.DEFAULT_TYPE,
    update: Update,
    view: MitupView,
):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    if update.effective_message is None:
        raise RuntimeError("Effective message not set")

    message_id = update.effective_message.message_id

    await context.bot.edit_message_text(
        view.description,
        update.effective_chat.id,
        message_id=message_id,
        reply_markup=view.markup,
        parse_mode="MarkdownV2",
    )
