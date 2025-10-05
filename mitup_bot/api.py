import logging
import re
from asyncio import gather
from collections.abc import Sequence
from contextlib import contextmanager

from sqlmodel import Session
from telegram import InlineQueryResultArticle, InputTextMessageContent, Message, Update
from telegram.error import BadRequest, Forbidden

from mitup_bot import guards
from mitup_bot.exceptions import (
    AnswerInlineQueryError,
    CallbackQueryTextTooLong,
    InactiveUserInteraction,
    NoMessageAvailable,
)
from mitup_bot.models import Meetup, User
from mitup_bot.models import Message as MessageModel
from mitup_bot.monitoring import MetricKey
from mitup_bot.utils import MeetingMessages
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import MitupInlineView, MitupView

TELEMGRAM_API_TIME_PREFIX = "TelegramApi"
MESSAGE_NOT_FOUND_ERROR_PATTERNS = [
    re.compile(r"Message_id_invalid"),
    re.compile(r"Message to edit not found"),
]
EDIT_MESSAGE_ERRORS_TO_IGNORE_PATTERNS = [re.compile(r"Message is not modified")]


async def send_message(*, context: TMitupContext, update: Update, view: MitupView | str) -> Message | None:
    chat_id = guards.chat(update).id

    if isinstance(view, str):
        message = view
        reply_markup = None
    else:
        message = view.description
        reply_markup = view.markup

    with context.with_time_metric(prefix=TELEMGRAM_API_TIME_PREFIX) as _:
        return await context.bot.send_message(chat_id=chat_id, text=message, reply_markup=reply_markup)


async def send_message_to_user(*, context: TMitupContext, user: User, view: MitupView | str) -> Message | None:
    if isinstance(view, str):
        message = view
        reply_markup = None
    else:
        message = view.description
        reply_markup = view.markup

    with context.with_time_metric(prefix=TELEMGRAM_API_TIME_PREFIX):
        try:
            return await context.bot.send_message(chat_id=user.tg_user_id, text=message, reply_markup=reply_markup)
        except Forbidden as e:
            logging.warning(f"User {user.tg_user_id} has blocked the bot.")
            context.emit_metric(MetricKey.INACTIVE_USER_SET, include_handler_dimensions=False)
            raise InactiveUserInteraction(user.tg_user_id, private=True) from e
        except BadRequest as e:
            if "not found" in e.message:
                logging.warning(f"User {user.tg_user_id} is not in Telegram.")
                raise InactiveUserInteraction(user.tg_user_id, private=True) from e
            raise


async def send_messages_to_users(context: TMitupContext, users: Sequence[User], views: Sequence[MitupView | str]):
    """
    Sends messages to multiple users.

    If a user has blocked the bot or is no longer available, they will be marked as inactive
    in the database and no exception will be raised.
    """

    if len(users) != len(views):
        raise ValueError("The number of users and views must be the same")

    awaitables = [
        send_message_to_user(
            context=context,
            user=user,
            view=views[i],
        )
        for i, user in enumerate(users)
    ]

    results = await gather(*awaitables, return_exceptions=True)

    # Mark users as inactive if they have blocked the bot
    for user, result in zip(users, results, strict=True):
        if isinstance(result, InactiveUserInteraction):
            logging.info(f"Marking user {user.tg_user_id} as inactive")
            user.is_active = False
            context.emit_metric(MetricKey.INACTIVE_USER_SET, include_handler_dimensions=False)


@contextmanager
def handle_edit_errors(
    message: MessageModel | None = None, session: Session | None = None, context: TMitupContext | None = None
):
    try:
        yield
    except BadRequest as e:
        # Sometimes the message does not need to be updated but we don't know that in advance
        # ignore the error when it happens
        if any(pattern.findall(e.message) for pattern in EDIT_MESSAGE_ERRORS_TO_IGNORE_PATTERNS):
            return

        # If we get an error saying that the message is not found, we should delete the message
        if any(pattern.findall(e.message) for pattern in MESSAGE_NOT_FOUND_ERROR_PATTERNS):
            if session and message and context:
                logging.info(f"Message with ID {message.message_id} is invalid. Deleting it...")
                session.delete(message)
                context.emit_metric(MetricKey.MESSAGE_DELETED, include_handler_dimensions=False)
            return
        raise


async def edit_message(*, context: TMitupContext, update: Update, view: MitupView | str) -> Message | bool:
    if isinstance(view, str):
        message = view
        reply_markup = None
    else:
        message = view.description
        reply_markup = view.markup

    chat_id = None
    message_id = None
    inline_message_id = None

    if update.effective_message:
        chat_id = update.effective_message.chat.id
        message_id = update.effective_message.id
    elif update.callback_query and update.callback_query.inline_message_id:
        inline_message_id = update.callback_query.inline_message_id
    else:
        raise NoMessageAvailable("Cannot edit message, neither message_id nor inline_message_id is available")

    with context.with_time_metric(prefix=TELEMGRAM_API_TIME_PREFIX):
        with handle_edit_errors():
            return await context.bot.edit_message_text(
                text=message,
                chat_id=chat_id,
                message_id=message_id,
                inline_message_id=inline_message_id,
                reply_markup=reply_markup,
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


async def answer_callback_query(*, context: TMitupContext, update: Update, text: str, show_alert: bool):
    if len(text) > 200:
        CallbackQueryTextTooLong(text)
    query = guards.valid_callback_query(update)
    await context.bot.answer_callback_query(query.id, text=text, show_alert=show_alert)


async def update_single_meeting_message(
    message: MessageModel,
    session: Session,
    context: TMitupContext,
    meeting: Meetup,
    was_deleted: bool,
):
    view = (
        meeting.inline_view
        if message.inline_message_id or message.chat_id != meeting.owner.tg_user_id
        else meeting.main_view
    )
    text = MeetingMessages.MEETING_HAS_BEEN_DELETED.get(lang=meeting.lang) if was_deleted else view.description
    reply_markup = None if was_deleted else MitupView.keyboard_to_markup(message.buttons.keyboard)

    with context.with_time_metric(prefix=TELEMGRAM_API_TIME_PREFIX):
        with handle_edit_errors(message, session, context):
            await context.bot.edit_message_text(
                text=text,
                chat_id=message.chat_id,
                message_id=message.message_id,
                inline_message_id=message.inline_message_id,
                reply_markup=reply_markup,
            )


async def update_meeting_messages(
    *,
    session: Session,
    context: TMitupContext,
    meeting: Meetup,
    current_message: MessageModel | None = None,
    skip_current=False,
    was_deleted=False,
):
    """
    Updates meeting messages with the current meeting view.

    Args:
        session: The database session.
        context: The update context.
        meeting: The Meetup object.
        current_message: The current message model, if any. If provided, it will be edited before any other message.
        skip_current: If set to True, the current message will be skipped. This is needed if the current message
                      is being updated in a different way.
        was_deleted: If set to True, the meeting has been deleted and the messages will be updated to inform the user.
    """
    # First lets update the current message for a better user experience
    if current_message and not skip_current:
        await update_single_meeting_message(current_message, session, context, meeting, was_deleted)
    for message in meeting.messages:
        if message == current_message:
            continue
        await update_single_meeting_message(message, session, context, meeting, was_deleted)
