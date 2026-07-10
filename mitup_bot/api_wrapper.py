import re
from asyncio import gather
from collections.abc import Callable, Coroutine, Generator, Sequence
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from functools import partial
from time import perf_counter
from typing import TYPE_CHECKING, Any, Protocol, cast

import structlog
from telegram import (
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InlineQueryResultsButton,
    InputTextMessageContent,
    Message,
    MessageEntity,
    Update,
)
from telegram.error import BadRequest, Forbidden, NetworkError
from telegram.ext import ExtBot

from mitup_bot.exceptions import (
    AnswerInlineQueryError,
    CallbackQueryTextTooLong,
    InactiveUserInteraction,
    NoDocumentAvailable,
    NoMessageAvailable,
)
from mitup_bot.models import Meetup, User
from mitup_bot.models import Message as MessageModel
from mitup_bot.models.joined_users import JoinedUsers
from mitup_bot.monitoring import MetricKey
from mitup_bot.monitoring.client import MetricsClient
from mitup_bot.monitoring.units import MetricUnit
from mitup_bot.protocols import ContextOrBotAdapter
from mitup_bot.utils import MeetingDisplayMessages, MeetingJoinMessages
from mitup_bot.utils.entities import FormattedText
from mitup_bot.views import InlineResultsButton, MitupInlineView, MitupView

TELEMGRAM_API_TIME_PREFIX = "TelegramApi"
MESSAGE_NOT_FOUND_ERROR_PATTERNS = [
    re.compile(r"Message_id_invalid"),
    re.compile(r"Message to edit not found"),
]
EDIT_MESSAGE_ERRORS_TO_IGNORE_PATTERNS = [re.compile(r"Message is not modified")]

log = structlog.get_logger(__name__)

# Telegram Bot API hard limit for answerCallbackQuery text.
CALLBACK_QUERY_TEXT_LIMIT = 200


if TYPE_CHECKING:
    ...


@dataclass
class QueuedApiCall:
    """A Telegram call captured during a write-mode handler, to be executed after commit.

    ``invoke`` is a zero-argument closure over plain data (chat ids, message ids, rendered
    view content) snapshotted at enqueue time — it must never touch the session or trigger
    an ORM load when awaited.
    """

    name: str
    invoke: Callable[[], Coroutine[Any, Any, object]]


@dataclass
class ApiOutbox:
    """Queued Telegram calls plus the DB fix-ups their execution discovers.

    The fix-up lists (`dead_message_ids`, `inactive_tg_user_ids`) are filled while the queue
    drains — after the caller's transaction committed — and applied by the write lifecycle
    in a short follow-up transaction (see ``db.begin_write``).
    """

    calls: list[QueuedApiCall] = field(default_factory=list)
    dead_message_ids: list[int] = field(default_factory=list)
    inactive_tg_user_ids: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class MeetingMessageEdit:
    """Plain-data snapshot of one meeting-message edit, rendered at enqueue time."""

    message_db_id: int | None
    chat_id: int | None
    message_id: int | None
    inline_message_id: str | None
    text: str
    entities: Sequence[MessageEntity] | None
    reply_markup: InlineKeyboardMarkup | None


class BotAdapter:
    """Adapter that wraps an ExtBot with a MetricsClient to conform to ContextOrBotAdapter."""

    def __init__(self, bot: ExtBot, metrics: MetricsClient):
        self._bot = bot
        self._metrics = metrics

    @property
    def bot(self) -> ExtBot:
        return self._bot

    @contextmanager
    def with_time_metric(self, prefix: str, handler_metrics: bool = False) -> Generator[None]:
        start = perf_counter()
        yield
        elapsed = 1000 * (perf_counter() - start)
        self._metrics.emit(
            MetricKey.TIME.with_prefix(prefix, separator=""),
            elapsed,
            MetricUnit.MILLISECONDS,
        )

    def emit_metric(
        self,
        name: str | MetricKey,
        value: float = 1.0,
        unit: MetricUnit = MetricUnit.COUNT,
        *,
        dimensions: dict[str, str] | None = None,
        properties: dict[str, Any] | None = None,
    ):
        self._metrics.emit(name, value, unit, dimensions=dimensions, properties=properties)

    async def flush_metrics(self):
        await self._metrics.flush()


def build_api(adapter_or_bot: ContextOrBotAdapter | ExtBot) -> TelegramApiWrapper:
    """Build a TelegramApi from an adapter or a bare ExtBot.

    When an ExtBot is passed directly, it is wrapped in a BotAdapter with a NullBackend.
    Prefer constructing BotAdapter(bot, metrics_client) explicitly for real metric emission.
    """
    from mitup_bot.monitoring.backend import NullBackend

    if isinstance(adapter_or_bot, ExtBot):
        adapter: ContextOrBotAdapter = BotAdapter(adapter_or_bot, MetricsClient(NullBackend()))
    else:
        adapter = adapter_or_bot
    api = TelegramApi()
    api.adapter = adapter
    return api


@asynccontextmanager
async def handle_edit_errors(adapter: ContextOrBotAdapter):
    try:
        yield
    except BadRequest as e:
        # Sometimes the message does not need to be updated but we don't know that in advance
        # ignore the error when it happens
        if any(pattern.findall(e.message) for pattern in EDIT_MESSAGE_ERRORS_TO_IGNORE_PATTERNS):
            return

        # The message was deleted by the user; nothing to edit anymore
        if any(pattern.findall(e.message) for pattern in MESSAGE_NOT_FOUND_ERROR_PATTERNS):
            adapter.emit_metric(MetricKey.MESSAGE_DELETED)
            return
        raise


def resolve_view(view: MitupView | FormattedText | str) -> MitupView:
    if isinstance(view, MitupView):
        return view
    if isinstance(view, FormattedText):
        return MitupView(view, keyboard=[])
    return MitupView(view, keyboard=[])


def edit_target(update: Update) -> tuple[int | None, int | None, str | None]:
    """Extract (chat_id, message_id, inline_message_id) for an edit from the update."""
    if update.effective_message:
        return update.effective_message.chat.id, update.effective_message.id, None
    if update.callback_query and update.callback_query.inline_message_id:
        return None, None, update.callback_query.inline_message_id
    raise NoMessageAvailable("Cannot edit message, neither message_id nor inline_message_id is available")


class TelegramApiWrapper(Protocol):
    @property
    def adapter(self) -> ContextOrBotAdapter: ...
    @adapter.setter
    def adapter(self, adapter: ContextOrBotAdapter): ...
    @property
    def immediate(self) -> TelegramApiWrapper: ...
    # The capture lifecycle, driven by db.begin_write (directly or via with_session(write=True)).
    def begin_capture(self) -> ApiOutbox: ...
    def end_capture(self): ...
    async def execute_queued(self, outbox: ApiOutbox): ...
    async def send_message(self, update: Update, view: MitupView | FormattedText | str) -> Message | None: ...
    async def send_document(self, update: Update, view: MitupView) -> Message | None: ...
    async def send_message_to_user(self, user: User, view: MitupView | FormattedText | str) -> Message | None: ...
    async def send_messages_to_users(
        self,
        users: Sequence[User],
        views: Sequence[MitupView | FormattedText | str],
        on_success: Sequence[Callable[[User], None]] | None = None,
        on_error: Sequence[Callable[[User, Exception], None]] | None = None,
    ): ...
    async def edit_message(self, update: Update, view: MitupView | FormattedText | str) -> Message | bool: ...
    async def edit_message_for_user(
        self, user: User, message_id: int, view: MitupView | FormattedText | str
    ) -> Message | bool: ...
    async def answer_inline_query(
        self,
        update: Update,
        results: list[MitupInlineView],
        button: InlineResultsButton | None = None,
        cache_time: int = 60,
    ): ...
    async def answer_callback_query(self, update: Update, text: str | FormattedText, show_alert: bool): ...
    async def update_single_meeting_message(
        self,
        message: MessageModel,
        meeting: Meetup,
        was_deleted: bool = False,
        has_finished: bool = False,
    ): ...
    async def update_meeting_messages(
        self,
        *,
        meeting: Meetup,
        current_message: MessageModel | None = None,
        skip_current: bool = False,
        was_deleted: bool = False,
        has_finished: bool = False,
    ): ...
    async def notify_users_promoted_from_waiting_list(
        self,
        joined_users: Sequence[JoinedUsers],
        meeting: Meetup,
    ): ...
    async def clear_reply_markup(self, update: Update): ...


class _ImmediateApi:
    """Escape hatch for the rare call that must run before commit (`context.api.immediate.X(...)`).

    Temporarily lifts the capture mode of the wrapped api so the call executes right away,
    inside the open transaction — meaning its failure aborts the transaction. Keep usages
    rare and greppable; the default under write-mode handlers is the post-commit queue.
    """

    def __init__(self, api: TelegramApi):
        self._api = api

    def __getattr__(self, name: str):
        attribute = getattr(self._api, name)
        if not callable(attribute):
            return attribute

        async def run_immediately(*args, **kwargs):
            outbox, self._api._outbox = self._api._outbox, None
            try:
                return await attribute(*args, **kwargs)
            finally:
                self._api._outbox = outbox

        return run_immediately


class TelegramApi:
    # Class-level default so subclasses that skip __init__ (the test MockApi) still start
    # in immediate mode.
    _outbox: ApiOutbox | None = None

    def __init__(self):
        self._adapter: ContextOrBotAdapter | None = None
        self._outbox = None

    @property
    def adapter(self) -> ContextOrBotAdapter:
        if self._adapter is None:
            raise ValueError("Adapter not set")
        return self._adapter

    @adapter.setter
    def adapter(self, adapter: ContextOrBotAdapter):
        self._adapter = adapter

    @property
    def immediate(self) -> TelegramApiWrapper:
        return cast(TelegramApiWrapper, _ImmediateApi(self))

    # -- Outbox lifecycle -------------------------------------------------------------------
    # Driven exclusively by db.begin_write (directly or via with_session(write=True)):
    # capture between begin_capture and end_capture, then execute_queued after the
    # transaction committed.

    def begin_capture(self) -> ApiOutbox:
        """Switch the api into capture mode: subsequent calls enqueue instead of executing."""
        if self._outbox is not None:
            raise RuntimeError("Api capture already active; write-mode handlers cannot nest")
        self._outbox = ApiOutbox()
        return self._outbox

    def end_capture(self):
        self._outbox = None

    async def execute_queued(self, outbox: ApiOutbox):
        """Execute the queued calls in order, after the handler's transaction committed.

        Failures here are partial rendering problems — the DB is already right — so calls are
        isolated per the post-commit semantics documented in the api-wrapper skill; only
        connectivity errors (``NetworkError`` excluding its ``BadRequest`` subclass) abort the
        drain, because every remaining call would fail the same way.
        """
        for queued in outbox.calls:
            try:
                await queued.invoke()
            except InactiveUserInteraction as exc:
                # The write lifecycle's reconcile transaction marks the user inactive.
                outbox.inactive_tg_user_ids.append(exc.tg_user_id)
                log.info("User unreachable during post-commit fan-out", tg_user_id=exc.tg_user_id)
            except BadRequest as exc:
                if any(pattern.findall(exc.message) for pattern in EDIT_MESSAGE_ERRORS_TO_IGNORE_PATTERNS):
                    continue
                self._record_queued_failure(queued, exc)
            except NetworkError:
                raise
            except Exception as exc:
                self._record_queued_failure(queued, exc)

    def _record_queued_failure(self, queued: QueuedApiCall, exc: Exception):
        log.exception("Queued Telegram call failed after commit", queued_call=queued.name, exc_info=exc)
        # Mirror the error handler's fault shape: a per-error-type fault plus the aggregate.
        self.adapter.emit_metric(
            MetricKey.FAULT.with_prefix(type(exc).__name__), properties={"QueuedApiCall": queued.name}
        )
        self.adapter.emit_metric(MetricKey.FAULT, properties={"QueuedApiCall": queued.name})

    def _enqueue(self, name: str, invoke: Callable[[], Coroutine[Any, Any, object]]):
        assert self._outbox is not None
        self._outbox.calls.append(QueuedApiCall(name, invoke))

    async def _call_or_enqueue[T](
        self, name: str, invoke: Callable[[], Coroutine[Any, Any, T]], default_result: T
    ) -> T:
        """Shared mode branch for the public api methods: execute ``invoke`` immediately, or
        under capture enqueue it and return ``default_result``. Callers prepare ``invoke``
        beforehand so validation and view rendering always happen at enqueue time."""
        if self._outbox is not None:
            self._enqueue(name, invoke)
            return default_result
        return await invoke()

    # -- Public api -------------------------------------------------------------------------

    async def send_message(self, update: Update, view: MitupView | FormattedText | str) -> Message | None:
        from mitup_bot import guards

        chat_id = guards.chat(update).id
        resolved = resolve_view(view)
        return await self._call_or_enqueue(
            "send_message", partial(self._send_chat_message_now, chat_id, resolved), None
        )

    async def _send_chat_message_now(self, chat_id: int, view: MitupView) -> Message | None:
        with self.adapter.with_time_metric(prefix=TELEMGRAM_API_TIME_PREFIX):
            return await self.adapter.bot.send_message(
                chat_id=chat_id,
                text=view.description.text,
                entities=view.description.entities or None,
                reply_markup=view.markup,
                disable_web_page_preview=True,
            )

    async def send_document(self, update: Update, view: MitupView) -> Message | None:
        """Send the view's document to the chat from the update, with the view's description
        as the caption and its keyboard as the reply markup."""
        from mitup_bot import guards

        chat_id = guards.chat(update).id
        if view.document is None:
            raise NoDocumentAvailable("Cannot send document, the view carries no document")
        # Rendered at enqueue time so the queued call carries only plain data under capture.
        return await self._call_or_enqueue(
            "send_document",
            partial(
                self._send_document_now,
                chat_id,
                view.document.content,
                view.document.filename,
                view.description,
                view.markup,
            ),
            None,
        )

    async def _send_document_now(
        self,
        chat_id: int,
        document: bytes,
        filename: str,
        caption: FormattedText | str | None,
        reply_markup: InlineKeyboardMarkup | None,
    ) -> Message | None:
        caption_text = caption.text if isinstance(caption, FormattedText) else caption
        caption_entities = (caption.entities or None) if isinstance(caption, FormattedText) else None
        with self.adapter.with_time_metric(prefix=TELEMGRAM_API_TIME_PREFIX):
            return await self.adapter.bot.send_document(
                chat_id=chat_id,
                document=document,
                filename=filename,
                caption=caption_text,
                caption_entities=caption_entities,
                reply_markup=reply_markup,
            )

    async def send_message_to_user(self, user: User, view: MitupView | FormattedText | str) -> Message | None:
        resolved = resolve_view(view)
        return await self._call_or_enqueue(
            "send_message_to_user", partial(self._send_user_message_now, user.tg_user_id, resolved), None
        )

    async def _send_user_message_now(self, tg_user_id: int, view: MitupView) -> Message | None:
        with self.adapter.with_time_metric(prefix=TELEMGRAM_API_TIME_PREFIX):
            try:
                return await self.adapter.bot.send_message(
                    chat_id=tg_user_id,
                    text=view.description.text,
                    entities=view.description.entities or None,
                    reply_markup=view.markup,
                    disable_web_page_preview=True,
                )
            except Forbidden as e:
                log.warning("User has blocked the bot", tg_user_id=tg_user_id)
                raise InactiveUserInteraction(tg_user_id, private=True) from e
            except BadRequest as e:
                if "not found" in e.message:
                    log.warning("User is not in Telegram", tg_user_id=tg_user_id)
                    raise InactiveUserInteraction(tg_user_id, private=True) from e
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
        in the database and no exception will be raised. Under capture mode the marking happens
        in the decorator's reconcile transaction instead of inline.
        """

        if len(users) != len(views):
            raise ValueError("The number of users and views must be the same")

        if self._outbox is not None:
            if on_success or on_error:
                # The callbacks run against live ORM objects; after commit their effects would
                # be silently lost. Callers that need them must opt into pre-commit execution.
                raise ValueError("Result callbacks cannot run after commit; use context.api.immediate instead")
            for user, view in zip(users, views, strict=True):
                self._enqueue(
                    "send_messages_to_users",
                    partial(self._send_user_message_now, user.tg_user_id, resolve_view(view)),
                )
            return

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
                if user.mark_inactive():
                    log.info("Marking user as inactive", tg_user_id=user.tg_user_id)
                    self.adapter.emit_metric(MetricKey.INACTIVE_USER_SET)
                continue

            # Handle Callbacks
            if on_error and isinstance(result, Exception):
                log.exception("Error sending message to user", user_id=user.id, exc_info=result)
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
            MeetingJoinMessages.PROMOTED_FROM_WAITING_LIST.get(lang=user.lang, meeting_title=meeting.title)
            for user in users
        ]
        await self.send_messages_to_users(
            users=users,
            views=views_to_send,
        )

    async def edit_message(self, update: Update, view: MitupView | FormattedText | str) -> Message | bool:
        resolved = resolve_view(view)
        target = edit_target(update)
        return await self._call_or_enqueue(
            "edit_message", partial(self._edit_message_now, target, resolved), cast("Message | bool", False)
        )

    async def _edit_message_now(
        self, target: tuple[int | None, int | None, str | None], view: MitupView
    ) -> Message | bool:
        chat_id, message_id, inline_message_id = target
        with self.adapter.with_time_metric(prefix=TELEMGRAM_API_TIME_PREFIX):
            async with handle_edit_errors(adapter=self.adapter):
                return await self.adapter.bot.edit_message_text(
                    text=view.description.text,
                    entities=view.description.entities or None,
                    chat_id=chat_id,
                    message_id=message_id,
                    inline_message_id=inline_message_id,
                    reply_markup=view.markup,
                    disable_web_page_preview=True,
                )
        return False

    async def edit_message_for_user(
        self, user: User, message_id: int, view: MitupView | FormattedText | str
    ) -> Message | bool:
        """Edit a message in a user's private chat by explicit ``message_id``, without an ``Update``.

        The private-chat id equals the user's ``tg_user_id``, so the edit target is derived from the
        user rather than an incoming update — this is the edit counterpart of ``send_message_to_user``
        for out-of-band callers (e.g. the Patreon OAuth web callback refreshing the tapped Collaborate
        message). Routes through the same ``_edit_message_now`` suppression as update-based edits.
        """
        resolved = resolve_view(view)
        target: tuple[int | None, int | None, str | None] = (user.tg_user_id, message_id, None)
        return await self._call_or_enqueue(
            "edit_message_for_user", partial(self._edit_message_now, target, resolved), cast("Message | bool", False)
        )

    async def clear_reply_markup(self, update: Update):
        target = edit_target(update)
        await self._call_or_enqueue("clear_reply_markup", partial(self._clear_reply_markup_now, target), None)

    async def _clear_reply_markup_now(self, target: tuple[int | None, int | None, str | None]):
        chat_id, message_id, inline_message_id = target
        with self.adapter.with_time_metric(prefix=TELEMGRAM_API_TIME_PREFIX):
            async with handle_edit_errors(adapter=self.adapter):
                await self.adapter.bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=message_id,
                    inline_message_id=inline_message_id,
                    reply_markup=None,
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
        await self._call_or_enqueue(
            "answer_inline_query",
            partial(self._answer_inline_query_now, query.id, query.query, inline_results, tg_button, cache_time),
            None,
        )

    async def _answer_inline_query_now(
        self,
        query_id: str,
        query_text: str,
        inline_results: list[InlineQueryResultArticle],
        tg_button: InlineQueryResultsButton | None,
        cache_time: int,
    ):
        if await self.adapter.bot.answer_inline_query(
            query_id, results=inline_results, button=tg_button, cache_time=cache_time
        ):
            return
        raise AnswerInlineQueryError(query_text)

    async def answer_callback_query(self, update: Update, text: str | FormattedText, show_alert: bool):
        from mitup_bot import guards

        if isinstance(text, FormattedText) and text.entities:
            raise ValueError("Callback query text should not contain entities")

        _text = text.text if isinstance(text, FormattedText) else text

        if len(_text) > CALLBACK_QUERY_TEXT_LIMIT:
            raise CallbackQueryTextTooLong(_text)
        query = guards.valid_callback_query(update)
        await self._call_or_enqueue(
            "answer_callback_query", partial(self._answer_callback_query_now, query.id, _text, show_alert), None
        )

    async def _answer_callback_query_now(self, query_id: str, text: str, show_alert: bool):
        await self.adapter.bot.answer_callback_query(query_id, text=text, show_alert=show_alert)

    def _render_meeting_message_edit(
        self,
        message: MessageModel,
        meeting: Meetup,
        was_deleted: bool,
        has_finished: bool,
    ) -> MeetingMessageEdit:
        """Render one stored meeting message into a plain edit payload.

        `has_finished` clears buttons; uses the enriched summary when `end_datetime` is set.
        """

        view = (
            meeting.inline_view(chat_instance=message.chat_instance)
            if message.inline_message_id or message.chat_id != meeting.owner.tg_user_id
            else meeting.main_view()
        )

        # Update the stored buttons to match the current view to ensure they are persisted.
        # The mutation is tracked by MutableModel and lands with the surrounding transaction's
        # flush/commit — the message is either already persistent or pending via the meeting's
        # messages cascade, so no explicit session.add is needed.
        # TODO: We might want to remove the persistency on this message. Not sure what was the
        # reason to have it to begin with
        message.buttons.keyboard = view.keyboard

        # Determine the text, entities and markup based on meeting state
        if was_deleted:
            ftext = MeetingDisplayMessages.DELETED_BANNER.get(lang=meeting.lang)
            text = ftext.text
            entities = ftext.entities or None
            reply_markup = None
        elif has_finished:
            finished_message = (
                MeetingDisplayMessages.FINISHED_SUMMARY_BANNER.get(
                    lang=meeting.lang,
                    start_datetime=f"{meeting.datetime:%Y-%m-%d %H:%M}" if meeting.datetime else "?",
                    end_datetime=f"{meeting.end_datetime:%Y-%m-%d %H:%M}" if meeting.end_datetime else "?",
                    attendee_count=meeting.n_participants,
                )
                if meeting.end_datetime is not None
                else MeetingDisplayMessages.FINISHED_BANNER.get(lang=meeting.lang)
            )
            finished_view = view.with_context(finished_message)
            text = finished_view.description.text
            entities = finished_view.description.entities or None
            reply_markup = None
        else:
            text = view.description.text
            entities = view.description.entities or None
            reply_markup = view.markup

        return MeetingMessageEdit(
            message_db_id=message.id,
            chat_id=message.chat_id,
            message_id=message.message_id,
            inline_message_id=message.inline_message_id,
            text=text,
            entities=entities,
            reply_markup=reply_markup,
        )

    async def _edit_meeting_message_now(self, edit: MeetingMessageEdit) -> bool:
        """Execute a rendered meeting-message edit. Returns True when Telegram reports the
        message gone (deleted by the user), leaving the DB cleanup to the caller."""
        with self.adapter.with_time_metric(prefix=TELEMGRAM_API_TIME_PREFIX):
            try:
                await self.adapter.bot.edit_message_text(
                    text=edit.text,
                    entities=edit.entities,
                    chat_id=edit.chat_id,
                    message_id=edit.message_id,
                    inline_message_id=edit.inline_message_id,
                    reply_markup=edit.reply_markup,
                    disable_web_page_preview=True,
                )
            except BadRequest as e:
                # Sometimes the message does not need to be updated but we don't know that in
                # advance — ignore the error when it happens
                if any(pattern.findall(e.message) for pattern in EDIT_MESSAGE_ERRORS_TO_IGNORE_PATTERNS):
                    return False
                if any(pattern.findall(e.message) for pattern in MESSAGE_NOT_FOUND_ERROR_PATTERNS):
                    self.adapter.emit_metric(MetricKey.MESSAGE_DELETED)
                    return True
                raise
        return False

    async def _queued_meeting_message_edit(self, edit: MeetingMessageEdit, outbox: ApiOutbox):
        if await self._edit_meeting_message_now(edit) and edit.message_db_id is not None:
            outbox.dead_message_ids.append(edit.message_db_id)

    async def update_single_meeting_message(
        self,
        message: MessageModel,
        meeting: Meetup,
        was_deleted: bool = False,
        has_finished: bool = False,
    ):
        """
        Updates a single meeting message with the current meeting view.

        The view renders at call time; under capture the plain payload is queued and a
        user-deleted message is recorded for the write lifecycle's reconcile transaction,
        which owns the dead-row cleanup for every caller. In immediate mode a dead message
        only emits its metric — the stale row is picked up by the next write-mode fan-out.
        """
        edit = self._render_meeting_message_edit(message, meeting, was_deleted, has_finished)
        if self._outbox is not None:
            self._enqueue("update_meeting_message", partial(self._queued_meeting_message_edit, edit, self._outbox))
            return
        await self._edit_meeting_message_now(edit)

    async def update_meeting_messages(
        self,
        *,
        meeting: Meetup,
        current_message: MessageModel | None = None,
        skip_current: bool = False,
        was_deleted: bool = False,
        has_finished: bool = False,
    ):
        """
        Updates all tracked messages for a meeting, `current_message` first for immediate
        feedback (`skip_current` when the caller already handles it separately).
        """
        # First lets update the current message for a better user experience
        if current_message and not skip_current:
            await self.update_single_meeting_message(
                current_message,
                meeting,
                was_deleted=was_deleted,
                has_finished=has_finished,
            )
        for message in meeting.messages:
            if message == current_message:
                continue
            await self.update_single_meeting_message(
                message,
                meeting,
                was_deleted=was_deleted,
                has_finished=has_finished,
            )
