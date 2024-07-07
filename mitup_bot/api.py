import logging

from sqlmodel import Session
from telegram import InlineQueryResultArticle, InputTextMessageContent, Message, Update
from telegram.error import BadRequest

from mitup_bot import guards
from mitup_bot.exceptions import AnswerInlineQueryError
from mitup_bot.models import Meetup
from mitup_bot.models import Message as MessageModel
from mitup_bot.monitoring import MetricKey
from mitup_bot.utils.types import TMitupContext
from mitup_bot.views import MitupInlineView, MitupView


async def send_message(context: TMitupContext, update: Update, view: MitupView | str) -> Message | None:
    chat_id = guards.chat(update).id

    if isinstance(view, str):
        message = view
        reply_markup = None
    else:
        message = view.description
        reply_markup = view.markup

    return await context.bot.send_message(chat_id=chat_id, text=message, reply_markup=reply_markup)


async def edit_message(context: TMitupContext, update: Update, view: MitupView | str) -> Message | bool:
    tg_message = guards.message(update)

    if isinstance(view, str):
        message = view
        reply_markup = None
    else:
        message = view.description
        reply_markup = view.markup

    return await context.bot.edit_message_text(
        message, tg_message.chat.id, message_id=tg_message.id, reply_markup=reply_markup
    )


async def answer_inline_query(context: TMitupContext, update: Update, results: list[MitupInlineView]):
    query = guards.valid_inline_query(update)
    inline_results = [
        InlineQueryResultArticle(
            id=view.id,
            title=view.title,
            description=view.inline_description,
            input_message_content=InputTextMessageContent(message_text=view.description),
            reply_markup=view.markup,
        )
        for view in results
    ]
    if await context.bot.answer_inline_query(query.id, results=inline_results):
        return
    raise AnswerInlineQueryError(query.query)


async def update_single_meeting_message(
    message: MessageModel, session: Session, context: TMitupContext, meeting: Meetup
):
    view = (
        meeting.inline_view
        if message.inline_message_id or message.chat_id != meeting.owner.tg_user_id
        else meeting.main_view
    )
    try:
        await context.bot.edit_message_text(
            text=view.description,
            chat_id=message.chat_id,
            message_id=message.message_id,
            inline_message_id=message.inline_message_id,
            reply_markup=MitupView.keyboard_to_markup(message.buttons.keyboard),
        )
    except BadRequest as e:
        # Sometimes the message does not need to be updated but we don't know that in advance
        # ignore the error when it happens
        if "Message is not modified" in e.message:
            return
        # If we get an error saying that the message is not found, we should delete the message
        if "Message_id_invalid" in e.message:
            logging.info(f"Message with ID {message.message_id} is invalid. Deleting it...")
            session.delete(message)
            context.put_custom_metric(MetricKey.MESSAGE_DELETED)
            return
        raise


async def update_meeting_messages(
    session: Session, context: TMitupContext, meeting: Meetup, current_message: MessageModel | None = None
):
    # First lets update the current message for a better user experience
    if current_message:
        await update_single_meeting_message(current_message, session, context, meeting)
    for message in meeting.messages:
        if message == current_message:
            continue

        # If the message is an inline message, we should update the inline view
        # otherwise the message is from a chat. When the chat id is the same as the owher telegram
        # id, it means we can show everything.
        view = (
            meeting.inline_view
            if message.inline_message_id or message.chat_id != meeting.owner.tg_user_id
            else meeting.main_view
        )
        try:
            await context.bot.edit_message_text(
                text=view.description,
                chat_id=message.chat_id,
                message_id=message.message_id,
                inline_message_id=message.inline_message_id,
                reply_markup=MitupView.keyboard_to_markup(message.buttons.keyboard),
            )
        except BadRequest as e:
            # Sometimes the message does not need to be updated but we don't know that in advance
            # ignore the error when it happens
            if "Message is not modified" in e.message:
                continue
            # If we get an error saying that the message is not found, we should delete the message
            if "Message_id_invalid" in e.message:
                logging.info(f"Message with ID {message.message_id} is invalid. Deleting it...")
                session.delete(message)
                context.put_custom_metric(MetricKey.MESSAGE_DELETED)
                continue
            raise
