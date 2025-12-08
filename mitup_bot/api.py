import logging
import re
from asyncio import gather
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager, nullcontext
from typing import Protocol

from aws_embedded_metrics.unit import Unit
from sqlmodel import Session
from telegram import InlineQueryResultArticle, InputTextMessageContent, Message, Update
from telegram.error import BadRequest, Forbidden
from telegram.ext import CallbackContext, ExtBot

from mitup_bot import guards
from mitup_bot.custom_context import MitupContext
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


class ContextOrBotAdapter(Protocol):
    """
    Protocl defining the interface for the object necessary to interact with the Telegram API.

    This is used to support both MitupContext and ExtBot for flexibility. In case a bot is provided
    to the methods instead of a MitupContext, the bot is turned into a BotAdapter that does not emit any metrics
    but can be used to interact with the Telegram API.
    """

    @contextmanager
    def with_time_metric(self, prefix: str, handler_metrics: bool = False) -> Generator[None]: ...

    def emit_metric(
        self,
        name: str | MetricKey,
        value: float = 1.0,
        unit: Unit = Unit.COUNT,
        *,
        dimensions: dict[str, str] | None = None,
        include_handler_dimensions: bool = True,
        properties: dict[str, str | int | float | None] | None = None,
        include_update_properties: bool = True,
        emit_global: bool = False,
    ): ...

    async def flush_metrics(self): ...

    @property
    def bot(self) -> ExtBot: ...


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


async def send_message_to_user(
    *, context_or_bot: TMitupContext | ExtBot, user: User, view: MitupView | str
) -> Message | None:
    adapter = get_bot(context_or_bot=context_or_bot)

    if isinstance(view, str):
        message = view
        reply_markup = None
    else:
        message = view.description
        reply_markup = view.markup

    with with_time_metrics_context(context_or_bot=context_or_bot):
        try:
            return await adapter.bot.send_message(chat_id=user.tg_user_id, text=message, reply_markup=reply_markup)
        except Forbidden as e:
            logging.warning(f"User {user.tg_user_id} has blocked the bot.")
            raise InactiveUserInteraction(user.tg_user_id, private=True) from e
        except BadRequest as e:
            if "not found" in e.message:
                logging.warning(f"User {user.tg_user_id} is not in Telegram.")
                raise InactiveUserInteraction(user.tg_user_id, private=True) from e
            raise


async def send_messages_to_users(
    *,
    context_or_bot: TMitupContext | ExtBot,
    users: Sequence[User],
    views: Sequence[MitupView | str],
    on_success: Sequence[Callable[[User], None]] | None = None,
    on_error: Sequence[Callable[[User], None]] | None = None,
):
    """
    Sends messages to multiple users.

    If a user has blocked the bot or is no longer available, they will be marked as inactive
    in the database and no exception will be raised.
    """

    if len(users) != len(views):
        raise ValueError("The number of users and views must be the same")

    awaitables = [
        send_message_to_user(
            context_or_bot=context_or_bot,
            user=user,
            view=views[i],
        )
        for i, user in enumerate(users)
    ]

    results = await gather(*awaitables, return_exceptions=True)
    adapter = get_bot(context_or_bot=context_or_bot)

    for idx, (user, result) in enumerate(zip(users, results, strict=True)):
        if isinstance(result, InactiveUserInteraction):
            # Handle inactive user different for other errors
            # we do not want to error out but mark the user as inactive
            logging.info(f"Marking user {user.tg_user_id} as inactive")
            user.is_active = False
            adapter.emit_metric(MetricKey.INACTIVE_USER_SET, include_handler_dimensions=False)
            continue

        # Handle Callbacks
        if on_error and isinstance(result, Exception):
            logging.exception(f"Error sending message to user {user.id}: {result}")
            on_error[idx](user)
        elif on_success:
            on_success[idx](user)


@contextmanager
def handle_edit_errors(
    adapter: ContextOrBotAdapter, message: MessageModel | None = None, session: Session | None = None
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
            if session and message:
                logging.info(f"Message with ID {message.message_id} is invalid. Deleting it...")
                session.delete(message)
            adapter.emit_metric(MetricKey.MESSAGE_DELETED, include_handler_dimensions=False)
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
        with handle_edit_errors(adapter=context):
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
    context_or_bot: TMitupContext | ExtBot,
    meeting: Meetup,
    was_deleted: bool = False,
    has_finished: bool = False,
):
    """
    Updates a single meeting message with the current meeting view.

    Args:
        message: The message model to update.
        session: The database session.
        context_or_bot: The context or bot instance.
        meeting: The meeting object.
        was_deleted: If set to True, the meeting has been deleted and the messages will be updated to inform the user.
        has_finished: If set to True, the meeting has finished and the messages will be updated to inform the user.
    """
    # Support both MitupContext and ExtBot for flexibility
    adapter = get_bot(context_or_bot=context_or_bot)

    view = (
        meeting.inline_view
        if message.inline_message_id or message.chat_id != meeting.owner.tg_user_id
        else meeting.main_view
    )

    # Update the stored buttons to match the current view to ensure they are persisted
    # TODO: We might want to remove the persistency on this message. Not sure what was the
    # reason to have it to begin with
    message.buttons.keyboard = view.keyboard
    session.add(message)

    # Determine the text and markup based on meeting state
    if was_deleted:
        text = MeetingMessages.MEETING_HAS_BEEN_DELETED.get(lang=meeting.lang)
        reply_markup = None
    elif has_finished:
        text = view.with_context(MeetingMessages.MEETING_HAS_FINISHED.get(lang=meeting.lang)).description
        reply_markup = None
    else:
        text = view.description
        reply_markup = view.markup

    with (
        adapter.with_time_metric(prefix=TELEMGRAM_API_TIME_PREFIX),
        handle_edit_errors(adapter=adapter, message=message, session=session),
    ):
        await adapter.bot.edit_message_text(
            text=text,
            chat_id=message.chat_id,
            message_id=message.message_id,
            inline_message_id=message.inline_message_id,
            reply_markup=reply_markup,
        )


async def update_meeting_messages(
    *,
    session: Session,
    context_or_bot: TMitupContext | ExtBot,
    meeting: Meetup,
    current_message: MessageModel | None = None,
    skip_current=False,
    was_deleted=False,
    has_finished=False,
):
    """
    Updates meeting messages with the current meeting view.

    The `context_or_bot` parameter accept both a context, when used when running as part of the
    service and a bot instance, when used when running as part of the CLI for stand alone operations.

    If a bot is passed, internal metrics will not be emitted.

    Args:
        session: The database session.
        context_or_bot: The update context or bot instance.
        meeting: The Meetup object.
        current_message: The current message model, if any. If provided, it will be edited before any other message.
        skip_current: If set to True, the current message will be skipped. This is needed if the current message
                      is being updated in a different way.
        was_deleted: If set to True, the meeting has been deleted and the messages will be updated to inform the user.
        has_finished: If set to True, the meeting has finished and the messages will be updated to inform the user.
    """
    # First lets update the current message for a better user experience
    if current_message and not skip_current:
        await update_single_meeting_message(
            current_message, session, context_or_bot, meeting, was_deleted, has_finished
        )
    for message in meeting.messages:
        if message == current_message:
            continue
        await update_single_meeting_message(message, session, context_or_bot, meeting, was_deleted, has_finished)


@contextmanager
def with_time_metrics_context(*, context_or_bot: TMitupContext | ExtBot) -> Generator[None]:
    """
    Context manager that can be used either with a context or a bot. If a context is passed,
    a metric will be emitted with the time it took to send the API call to Telegram.
    """
    context = (
        context_or_bot.with_time_metric(prefix=TELEMGRAM_API_TIME_PREFIX)
        if isinstance(context_or_bot, CallbackContext)
        else nullcontext()
    )
    with context:
        yield


def get_bot(context_or_bot: TMitupContext | ExtBot) -> ContextOrBotAdapter:
    return context_or_bot if isinstance(context_or_bot, MitupContext) else BotAdapter(context_or_bot)


class BotAdapter:
    def __init__(self, bot: ExtBot):
        self.bot = bot

    @contextmanager
    def with_time_metric(self, prefix: str, handler_metrics: bool = False) -> Generator[None]:
        yield

    def emit_metric(
        self,
        name: str | MetricKey,
        value: float = 1.0,
        unit: Unit = Unit.COUNT,
        *,
        dimensions: dict[str, str] | None = None,
        include_handler_dimensions: bool = True,
        properties: dict[str, str | int | float | None] | None = None,
        include_update_properties: bool = True,
        emit_global: bool = False,
    ):
        pass

    async def flush_metrics(self):
        pass
