import logging
import re
from asyncio import gather
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING, Protocol, cast

from aws_embedded_metrics.unit import Unit
from sqlmodel import Session
from telegram import (
    InlineQueryResultArticle,
    InlineQueryResultsButton,
    InputTextMessageContent,
    Message,
    Update,
)
from telegram.error import BadRequest, Forbidden
from telegram.ext import CallbackContext, ExtBot

from mitup_bot.exceptions import (
    AnswerInlineQueryError,
    CallbackQueryTextTooLong,
    InactiveUserInteraction,
    NoMessageAvailable,
)
from mitup_bot.models import Meetup, User
from mitup_bot.models import Message as MessageModel
from mitup_bot.models.joined_users import JoinedUsers
from mitup_bot.monitoring import MetricKey
from mitup_bot.protocols import ContextOrBotAdapter
from mitup_bot.utils import MeetingMessages
from mitup_bot.utils.entities import FormattedText
from mitup_bot.views import InlineResultsButton, MitupInlineView, MitupView

TELEMGRAM_API_TIME_PREFIX = "TelegramApi"
MESSAGE_NOT_FOUND_ERROR_PATTERNS = [
    re.compile(r"Message_id_invalid"),
    re.compile(r"Message to edit not found"),
]
EDIT_MESSAGE_ERRORS_TO_IGNORE_PATTERNS = [re.compile(r"Message is not modified")]


if TYPE_CHECKING:
    from mitup_bot.custom_context import MitupContext


class BotAdapter:
    """
    This class is used to be able to use the TelegramApi classes with a bot instead of a MitupContext.

    TODO: For now this class does not emit any metrics and just provides access to the bot. In thef uture
    we might want to create feature parity with context so this adapter emits the same metrics that the context does.
    """

    def __init__(self, bot: ExtBot):
        self._bot = bot

    @property
    def bot(self) -> ExtBot:
        return self._bot

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


def build_api(context_or_bot: MitupContext | ExtBot) -> TelegramApiWrapper:
    from mitup_bot.custom_context import MitupContext

    adapter = context_or_bot if isinstance(context_or_bot, MitupContext) else BotAdapter(context_or_bot)
    api = TelegramApi()
    # ty seems to be having issues finding that the adapter is of conforming type for now
    # https://github.com/astral-sh/ty/issues/2692
    api.adapter = cast(ContextOrBotAdapter, adapter)
    return api


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


def _resolve_view(view: MitupView | FormattedText | str) -> MitupView:
    if isinstance(view, MitupView):
        return view
    if isinstance(view, FormattedText):
        return MitupView(view, keyboard=[])
    return MitupView(view, keyboard=[])


class TelegramApiWrapper(Protocol):
    @property
    def adapter(self) -> ContextOrBotAdapter: ...
    @adapter.setter
    def adapter(self, adapter: ContextOrBotAdapter): ...
    async def send_message(self, update: Update, view: MitupView | FormattedText | str) -> Message | None: ...
    async def send_message_to_user(self, user: User, view: MitupView | FormattedText | str) -> Message | None: ...
    async def send_messages_to_users(
        self,
        users: Sequence[User],
        views: Sequence[MitupView | FormattedText | str],
        on_success: Sequence[Callable[[User], None]] | None = None,
        on_error: Sequence[Callable[[User, Exception], None]] | None = None,
    ) -> None: ...
    async def edit_message(self, update: Update, view: MitupView | FormattedText | str) -> Message | bool: ...
    async def answer_inline_query(
        self,
        update: Update,
        results: list[MitupInlineView],
        button: InlineResultsButton | None = None,
        cache_time: int = 60,
    ) -> None: ...
    async def answer_callback_query(self, update: Update, text: str | FormattedText, show_alert: bool) -> None: ...
    async def update_single_meeting_message(
        self,
        message: MessageModel,
        session: Session,
        meeting: Meetup,
        was_deleted: bool = False,
        has_finished: bool = False,
    ) -> None: ...
    async def update_meeting_messages(
        self,
        *,
        session: Session,
        meeting: Meetup,
        current_message: MessageModel | None = None,
        skip_current: bool = False,
        was_deleted: bool = False,
        has_finished: bool = False,
    ) -> None: ...
    async def notify_users_promoted_from_waiting_list(
        self,
        joined_users: Sequence[JoinedUsers],
        meeting: Meetup,
    ) -> None: ...


class TelegramApi:
    def __init__(self):
        self._adapter: ContextOrBotAdapter | None = None

    @property
    def adapter(self) -> ContextOrBotAdapter:
        if self._adapter is None:
            raise ValueError("Adapter not set")
        return self._adapter

    @adapter.setter
    def adapter(self, adapter: ContextOrBotAdapter):
        self._adapter = adapter

    async def send_message(self, update: Update, view: MitupView | FormattedText | str) -> Message | None:
        from mitup_bot import guards

        chat_id = guards.chat(update).id
        resolved = _resolve_view(view)

        with self.adapter.with_time_metric(prefix=TELEMGRAM_API_TIME_PREFIX):
            return await self.adapter.bot.send_message(
                chat_id=chat_id,
                text=resolved.description.text,
                entities=resolved.description.entities or None,
                reply_markup=resolved.markup,
            )

    @contextmanager
    def _with_time_metrics_context(self) -> Generator[None]:
        """
        Context manager that can be used either with a context or a bot. If a context is passed,
        a metric will be emitted with the time it took to send the API call to Telegram.
        """
        # We check if it is an instance of CallbackContext because the adapter wraps it
        # but does not inherit from it
        if isinstance(self.adapter, CallbackContext):
            with self.adapter.with_time_metric(prefix=TELEMGRAM_API_TIME_PREFIX):
                yield
        else:
            yield

    async def send_message_to_user(self, user: User, view: MitupView | FormattedText | str) -> Message | None:
        resolved = _resolve_view(view)

        with self._with_time_metrics_context():
            try:
                return await self.adapter.bot.send_message(
                    chat_id=user.tg_user_id,
                    text=resolved.description.text,
                    entities=resolved.description.entities or None,
                    reply_markup=resolved.markup,
                )
            except Forbidden as e:
                logging.warning(f"User {user.tg_user_id} has blocked the bot.")
                raise InactiveUserInteraction(user.tg_user_id, private=True) from e
            except BadRequest as e:
                if "not found" in e.message:
                    logging.warning(f"User {user.tg_user_id} is not in Telegram.")
                    raise InactiveUserInteraction(user.tg_user_id, private=True) from e
                raise

    async def send_messages_to_users(
        self,
        users: Sequence[User],
        views: Sequence[MitupView | FormattedText | str],
        on_success: Sequence[Callable[[User], None]] | None = None,
        on_error: Sequence[Callable[[User, Exception], None]] | None = None,
    ):
        """
        Sends messages to multiple users.

        If a user has blocked the bot or is no longer available, they will be marked as inactive
        in the database and no exception will be raised.
        """

        if len(users) != len(views):
            raise ValueError("The number of users and views must be the same")

        awaitables = [
            self.send_message_to_user(
                user=user,
                view=views[i],
            )
            for i, user in enumerate(users)
        ]

        results = await gather(*awaitables, return_exceptions=True)

        for idx, (user, result) in enumerate(zip(users, results, strict=True)):
            if isinstance(result, InactiveUserInteraction):
                # Handle inactive user different for other errors
                # we do not want to error out but mark the user as inactive
                logging.info(f"Marking user {user.tg_user_id} as inactive")
                user.is_active = False
                self.adapter.emit_metric(MetricKey.INACTIVE_USER_SET, include_handler_dimensions=False)
                continue

            # Handle Callbacks
            if on_error and isinstance(result, Exception):
                logging.exception(f"Error sending message to user {user.id}: {result}")
                on_error[idx](user, result)
            elif on_success:
                on_success[idx](user)

    async def notify_users_promoted_from_waiting_list(
        self,
        joined_users: Sequence[JoinedUsers],
        meeting: Meetup,
    ):
        users = [link.user for link in joined_users if link.invited_by is None]
        views_to_send = [
            MeetingMessages.PROMOTED_FROM_THE_WAITING_LIST.get(lang=user.lang, meeting_title=meeting.title)
            for user in users
        ]
        await self.send_messages_to_users(
            users=users,
            views=views_to_send,
        )

    async def edit_message(self, update: Update, view: MitupView | FormattedText | str) -> Message | bool:
        resolved = _resolve_view(view)

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

        with self.adapter.with_time_metric(prefix=TELEMGRAM_API_TIME_PREFIX):
            with handle_edit_errors(adapter=self.adapter):
                return await self.adapter.bot.edit_message_text(
                    text=resolved.description.text,
                    entities=resolved.description.entities or None,
                    chat_id=chat_id,
                    message_id=message_id,
                    inline_message_id=inline_message_id,
                    reply_markup=resolved.markup,
                )

    async def answer_inline_query(
        self,
        update: Update,
        results: list[MitupInlineView],
        button: InlineResultsButton | None = None,
        cache_time: int = 60,
    ):
        from mitup_bot import guards

        query = guards.valid_inline_query(update)
        inline_results = [
            InlineQueryResultArticle(
                id=view.id,
                title=view.title,
                description=view.inline_description,
                input_message_content=InputTextMessageContent(
                    message_text=view.description.text,
                    entities=view.description.entities or None,
                ),
                reply_markup=view.markup,
            )
            for view in results
        ]
        tg_button = (
            InlineQueryResultsButton(text=button.text, start_parameter=button.start_parameter) if button else None
        )
        if await self.adapter.bot.answer_inline_query(
            query.id, results=inline_results, button=tg_button, cache_time=cache_time
        ):
            return
        raise AnswerInlineQueryError(query.query)

    async def answer_callback_query(self, update: Update, text: str | FormattedText, show_alert: bool):
        from mitup_bot import guards

        if isinstance(text, FormattedText) and text.entities:
            raise ValueError("Callback query text should not contain entities")

        _text = text.text if isinstance(text, FormattedText) else text

        if len(_text) > 200:
            CallbackQueryTextTooLong(_text)
        query = guards.valid_callback_query(update)
        await self.adapter.bot.answer_callback_query(query.id, text=_text, show_alert=show_alert)

    async def update_single_meeting_message(
        self,
        message: MessageModel,
        session: Session,
        meeting: Meetup,
        was_deleted: bool = False,
        has_finished: bool = False,
    ):
        """
        Updates a single meeting message with the current meeting view.

        Args:
            message: The message model to update.
            session: The database session.
            meeting: The meeting object.
            was_deleted: If set to True, the meeting has been deleted and the messages will be updated
                         to inform the user.
            has_finished: If set to True, the meeting has finished and the messages will be updated to inform the user.
        """

        view = (
            meeting.inline_view(chat_instance=message.chat_instance)
            if message.inline_message_id or message.chat_id != meeting.owner.tg_user_id
            else meeting.main_view
        )

        # Update the stored buttons to match the current view to ensure they are persisted
        # TODO: We might want to remove the persistency on this message. Not sure what was the
        # reason to have it to begin with
        message.buttons.keyboard = view.keyboard
        session.add(message)

        # Determine the text, entities and markup based on meeting state
        if was_deleted:
            ftext = MeetingMessages.MEETING_HAS_BEEN_DELETED.get(lang=meeting.lang)
            text = ftext.text
            entities = ftext.entities or None
            reply_markup = None
        elif has_finished:
            finished_view = view.with_context(MeetingMessages.MEETING_HAS_FINISHED.get(lang=meeting.lang))
            text = finished_view.description.text
            entities = finished_view.description.entities or None
            reply_markup = None
        else:
            text = view.description.text
            entities = view.description.entities or None
            reply_markup = view.markup

        with (
            self.adapter.with_time_metric(prefix=TELEMGRAM_API_TIME_PREFIX),
            handle_edit_errors(adapter=self.adapter, message=message, session=session),
        ):
            await self.adapter.bot.edit_message_text(
                text=text,
                entities=entities,
                chat_id=message.chat_id,
                message_id=message.message_id,
                inline_message_id=message.inline_message_id,
                reply_markup=reply_markup,
            )

    async def update_meeting_messages(
        self,
        *,
        session: Session,
        meeting: Meetup,
        current_message: MessageModel | None = None,
        skip_current=False,
        was_deleted=False,
        has_finished=False,
    ):
        """
        Updates meeting messages with the current meeting view.

        Args:
            session: The database session.
            meeting: The Meetup object.
            current_message: The current message model, if any. If provided, it will be edited before any other message.
            skip_current: If set to True, the current message will be skipped. This is needed if the current message
                          is being updated in a different way.
            was_deleted: If set to True, the meeting has been deleted and the messages will be updated
                         to inform the user.
            has_finished: If set to True, the meeting has finished and the messages will be updated to inform the user.
        """
        # First lets update the current message for a better user experience
        if current_message and not skip_current:
            await self.update_single_meeting_message(current_message, session, meeting, was_deleted, has_finished)
        for message in meeting.messages:
            if message == current_message:
                continue
            await self.update_single_meeting_message(message, session, meeting, was_deleted, has_finished)
