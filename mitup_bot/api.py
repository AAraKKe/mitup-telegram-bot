from telegram import InlineQueryResultArticle, InputTextMessageContent, Message, Update
from telegram.error import TelegramError
from telegram.ext import CallbackContext

from mitup_bot import guards
from mitup_bot.exceptions import AnswerInlineQueryError
from mitup_bot.views import MitupInlineView, MitupView


async def send_message(context: CallbackContext, update: Update, view: MitupView | str) -> Message | None:
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


async def edit_message(context: CallbackContext, update: Update, view: MitupView | str) -> Message | bool:
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


async def answer_inline_query(context: CallbackContext, update: Update, results: list[MitupInlineView]):
    query = guards.valid_inline_query(update)
    inline_results = [
        InlineQueryResultArticle(
            id=view.id,
            title=view.title,
            input_message_content=InputTextMessageContent(message_text=view.description),
            reply_markup=view.markup,
        )
        for view in results
    ]
    if await context.bot.answer_inline_query(query.id, results=inline_results):
        return
    raise AnswerInlineQueryError(query.query)
