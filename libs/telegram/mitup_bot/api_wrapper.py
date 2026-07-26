import re
from asyncio import gather, sleep
from collections.abc import Callable, Coroutine, Generator, Sequence
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from enum import Enum, auto
from functools import partial
from time import perf_counter
from typing import TYPE_CHECKING, Any, Protocol, cast

import httpx
import structlog
from telegram import (
    CallbackQuery,
    Chat,
    ChatMember,
    ChatMemberRestricted,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultsButton,
    InputTextMessageContent,
    Message,
    MessageEntity,
    Update,
)
from telegram.constants import ChatMemberStatus
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
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.meeting_text import rich_title

TELEMGRAM_API_TIME_PREFIX = "TelegramApi"
MESSAGE_NOT_FOUND_ERROR_PATTERNS = [
    re.compile(r"Message_id_invalid"),
    re.compile(r"Message to edit not found"),
    # The whole chat is unknown to the bot (private chat deleted, bot kicked from the group):
    # the stored message can never be edited again, so it gets the same dead-message treatment.
    re.compile(r"Chat not found"),
]
EDIT_MESSAGE_ERRORS_TO_IGNORE_PATTERNS = [re.compile(r"Message is not modified")]
# Sending custom_emoji entities requires the bot owner's Telegram Premium; when that lapses
# Telegram rejects the whole call. Matched conservatively: only errors that name the entity.
CUSTOM_EMOJI_REJECTION_PATTERN = re.compile(r"custom[ _]emoji", re.IGNORECASE)

log = structlog.get_logger(__name__)

# Telegram Bot API hard limit for answerCallbackQuery text.
CALLBACK_QUERY_TEXT_LIMIT = 200

# Bounds for the post-commit drain: the user's action is already committed, so the budget spent
# rendering it is capped per call and the drain stops once the network looks dead.
QUEUED_CALL_ATTEMPTS = 3
QUEUED_CALL_RETRY_BACKOFF_SECONDS = 0.25
DRAIN_NETWORK_FAILURE_LIMIT = 3

# httpx failures raised before any byte of the request left this process. PTB wraps every httpx
# transport error in NetworkError (or TimedOut) and keeps the original as __cause__, which is the
# only place the distinction survives.
CONNECT_FAILURE_CAUSES = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


if TYPE_CHECKING:
    ...


@dataclass(frozen=True)
class UpdateGuards:
    """The Update-field validators the api resolves before sending: pull the chat, inline
    query, or callback query off an incoming Update, raising the app's guard errors when a
    field is missing. Injected at startup (`set_update_guards`) so this rendering layer stays
    free of the guards/handlers layer, which lives in the root package."""

    chat: Callable[[Update], Chat]
    valid_inline_query: Callable[[Update], InlineQuery]
    valid_callback_query: Callable[[Update], CallbackQuery]


class UpdateGuardsNotRegisteredError(RuntimeError):
    def __init__(self):
        super().__init__(
            "No update guards have been registered. See mitup_bot.api_wrapper.set_update_guards for information."
        )


__update_guards: UpdateGuards | None = None


def set_update_guards(guards: UpdateGuards):
    """Register the Update-field validators the api uses before sending. Every process entry
    point that sends through the api wires this once at startup (see mitup_bot.api_guards),
    mirroring the db reconciler registration in mitup_bot.reconcile."""
    global __update_guards
    __update_guards = guards


def get_update_guards() -> UpdateGuards:
    if __update_guards is None:
        raise UpdateGuardsNotRegisteredError()
    return __update_guards


@dataclass
class QueuedApiCall:
    """A Telegram call captured during a write-mode handler, to be executed after commit.

    ``invoke`` is a zero-argument closure over plain data (chat ids, message ids, rendered
    view content) snapshotted at enqueue time — it must never touch the session or trigger
    an ORM load when awaited. ``payload`` is a loggable snapshot of what the call sends
    (chat/message ids, text, markup) so a post-commit failure reports what it attempted.

    ``idempotent`` says whether repeating the call can deliver anything twice. Telegram offers
    no idempotency key, so the drain reads this flag before retrying a failure that may already
    have been applied: an edit repeats harmlessly ("message is not modified" is swallowed), a
    send would post the message a second time. The closure is opaque by drain time, so only the
    enqueue site can answer this — it defaults to the safe answer.
    """

    name: str
    invoke: Callable[[], Coroutine[Any, Any, object]]
    payload: dict[str, Any] = field(default_factory=dict)
    idempotent: bool = False


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


class DrainOutcome(Enum):
    """How one queued call ended. `SENT` covers the calls Telegram accepted plus the expected
    business answers (an unreachable user, an unchanged message): those reached Telegram, so
    they say nothing about connectivity."""

    SENT = auto()
    FAILED = auto()
    NETWORK_FAILED = auto()


@dataclass
class DrainReport:
    """Running tally of one post-commit drain, reported when it finishes."""

    queued: int
    sent: int = 0
    failed: int = 0
    consecutive_network_failures: int = 0
    abandoned: list[str] = field(default_factory=list)

    def record(self, outcome: DrainOutcome):
        if outcome is DrainOutcome.SENT:
            self.sent += 1
        else:
            self.failed += 1
        self.consecutive_network_failures = (
            self.consecutive_network_failures + 1 if outcome is DrainOutcome.NETWORK_FAILED else 0
        )

    @property
    def network_is_dead(self) -> bool:
        return self.consecutive_network_failures >= DRAIN_NETWORK_FAILURE_LIMIT


def failure_never_reached_telegram(error: BaseException) -> bool:
    """Whether a network failure happened before the request went on the wire.

    Only a failure to establish the connection proves Telegram never saw the call; a read
    timeout or a mid-flight transport error leaves it unknown whether the call was applied.
    """
    return isinstance(error.__cause__, CONNECT_FAILURE_CAUSES)


def queued_call_is_retryable(queued: QueuedApiCall, error: NetworkError) -> bool:
    """Whether repeating *queued* after *error* is safe: either Telegram never received the
    first attempt, or a second delivery of this call is harmless."""
    return failure_never_reached_telegram(error) or queued.idempotent


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
    def with_time_metric(self, prefix: str) -> Generator[None]:
        """Emit `<prefix>Time` plus a continuous 0/1 `<prefix>Fault` on exit, even when the timed
        call raises, so latency series keep their failure samples and the fault series is alarmable.

        The outcome rides on `<prefix>Fault` alone, never as a property: an EMF property may only
        carry a fact that holds for the whole flush window, and within one window metric values
        accumulate into an array while properties are last-writer-wins. Per-call detail beyond the
        fault sample belongs on a structlog line.
        """
        start = perf_counter()
        success = False
        try:
            yield
            success = True
        finally:
            elapsed = 1000 * (perf_counter() - start)
            self._metrics.emit(
                MetricKey.TIME.with_prefix(prefix, separator=""),
                elapsed,
                MetricUnit.MILLISECONDS,
            )
            self._metrics.emit(MetricKey.FAULT.with_prefix(prefix, separator=""), 0 if success else 1)

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


async def retry_without_custom_emoji[T](
    send: Callable[[Sequence[MessageEntity] | None], Coroutine[Any, Any, T]],
    entities: Sequence[MessageEntity] | None,
) -> T:
    """Run *send* with *entities*; when Telegram rejects the custom emoji in them, retry once
    with only the custom_emoji entities stripped so the message still goes out with fallback
    glyphs and its remaining formatting."""
    try:
        return await send(entities)
    except BadRequest as error:
        stripped = [entity for entity in entities or [] if entity.type != MessageEntity.CUSTOM_EMOJI]
        if len(stripped) == len(entities or []) or not CUSTOM_EMOJI_REJECTION_PATTERN.search(error.message):
            raise
        log.warning("Custom emoji rejected by Telegram, retrying without them", error=error.message)
        return await send(stripped or None)


def view_log_payload(view: MitupView) -> dict[str, Any]:
    """Loggable snapshot of a rendered view — the full text plus the serialized markup.

    Attached only to failure logs: happy-path log and metric lines stay lean, but when a send
    fails the log must show everything the call tried to deliver (log retention owns the PII
    lifecycle).
    """
    return {
        "text": view.description.text,
        "markup": view.markup.to_dict() if view.markup is not None else None,
    }


def edit_target_log_payload(target: tuple[int | None, int | None, str | None]) -> dict[str, Any]:
    """Loggable identifiers of an edit target (chat, message, inline message)."""
    chat_id, message_id, inline_message_id = target
    return {"chat_id": chat_id, "message_id": message_id, "inline_message_id": inline_message_id}


def meeting_edit_log_payload(edit: MeetingMessageEdit) -> dict[str, Any]:
    """Loggable snapshot of a rendered meeting-message edit for failure diagnostics."""
    return {
        "chat_id": edit.chat_id,
        "message_id": edit.message_id,
        "inline_message_id": edit.inline_message_id,
        "text": edit.text,
        "markup": edit.reply_markup.to_dict() if edit.reply_markup is not None else None,
    }


def resolve_view(view: MitupView | FormattedText | str) -> MitupView:
    if isinstance(view, MitupView):
        return view
    if isinstance(view, FormattedText):
        return MitupView(view, keyboard=[])
    return MitupView(view, keyboard=[])


def chat_member_is_present(member: ChatMember) -> bool:
    """Whether a ChatMember counts as currently inside the chat.

    A restricted user is only present when Telegram reports `is_member` — a restricted
    non-member has already left but keeps its restrictions on record.
    """
    if isinstance(member, ChatMemberRestricted):
        return member.is_member
    return member.status in {ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER}


def chat_member_is_admin(member: ChatMember) -> bool:
    """Whether a ChatMember is the chat creator or an administrator (unbannable)."""
    return member.status in {ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR}


def chat_member_is_banned(member: ChatMember) -> bool:
    """Whether a ChatMember has been banned (kicked) from the chat."""
    return member.status == ChatMemberStatus.BANNED


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
        on_unreachable: Sequence[Callable[[User], None]] | None = None,
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
    # Immediate admin operations on a supergroup — never routed through the outbox.
    async def approve_chat_join_request(self, chat_id: int, tg_user_id: int): ...
    async def decline_chat_join_request(self, chat_id: int, tg_user_id: int): ...
    async def ban_chat_member(self, chat_id: int, tg_user_id: int): ...
    async def unban_chat_member(self, chat_id: int, tg_user_id: int, only_if_banned: bool = True): ...
    async def is_chat_member(self, chat_id: int, tg_user_id: int) -> bool: ...
    async def is_chat_admin(self, chat_id: int, tg_user_id: int) -> bool: ...
    async def is_chat_banned(self, chat_id: int, tg_user_id: int) -> bool: ...


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

        The action the user took has already landed, so nothing here is reported back to them:
        every call is attempted, its failure recorded, and the drain never raises. The one bound
        is the network — after ``DRAIN_NETWORK_FAILURE_LIMIT`` consecutive network failures the
        rest of the queue is abandoned rather than spending a timeout on each remaining delivery.
        """
        if not outbox.calls:
            return
        report = DrainReport(queued=len(outbox.calls))
        for index, queued in enumerate(outbox.calls):
            report.record(await self._drain_queued_call(queued, outbox))
            if report.network_is_dead:
                report.abandoned = [call.name for call in outbox.calls[index + 1 :]]
                log.error(
                    "Post-commit drain abandoned: Telegram unreachable",
                    consecutive_network_failures=report.consecutive_network_failures,
                    abandoned_calls=report.abandoned,
                )
                break
        log.info(
            "Post-commit drain finished",
            queued=report.queued,
            sent=report.sent,
            failed=report.failed,
            abandoned=len(report.abandoned),
        )

    async def _drain_queued_call(self, queued: QueuedApiCall, outbox: ApiOutbox) -> DrainOutcome:
        """Execute one queued call and classify how it ended, never raising: a rendering failure
        after commit is not the user's problem, and the calls behind this one are independent
        deliveries to other chats."""
        try:
            await self._invoke_queued_with_retry(queued)
        except InactiveUserInteraction as exc:
            # The write lifecycle's reconcile transaction marks the user inactive.
            outbox.inactive_tg_user_ids.append(exc.tg_user_id)
            log.info("User unreachable during post-commit fan-out", tg_user_id=exc.tg_user_id)
        except BadRequest as exc:
            if any(pattern.findall(exc.message) for pattern in EDIT_MESSAGE_ERRORS_TO_IGNORE_PATTERNS):
                return DrainOutcome.SENT
            self._record_queued_failure(queued, exc)
            return DrainOutcome.FAILED
        except NetworkError as exc:
            self._record_queued_failure(queued, exc)
            return DrainOutcome.NETWORK_FAILED
        except Exception as exc:
            self._record_queued_failure(queued, exc)
            return DrainOutcome.FAILED
        return DrainOutcome.SENT

    async def _invoke_queued_with_retry(self, queued: QueuedApiCall):
        """Await the queued call, retrying a network failure only when the repeat cannot
        double-post — Telegram has no idempotency key, so a read timeout on a send is given up
        on rather than risking a second delivery."""
        for attempt in range(1, QUEUED_CALL_ATTEMPTS + 1):
            try:
                await queued.invoke()
                return
            except BadRequest:
                # A BadRequest is a NetworkError subclass but a permanent answer from Telegram.
                raise
            except NetworkError as error:
                if attempt == QUEUED_CALL_ATTEMPTS or not queued_call_is_retryable(queued, error):
                    raise
                log.warning(
                    "Retrying queued Telegram call after network failure",
                    queued_call=queued.name,
                    attempt=attempt,
                    error=str(error),
                )
                await sleep(QUEUED_CALL_RETRY_BACKOFF_SECONDS * 2 ** (attempt - 1))

    def _record_queued_failure(self, queued: QueuedApiCall, exc: Exception):
        log.exception(
            "Queued Telegram call failed after commit",
            queued_call=queued.name,
            payload=queued.payload,
            exc_info=exc,
        )
        # The aggregate `Fault` belongs to the invocation, which is still on its way to completing
        # normally — emitting it here would append a second value to the same EMF datapoint. The
        # per-failure detail rides on the log line above, where a drain with several failures keeps
        # every one of them; EMF properties are scalar and last-writer-wins on the shared logger.
        self.adapter.emit_metric(MetricKey.POST_COMMIT_API_FAULT)

    def _enqueue(
        self,
        name: str,
        invoke: Callable[[], Coroutine[Any, Any, object]],
        payload: dict[str, Any] | None = None,
        idempotent: bool = False,
    ):
        assert self._outbox is not None
        self._outbox.calls.append(QueuedApiCall(name, invoke, payload or {}, idempotent))

    async def _call_or_enqueue[T](
        self,
        name: str,
        invoke: Callable[[], Coroutine[Any, Any, T]],
        default_result: T,
        payload: dict[str, Any] | None = None,
        idempotent: bool = False,
    ) -> T:
        """Shared mode branch for the public api methods: execute ``invoke`` immediately, or
        under capture enqueue it and return ``default_result``. Callers prepare ``invoke``
        beforehand so validation and view rendering always happen at enqueue time; ``payload``
        is the loggable snapshot of what the call sends, reported when it fails, and
        ``idempotent`` tells the post-commit drain whether the call may be retried."""
        if self._outbox is not None:
            self._enqueue(name, invoke, payload, idempotent)
            return default_result
        return await self._invoke_logging_failure(name, invoke, payload)

    async def _invoke_logging_failure[T](
        self,
        name: str,
        invoke: Callable[[], Coroutine[Any, Any, T]],
        payload: dict[str, Any] | None,
    ) -> T:
        """Await the call, logging the attempted payload on failure so a raised Telegram error
        always shows what it tried to send. ``InactiveUserInteraction`` stays silent here — it
        is an expected business signal already logged at its raise site."""
        try:
            return await invoke()
        except InactiveUserInteraction:
            raise
        except Exception:
            log.exception("Telegram call failed", telegram_call=name, payload=payload or {})
            raise

    # -- Public api -------------------------------------------------------------------------

    async def send_message(self, update: Update, view: MitupView | FormattedText | str) -> Message | None:
        chat_id = get_update_guards().chat(update).id
        resolved = resolve_view(view)
        return await self._call_or_enqueue(
            "send_message",
            partial(self._send_chat_message_now, chat_id, resolved),
            None,
            {"chat_id": chat_id} | view_log_payload(resolved),
        )

    async def _send_chat_message_now(self, chat_id: int, view: MitupView) -> Message | None:
        with self.adapter.with_time_metric(prefix=TELEMGRAM_API_TIME_PREFIX):
            return await retry_without_custom_emoji(
                lambda entities: self.adapter.bot.send_message(
                    chat_id=chat_id,
                    text=view.description.text,
                    entities=entities,
                    reply_markup=view.markup,
                    disable_web_page_preview=True,
                ),
                view.description.entities or None,
            )

    async def send_document(self, update: Update, view: MitupView) -> Message | None:
        """Send the view's document to the chat from the update, with the view's description
        as the caption and its keyboard as the reply markup."""
        chat_id = get_update_guards().chat(update).id
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
            {"chat_id": chat_id, "filename": view.document.filename, "caption": view.description.text},
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
            "send_message_to_user",
            partial(self._send_user_message_now, user.tg_user_id, resolved),
            None,
            {"chat_id": user.tg_user_id} | view_log_payload(resolved),
        )

    async def _send_user_message_now(self, tg_user_id: int, view: MitupView) -> Message | None:
        with self.adapter.with_time_metric(prefix=TELEMGRAM_API_TIME_PREFIX):
            try:
                return await retry_without_custom_emoji(
                    lambda entities: self.adapter.bot.send_message(
                        chat_id=tg_user_id,
                        text=view.description.text,
                        entities=entities,
                        reply_markup=view.markup,
                        disable_web_page_preview=True,
                    ),
                    view.description.entities or None,
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
        on_unreachable: Sequence[Callable[[User], None]] | None = None,
    ):
        """Send one view per user, classifying every user by how their send resolved.

        Exactly one callback runs per user: `on_success` when the message reached them,
        `on_unreachable` when they have blocked the bot or no longer exist, `on_error` for any
        other failure. An unreachable user is also marked inactive in the database and raises
        nothing, so a batch never fails on one blocked recipient; under capture mode the marking
        happens in the decorator's reconcile transaction instead of inline.
        """

        if len(users) != len(views):
            raise ValueError("The number of users and views must be the same")

        if self._outbox is not None:
            self._enqueue_user_messages(users, views, has_callbacks=bool(on_success or on_error or on_unreachable))
            return

        results = await gather(
            *(self.send_message_to_user(user=user, view=view) for user, view in zip(users, views, strict=True)),
            return_exceptions=True,
        )

        for index, (user, result) in enumerate(zip(users, results, strict=True)):
            if isinstance(result, InactiveUserInteraction):
                self._mark_user_inactive(user)
                if on_unreachable:
                    on_unreachable[index](user)
            elif isinstance(result, Exception):
                log.exception("Error sending message to user", user_id=user.id, exc_info=result)
                if on_error:
                    on_error[index](user, result)
            elif on_success:
                on_success[index](user)

    def _enqueue_user_messages(
        self,
        users: Sequence[User],
        views: Sequence[MitupView | FormattedText | str],
        *,
        has_callbacks: bool,
    ):
        if has_callbacks:
            # The callbacks run against live ORM objects; after commit their effects would
            # be silently lost. Callers that need them must opt into pre-commit execution.
            raise ValueError("Result callbacks cannot run after commit; use context.api.immediate instead")
        for user, view in zip(users, views, strict=True):
            resolved = resolve_view(view)
            self._enqueue(
                "send_messages_to_users",
                partial(self._send_user_message_now, user.tg_user_id, resolved),
                {"chat_id": user.tg_user_id} | view_log_payload(resolved),
            )

    def _mark_user_inactive(self, user: User):
        if user.mark_inactive():
            log.info("Marking user as inactive", tg_user_id=user.tg_user_id)
            self.adapter.emit_metric(MetricKey.INACTIVE_USER_SET)

    async def notify_users_promoted_from_waiting_list(
        self,
        joined_users: Sequence[JoinedUsers],
        meeting: Meetup,
    ):
        users = [link.user for link in joined_users if link.invited_by is None]
        views_to_send = [
            MeetingJoinMessages.PROMOTED_FROM_WAITING_LIST.get(lang=user.lang, meeting_title=rich_title(meeting))
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
            "edit_message",
            partial(self._edit_message_now, target, resolved),
            cast("Message | bool", False),
            edit_target_log_payload(target) | view_log_payload(resolved),
            idempotent=True,
        )

    async def _edit_message_now(
        self, target: tuple[int | None, int | None, str | None], view: MitupView
    ) -> Message | bool:
        chat_id, message_id, inline_message_id = target
        with self.adapter.with_time_metric(prefix=TELEMGRAM_API_TIME_PREFIX):
            async with handle_edit_errors(adapter=self.adapter):
                return await retry_without_custom_emoji(
                    lambda entities: self.adapter.bot.edit_message_text(
                        text=view.description.text,
                        entities=entities,
                        chat_id=chat_id,
                        message_id=message_id,
                        inline_message_id=inline_message_id,
                        reply_markup=view.markup,
                        disable_web_page_preview=True,
                    ),
                    view.description.entities or None,
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
            "edit_message_for_user",
            partial(self._edit_message_now, target, resolved),
            cast("Message | bool", False),
            edit_target_log_payload(target) | view_log_payload(resolved),
            idempotent=True,
        )

    async def clear_reply_markup(self, update: Update):
        target = edit_target(update)
        await self._call_or_enqueue(
            "clear_reply_markup",
            partial(self._clear_reply_markup_now, target),
            None,
            edit_target_log_payload(target),
            idempotent=True,
        )

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
        query = get_update_guards().valid_inline_query(update)
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
            {"query_id": query.id, "query": query.query, "result_count": len(inline_results)},
            idempotent=True,
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
        if isinstance(text, FormattedText) and text.entities:
            raise ValueError("Callback query text should not contain entities")

        _text = text.text if isinstance(text, FormattedText) else text

        if len(_text) > CALLBACK_QUERY_TEXT_LIMIT:
            raise CallbackQueryTextTooLong(_text)
        query = get_update_guards().valid_callback_query(update)
        await self._call_or_enqueue(
            "answer_callback_query",
            partial(self._answer_callback_query_now, query.id, _text, show_alert),
            None,
            {"query_id": query.id, "text": _text, "show_alert": show_alert},
            idempotent=True,
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
            meeting_views.inline_view(meeting, chat_instance=message.chat_instance)
            if message.inline_message_id or message.chat_id != meeting.owner.tg_user_id
            else meeting_views.main_view(meeting)
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
        message unreachable (deleted by the user, or its chat gone), leaving the DB cleanup
        to the caller."""
        with self.adapter.with_time_metric(prefix=TELEMGRAM_API_TIME_PREFIX):
            try:
                await retry_without_custom_emoji(
                    lambda entities: self.adapter.bot.edit_message_text(
                        text=edit.text,
                        entities=entities,
                        chat_id=edit.chat_id,
                        message_id=edit.message_id,
                        inline_message_id=edit.inline_message_id,
                        reply_markup=edit.reply_markup,
                        disable_web_page_preview=True,
                    ),
                    edit.entities,
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
            except Forbidden as e:
                # The chat can no longer be written to (the user blocked the bot, deactivated
                # their account, or the bot lost access to the group): the stored message can
                # never be edited again, so it gets the same dead-message treatment.
                log.warning("Meeting message unreachable, dropping it", chat_id=edit.chat_id, error=str(e))
                self.adapter.emit_metric(MetricKey.MESSAGE_DELETED)
                return True
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
            self._enqueue(
                "update_meeting_message",
                partial(self._queued_meeting_message_edit, edit, self._outbox),
                meeting_edit_log_payload(edit),
                idempotent=True,
            )
            return
        await self._invoke_logging_failure(
            "update_meeting_message",
            partial(self._edit_meeting_message_now, edit),
            meeting_edit_log_payload(edit),
        )

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

    # -- Supergroup admin operations --------------------------------------------------------
    # Immediate ops (never enqueued): join-request gating and ban/unban for the hosts-only
    # group. A Telegram failure is logged and swallowed so it never crashes the caller (the
    # join-request handler, the daily supporter-check job, or the Collaborate screen render).

    async def approve_chat_join_request(self, chat_id: int, tg_user_id: int):
        with self.adapter.with_time_metric(prefix=TELEMGRAM_API_TIME_PREFIX):
            try:
                await self.adapter.bot.approve_chat_join_request(chat_id=chat_id, user_id=tg_user_id)
            except (Forbidden, BadRequest) as e:
                log.warning("Failed to approve chat join request", chat_id=chat_id, tg_user_id=tg_user_id, error=str(e))

    async def decline_chat_join_request(self, chat_id: int, tg_user_id: int):
        with self.adapter.with_time_metric(prefix=TELEMGRAM_API_TIME_PREFIX):
            try:
                await self.adapter.bot.decline_chat_join_request(chat_id=chat_id, user_id=tg_user_id)
            except (Forbidden, BadRequest) as e:
                log.warning("Failed to decline chat join request", chat_id=chat_id, tg_user_id=tg_user_id, error=str(e))

    async def ban_chat_member(self, chat_id: int, tg_user_id: int):
        with self.adapter.with_time_metric(prefix=TELEMGRAM_API_TIME_PREFIX):
            try:
                await self.adapter.bot.ban_chat_member(chat_id=chat_id, user_id=tg_user_id)
            except (Forbidden, BadRequest) as e:
                log.warning("Failed to ban chat member", chat_id=chat_id, tg_user_id=tg_user_id, error=str(e))

    async def unban_chat_member(self, chat_id: int, tg_user_id: int, only_if_banned: bool = True):
        with self.adapter.with_time_metric(prefix=TELEMGRAM_API_TIME_PREFIX):
            try:
                await self.adapter.bot.unban_chat_member(
                    chat_id=chat_id, user_id=tg_user_id, only_if_banned=only_if_banned
                )
            except (Forbidden, BadRequest) as e:
                log.warning("Failed to unban chat member", chat_id=chat_id, tg_user_id=tg_user_id, error=str(e))

    async def _fetch_chat_member(self, chat_id: int, tg_user_id: int) -> ChatMember | None:
        with self.adapter.with_time_metric(prefix=TELEMGRAM_API_TIME_PREFIX):
            try:
                return await self.adapter.bot.get_chat_member(chat_id=chat_id, user_id=tg_user_id)
            except (Forbidden, BadRequest) as e:
                log.warning("Failed to fetch chat member", chat_id=chat_id, tg_user_id=tg_user_id, error=str(e))
                return None

    async def is_chat_member(self, chat_id: int, tg_user_id: int) -> bool:
        member = await self._fetch_chat_member(chat_id, tg_user_id)
        return member is not None and chat_member_is_present(member)

    async def is_chat_admin(self, chat_id: int, tg_user_id: int) -> bool:
        member = await self._fetch_chat_member(chat_id, tg_user_id)
        return member is not None and chat_member_is_admin(member)

    async def is_chat_banned(self, chat_id: int, tg_user_id: int) -> bool:
        member = await self._fetch_chat_member(chat_id, tg_user_id)
        return member is not None and chat_member_is_banned(member)
