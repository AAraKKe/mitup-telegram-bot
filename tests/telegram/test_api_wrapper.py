"""Tests for the real TelegramApi class with a mocked bot."""

import logging
import re
import warnings
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from structlog.contextvars import bound_contextvars
from structlog.testing import capture_logs
from telegram import (
    Bot,
    ChatMember,
    ChatMemberRestricted,
    Message,
    MessageEntity,
    Update,
)
from telegram.constants import ChatMemberStatus
from telegram.error import BadRequest, Forbidden, NetworkError, TimedOut
from telegram.ext import ExtBot
from telegram.warnings import PTBUserWarning

from mitup_bot.api_wrapper import (
    ANSWER_INLINE_QUERY_ENDPOINT,
    CALLBACK_QUERY_TEXT_LIMIT,
    EDIT_MESSAGE_TEXT_ENDPOINT,
    QUEUED_CALL_ATTEMPTS,
    SEND_RICH_MESSAGE_ENDPOINT,
    ApiOutbox,
    BotAdapter,
    KeyedSend,
    MeetingMessageEdit,
    MeetingRefresh,
    OutboxStrategy,
    QueuedApiCall,
    TelegramApi,
    build_api,
    chat_member_is_admin,
    chat_member_is_banned,
    chat_member_is_present,
    handle_edit_errors,
    meeting_job_key,
    modelled_endpoint_advisory,
    silence_modelled_endpoint_advisory,
)
from mitup_bot.card_refresh import RefreshQueue
from mitup_bot.exceptions import (
    AnswerInlineQueryError,
    CallbackQueryTextTooLong,
    InactiveUserInteraction,
    NoMessageAvailable,
)
from mitup_bot.keyboards import ButtonConfig, Keyboard
from mitup_bot.models import MeetingImage, Meetup
from mitup_bot.models import Message as MessageModel
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient
from mitup_bot.protocols import ContextOrBotAdapter
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import MAX_MESSAGE_UTF16_LENGTH, FormattedText
from mitup_bot.utils.messages import MeetingDisplayMessages
from mitup_bot.utils.rich_message import (
    DOCUMENT_ATTACH_NAME,
    DOCUMENT_MEDIA_ID,
    MAX_RICH_TEXT_LENGTH,
    RichContent,
    RichDocument,
    RichMessageTooLong,
    RichPhoto,
    photo_content,
)
from mitup_bot.views import InlineResultsButton, MitupInlineView, MitupView
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.meeting import shared_card
from tests.helpers import RichCall, log_record, make_test_metrics_client, only_rich_call, rich_call, rich_calls
from tests.helpers.fixtures import create_joined_link, create_meetup, create_message, create_user
from tests.helpers.monitoring import MetricAssertions


@pytest.fixture
def bot() -> AsyncMock:
    return AsyncMock(spec=ExtBot)


@pytest.fixture
def api_metrics_client() -> MetricsClient:
    return make_test_metrics_client()


@pytest.fixture
def adapter(bot: AsyncMock, api_metrics_client: MetricsClient) -> BotAdapter:
    return BotAdapter(bot=bot, metrics=api_metrics_client)


@pytest.fixture
def api_metrics(api_metrics_client: MetricsClient) -> MetricAssertions:
    return MetricAssertions(api_metrics_client)


@pytest.fixture
def telegram_api(adapter: BotAdapter) -> TelegramApi:
    api = TelegramApi()
    api.adapter = cast(ContextOrBotAdapter, adapter)
    return api


@pytest.fixture
def no_retry_backoff(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Run the post-commit retries without their backoff wait."""
    waited = AsyncMock()
    monkeypatch.setattr("mitup_bot.api_wrapper.sleep", waited)
    return waited


# ---------------------------------------------------------------------------
# TelegramApi.adapter property
# ---------------------------------------------------------------------------


def test_adapter_raises_when_not_set():
    api = TelegramApi()
    with pytest.raises(ValueError, match="Adapter not set"):
        _ = api.adapter


# ---------------------------------------------------------------------------
# build_api
# ---------------------------------------------------------------------------


def test_build_api_with_context():
    from mitup_bot.custom_context import MitupContext

    context = MagicMock(spec=MitupContext)
    context.__class__ = MitupContext
    api = build_api(context)
    assert isinstance(api, TelegramApi)
    assert api.adapter is context


def test_build_api_with_raw_ext_bot():
    """When an ExtBot is passed directly, it is wrapped in a BotAdapter with NullBackend."""
    raw_bot = MagicMock(spec=ExtBot)
    api = build_api(raw_bot)

    assert isinstance(api, TelegramApi)
    assert isinstance(api.adapter, BotAdapter)
    assert api.adapter.bot is raw_bot


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


async def test_send_message_with_string_view(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.effective_chat.id = 42
    sentinel = MagicMock(spec=Message)
    bot.do_api_request.return_value = sentinel

    result = await telegram_api.send_message(update, "hello")

    call = only_rich_call(bot)
    assert call.endpoint == SEND_RICH_MESSAGE_ENDPOINT
    assert call.api_kwargs == {"chat_id": 42, "rich_message": {"html": "hello", "skip_entity_detection": True}}
    assert result is sentinel


async def test_send_message_with_mitup_view(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.effective_chat.id = 99
    view = MitupView(message=RichContent("view text"), menu=[])
    sentinel = MagicMock(spec=Message)
    bot.do_api_request.return_value = sentinel

    result = await telegram_api.send_message(update, view)

    call = only_rich_call(bot)
    assert call.chat_id == 99
    assert call.html == "view text"
    assert result is sentinel


# ---------------------------------------------------------------------------
# The outbound message cap
# ---------------------------------------------------------------------------

OVER_CAP_CHARS = 250
OVER_CAP_TEXT = "x" * (MAX_MESSAGE_UTF16_LENGTH + OVER_CAP_CHARS)
OVER_CAP_EVENT = "Message text over the Telegram cap, ellipsized"


def assert_over_cap_warning(caplog: pytest.LogCaptureFixture, api_method: str):
    record = log_record(caplog, OVER_CAP_EVENT)
    assert record.levelname == "WARNING"
    assert record.__dict__["api_method"] == api_method
    assert record.__dict__["overflow"] == OVER_CAP_CHARS
    # The message text is the user's; only its overflow is reportable.
    assert "xxxxxxxxxx" not in caplog.text


async def test_send_refuses_a_view_over_the_wire_ceiling(telegram_api: TelegramApi, bot: AsyncMock):
    """Nothing is trimmed to fit: a client folds a long message behind "Show more", so length is a
    wire limit and a payload past it is a defect to surface rather than content to cut."""
    update = MagicMock(spec=Update)
    update.effective_chat.id = 42

    with pytest.raises(RichMessageTooLong):
        await telegram_api.send_message(update, MitupView(RichContent("x" * (MAX_RICH_TEXT_LENGTH + 1))))

    bot.do_api_request.assert_not_awaited()


async def test_edit_refuses_a_view_over_the_wire_ceiling(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.effective_message.chat.id = 123
    update.effective_message.id = 456

    with pytest.raises(RichMessageTooLong):
        await telegram_api.edit_message(update, MitupView(RichContent("x" * (MAX_RICH_TEXT_LENGTH + 1))))

    bot.do_api_request.assert_not_awaited()


async def test_a_view_at_the_ceiling_sends_unchanged(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.effective_chat.id = 42
    at_the_ceiling = "x" * MAX_RICH_TEXT_LENGTH

    await telegram_api.send_message(update, MitupView(RichContent(at_the_ceiling)))

    assert only_rich_call(bot).html == at_the_ceiling


async def test_button_markup_does_not_count_towards_the_ceiling(telegram_api: TelegramApi, bot: AsyncMock):
    """The cap is on the text a reader sees, so a keyboard costing thousands of characters of
    markup cannot push a short body over it."""
    update = MagicMock(spec=Update)
    update.effective_chat.id = 42
    rows = [[ButtonConfig(text=f"b{index}", callback_data="join")] for index in range(400)]

    await telegram_api.send_message(update, MitupView(RichContent("short body"), rows))

    assert len(only_rich_call(bot).html) > MAX_RICH_TEXT_LENGTH


# ---------------------------------------------------------------------------
# handle_edit_errors
# ---------------------------------------------------------------------------


async def test_handle_edit_errors_ignores_message_not_modified(adapter: BotAdapter):
    async with handle_edit_errors(cast(ContextOrBotAdapter, adapter)):
        raise BadRequest("Message is not modified: specified new message content and reply markup are exactly the same")


@pytest.mark.parametrize(
    "error_message",
    [
        "Message_id_invalid",
        "Message to edit not found",
        "Chat not found",
    ],
    ids=["message_id_invalid", "message_to_edit_not_found", "chat_not_found"],
)
async def test_handle_edit_errors_swallows_message_not_found(adapter: BotAdapter, error_message: str):
    # The DB cleanup for vanished meeting messages lives in update_single_meeting_message /
    # the write-mode reconcile; here the error is only swallowed and recorded.
    with capture_logs() as logs:
        async with handle_edit_errors(cast(ContextOrBotAdapter, adapter)):
            raise BadRequest(error_message)

    gone = next(entry for entry in logs if entry["event"] == "Message to edit is gone")
    assert gone["reason"] == "message_not_found"


async def test_handle_edit_errors_reraises_other_bad_request(adapter: BotAdapter):
    with pytest.raises(BadRequest, match="Something else"):
        async with handle_edit_errors(cast(ContextOrBotAdapter, adapter)):
            raise BadRequest("Something else went wrong")


# ---------------------------------------------------------------------------
# send_message_to_user
# ---------------------------------------------------------------------------


async def test_send_message_to_user_with_mitup_view(telegram_api: TelegramApi, bot: AsyncMock):
    user = create_user(id=1, tg_user_id=456)
    view = MitupView(message=RichContent("Hello world"), menu=[])
    sentinel = MagicMock(spec=Message)
    bot.do_api_request.return_value = sentinel

    result = await telegram_api.send_message_to_user(user, view)

    call = only_rich_call(bot)
    assert call.endpoint == SEND_RICH_MESSAGE_ENDPOINT
    assert call.chat_id == 456
    assert call.html == "Hello world"
    assert result is sentinel


async def test_send_message_to_user_with_plain_string(telegram_api: TelegramApi, bot: AsyncMock):
    user = create_user(id=1, tg_user_id=789)
    sentinel = MagicMock(spec=Message)
    bot.do_api_request.return_value = sentinel

    result = await telegram_api.send_message_to_user(user, "plain text")

    call = only_rich_call(bot)
    assert call.api_kwargs == {"chat_id": 789, "rich_message": {"html": "plain text", "skip_entity_detection": True}}
    assert result is sentinel


@pytest.mark.parametrize(
    "side_effect, tg_user_id",
    [
        (Forbidden("Forbidden: bot was blocked by the user"), 111),
        (BadRequest("Chat not found"), 222),
    ],
    ids=["forbidden", "bad_request_not_found"],
)
async def test_send_message_to_user_raises_inactive_user(
    telegram_api: TelegramApi, bot: AsyncMock, side_effect: Exception, tg_user_id: int
):
    user = create_user(id=1, tg_user_id=tg_user_id)
    bot.do_api_request.side_effect = side_effect

    with pytest.raises(InactiveUserInteraction) as exc_info:
        await telegram_api.send_message_to_user(user, "test")

    assert exc_info.value.tg_user_id == tg_user_id


async def test_send_message_to_user_other_bad_request_reraised(telegram_api: TelegramApi, bot: AsyncMock):
    user = create_user(id=1, tg_user_id=333)
    bot.do_api_request.side_effect = BadRequest("Something else")

    with pytest.raises(BadRequest, match="Something else"):
        await telegram_api.send_message_to_user(user, "test")


# ---------------------------------------------------------------------------
# send_messages_to_users
# ---------------------------------------------------------------------------


async def test_send_messages_to_users_mismatched_raises_value_error(telegram_api: TelegramApi):
    users = [create_user(id=1), create_user(id=2, tg_user_id=456)]
    views: list[MitupView | str] = ["msg1"]

    with pytest.raises(ValueError, match="number of users and views must be the same"):
        await telegram_api.send_messages_to_users(users, views)


async def test_send_messages_to_users_on_success_called_for_each(telegram_api: TelegramApi, bot: AsyncMock):
    user1 = create_user(id=1, tg_user_id=100)
    user2 = create_user(id=2, tg_user_id=200)
    on_success_1 = MagicMock()
    on_success_2 = MagicMock()
    bot.do_api_request.return_value = MagicMock(spec=Message)

    await telegram_api.send_messages_to_users([user1, user2], ["msg1", "msg2"], on_success=[on_success_1, on_success_2])

    on_success_1.assert_called_once_with(user1)
    on_success_2.assert_called_once_with(user2)


async def test_send_messages_to_users_inactive_user_marked_inactive(
    telegram_api: TelegramApi, bot: AsyncMock, api_metrics: MetricAssertions, api_metrics_client: MetricsClient
):
    """MEMBER user blocking the bot must transition to LEFT and have the departure recorded."""
    user1 = create_user(id=1, tg_user_id=100)
    user2 = create_user(id=2, tg_user_id=200)
    bot.do_api_request.side_effect = [
        Forbidden("Forbidden: bot was blocked by the user"),
        MagicMock(spec=Message),
    ]

    with capture_logs() as logs:
        await telegram_api.send_messages_to_users([user1, user2], ["msg1", "msg2"])
    await api_metrics_client.flush()

    assert user1.status is UserStatus.LEFT
    assert user2.status is UserStatus.MEMBER
    # Only the MEMBER → LEFT transition is recorded as a departure.
    changed = [entry for entry in logs if entry["event"] == "User status changed"]
    assert len(changed) == 1
    assert changed[0]["reason"] == "fanout_unreachable"


async def test_send_messages_to_users_joined_only_user_not_transitioned(
    telegram_api: TelegramApi, bot: AsyncMock, api_metrics: MetricAssertions, api_metrics_client: MetricsClient
):
    """A JOINED_ONLY user that Forbidden-fails must NOT transition and must NOT emit the metric.

    In practice the query filters prevent this from happening in the notify CLIs, but
    `send_messages_to_users` is general-purpose. The `mark_inactive` no-op gate is the
    only thing keeping the INACTIVE_USER_SET counter honest if a JOINED_ONLY ever sneaks
    through.
    """
    joined_only_user = create_user(id=1, tg_user_id=100, status=UserStatus.JOINED_ONLY)
    bot.do_api_request.side_effect = Forbidden("Forbidden: bot was blocked by the user")

    await telegram_api.send_messages_to_users([joined_only_user], ["msg1"])
    await api_metrics_client.flush()

    # Status stays JOINED_ONLY — mark_inactive() returns False.
    assert joined_only_user.status is UserStatus.JOINED_ONLY


async def test_send_messages_to_users_general_error_calls_on_error(telegram_api: TelegramApi, bot: AsyncMock):
    user1 = create_user(id=1, tg_user_id=100)
    on_error_1 = MagicMock()
    bot.do_api_request.side_effect = RuntimeError("network failure")

    await telegram_api.send_messages_to_users([user1], ["msg1"], on_error=[on_error_1])

    on_error_1.assert_called_once()
    call_args = on_error_1.call_args
    assert call_args[0][0] is user1
    assert isinstance(call_args[0][1], RuntimeError)


async def test_send_messages_to_users_general_error_never_reports_success(telegram_api: TelegramApi, bot: AsyncMock):
    """A failed send must not run `on_success`, even when the caller passed no `on_error`."""
    user1 = create_user(id=1, tg_user_id=100)
    on_success_1 = MagicMock()
    bot.do_api_request.side_effect = RuntimeError("network failure")

    await telegram_api.send_messages_to_users([user1], ["msg1"], on_success=[on_success_1])

    on_success_1.assert_not_called()


async def test_send_messages_to_users_unreachable_user_calls_on_unreachable(telegram_api: TelegramApi, bot: AsyncMock):
    """A blocked recipient is its own outcome: neither a success nor an error for the caller."""
    user1 = create_user(id=1, tg_user_id=100)
    user2 = create_user(id=2, tg_user_id=200)
    callbacks = {name: [MagicMock(), MagicMock()] for name in ("on_success", "on_error", "on_unreachable")}
    bot.do_api_request.side_effect = [Forbidden("Forbidden: bot was blocked by the user"), MagicMock(spec=Message)]

    await telegram_api.send_messages_to_users([user1, user2], ["msg1", "msg2"], **callbacks)

    callbacks["on_unreachable"][0].assert_called_once_with(user1)
    callbacks["on_success"][0].assert_not_called()
    callbacks["on_error"][0].assert_not_called()
    # The batch keeps going: the reachable recipient still resolves as a success.
    callbacks["on_success"][1].assert_called_once_with(user2)
    callbacks["on_unreachable"][1].assert_not_called()


# ---------------------------------------------------------------------------
# notify_users_promoted_from_waiting_list
# ---------------------------------------------------------------------------


async def test_notify_users_promoted_filters_invited_users(telegram_api: TelegramApi, bot: AsyncMock):
    meeting = create_meetup(id=10, title="Event", language="en")
    create_user(id=1, tg_user_id=100, owned_meetings=[meeting])
    organic_user = create_user(id=2, tg_user_id=200)
    invited_user = create_user(id=3, tg_user_id=300)
    inviter = create_user(id=4, tg_user_id=400)
    organic_link = create_joined_link(organic_user, meeting, id=1)
    invited_link = create_joined_link(invited_user, meeting, id=2, invited_by=inviter)
    bot.do_api_request.return_value = MagicMock(spec=Message)

    await telegram_api.notify_users_promoted_from_waiting_list([organic_link, invited_link], meeting)

    assert bot.do_api_request.await_count == 1
    assert rich_call(bot).chat_id == 200


# ---------------------------------------------------------------------------
# update_single_meeting_message
# ---------------------------------------------------------------------------


def assert_card_is_inert(call: RichCall):
    """Assert the rendered card offers nothing to press.

    A state card (deleted, finished) reports what happened to a meeting nobody can act on any
    more, so neither the menu nor the body may carry a button: the body is checked too because a
    meeting card renders its controls inside its content.
    """
    assert call.button_rows == []
    assert "<tg-button" not in call.html


def make_bot_chat_meeting(lang: str = "en") -> tuple[Meetup, MessageModel]:
    meeting = create_meetup(id=10, title="Test Meeting", language=lang)
    owner = create_user(id=1, tg_user_id=100, owned_meetings=[meeting])
    msg = create_message(
        id=1,
        inline_message_id=None,
        chat_id=owner.tg_user_id,
        message_id=555,
        meetup_id=10,
    )
    return meeting, msg


def make_inline_meeting() -> tuple[Meetup, MessageModel]:
    meeting = create_meetup(id=10, title="Test Meeting", language="en")
    create_user(id=1, tg_user_id=100, owned_meetings=[meeting])
    msg = create_message(
        id=2,
        inline_message_id="inline_123",
        chat_instance="chat_inst",
        chat_id=None,
        message_id=None,
        meetup_id=10,
    )
    return meeting, msg


async def test_update_single_meeting_message_bot_chat(telegram_api: TelegramApi, bot: AsyncMock):
    meeting, msg = make_bot_chat_meeting()

    await telegram_api.update_single_meeting_message(msg, meeting)

    bot.do_api_request.assert_awaited_once()
    call = rich_call(bot)
    assert call.chat_id == msg.chat_id
    assert call.message_id == msg.message_id
    assert call.button_rows
    # The rendered keyboard is persisted onto the row via MutableModel change tracking with
    # the caller's surrounding transaction.
    assert msg.buttons.keyboard


async def test_update_single_meeting_message_inline(telegram_api: TelegramApi, bot: AsyncMock):
    meeting, msg = make_inline_meeting()

    await telegram_api.update_single_meeting_message(msg, meeting)

    bot.do_api_request.assert_awaited_once()
    call = rich_call(bot)
    assert call.inline_message_id == "inline_123"
    # An inline-addressed card carries its buttons as a classic keyboard so chosen_inline_result
    # keeps delivering inline_message_id (see classic_markup); the body holds no button rows.
    assert not call.button_rows
    assert call.reply_markup is not None
    assert call.reply_markup["inline_keyboard"]


@pytest.mark.parametrize(
    "was_deleted, has_finished, expected_state_line",
    [
        (True, False, MeetingDisplayMessages.DELETED_BANNER),
        (False, True, MeetingDisplayMessages.FINISHED_STATUS),
    ],
    ids=["was_deleted", "has_finished"],
)
async def test_update_single_meeting_message_state_flags(
    telegram_api: TelegramApi,
    bot: AsyncMock,
    was_deleted: bool,
    has_finished: bool,
    expected_state_line: MeetingDisplayMessages,
    lang: str,
):
    meeting, msg = make_bot_chat_meeting(lang=lang)

    await telegram_api.update_single_meeting_message(msg, meeting, was_deleted=was_deleted, has_finished=has_finished)

    call = rich_call(bot)
    assert_card_is_inert(call)
    assert expected_state_line.rich(lang=lang).html in call.body_html


async def test_a_finished_card_is_the_meeting_card_itself_with_nothing_over_it(
    telegram_api: TelegramApi, bot: AsyncMock, lang: str
):
    """The finish is reported by the status line inside the card, so no banner stands between the
    reader and the title of the meeting they came for."""
    meeting, msg = make_bot_chat_meeting(lang=lang)

    await telegram_api.update_single_meeting_message(msg, meeting, has_finished=True)

    assert rich_call(bot).body_html == meeting_views.shared_body(meeting, finished=True).html


async def test_update_single_meeting_message_inline_vs_bot_chat_different_views(
    telegram_api: TelegramApi, bot: AsyncMock
):
    """The owner's own card is the editor, every chip on it; the shared one closes on the link back
    into the bot and offers nothing to edit."""
    meeting_bot, msg_bot = make_bot_chat_meeting()
    meeting_inline, msg_inline = make_inline_meeting()

    await telegram_api.update_single_meeting_message(msg_bot, meeting_bot)
    bot_chat_html = rich_call(bot).html

    bot.reset_mock()

    await telegram_api.update_single_meeting_message(msg_inline, meeting_inline)
    inline_html = rich_call(bot).html

    assert str(cb.EDIT_MEETING_TITLE.with_id(meeting_bot.db_id)) in bot_chat_html
    assert str(cb.EDIT_MEETING_TITLE.with_id(meeting_inline.db_id)) not in inline_html
    assert f"?start={shared_card.SHARED_CHAT_SOURCE}" in inline_html
    assert "?start=" not in bot_chat_html


async def test_update_single_meeting_message_not_modified_is_swallowed(telegram_api: TelegramApi, bot: AsyncMock):
    meeting, msg = make_bot_chat_meeting()
    bot.do_api_request.side_effect = BadRequest("Message is not modified: ...")

    # Verifies the wiring: update_single_meeting_message routes errors through handle_edit_errors
    await telegram_api.update_single_meeting_message(msg, meeting)


async def test_update_single_meeting_message_not_found_is_swallowed_in_immediate_mode(
    telegram_api: TelegramApi, bot: AsyncMock
):
    meeting, msg = make_bot_chat_meeting()
    bot.do_api_request.side_effect = BadRequest("Message to edit not found")

    # No DB cleanup happens here: the dead row is recorded and deleted only by the write
    # lifecycle's reconcile (see test_execute_queued_records_dead_message_for_reconcile).
    await telegram_api.update_single_meeting_message(msg, meeting)


@pytest.mark.parametrize(
    "error_message",
    ["Forbidden: user is deactivated", "Forbidden: bot was blocked by the user"],
)
async def test_update_single_meeting_message_forbidden_is_swallowed_in_immediate_mode(
    telegram_api: TelegramApi, bot: AsyncMock, error_message: str
):
    meeting, msg = make_bot_chat_meeting()
    bot.do_api_request.side_effect = Forbidden(error_message)

    # An unreachable chat gets the same dead-message treatment as a deleted message.
    await telegram_api.update_single_meeting_message(msg, meeting)


# ---------------------------------------------------------------------------
# update_meeting_messages
# ---------------------------------------------------------------------------


async def test_update_meeting_messages_current_updated_first_then_others(telegram_api: TelegramApi, bot: AsyncMock):
    meeting = create_meetup(id=10, title="Meeting", language="en")
    create_user(id=1, tg_user_id=100, owned_meetings=[meeting])
    current_msg = create_message(id=1, inline_message_id=None, chat_id=100, message_id=501, meetup_id=10)
    other_msg = create_message(
        id=2, inline_message_id="inline_other", chat_instance="ci", chat_id=None, message_id=None, meetup_id=10
    )
    meeting.messages = [other_msg]

    await telegram_api.update_meeting_messages(
        meeting=meeting,
        current_message=current_msg,
    )

    assert bot.do_api_request.await_count == 2


async def test_update_meeting_messages_skip_current(telegram_api: TelegramApi, bot: AsyncMock):
    meeting = create_meetup(id=10, title="Meeting", language="en")
    create_user(id=1, tg_user_id=100, owned_meetings=[meeting])
    current_msg = create_message(id=1, inline_message_id=None, chat_id=100, message_id=501, meetup_id=10)
    other_msg = create_message(
        id=2, inline_message_id="inline_other", chat_instance="ci", chat_id=None, message_id=None, meetup_id=10
    )
    meeting.messages = [current_msg, other_msg]

    await telegram_api.update_meeting_messages(
        meeting=meeting,
        current_message=current_msg,
        skip_current=True,
    )

    assert bot.do_api_request.await_count == 1


async def test_update_meeting_messages_no_current_message(telegram_api: TelegramApi, bot: AsyncMock):
    meeting = create_meetup(id=10, title="Meeting", language="en")
    create_user(id=1, tg_user_id=100, owned_meetings=[meeting])
    msg1 = create_message(
        id=1, inline_message_id="inline_1", chat_instance="ci", chat_id=None, message_id=None, meetup_id=10
    )
    msg2 = create_message(
        id=2, inline_message_id="inline_2", chat_instance="ci2", chat_id=None, message_id=None, meetup_id=10
    )
    meeting.messages = [msg1, msg2]

    await telegram_api.update_meeting_messages(
        meeting=meeting,
    )

    assert bot.do_api_request.await_count == 2


@pytest.mark.parametrize(
    "was_deleted, has_finished, expected_state_line",
    [
        (True, False, MeetingDisplayMessages.DELETED_BANNER),
        (False, True, MeetingDisplayMessages.FINISHED_STATUS),
    ],
    ids=["was_deleted", "has_finished"],
)
async def test_update_meeting_messages_state_flag_propagated(
    telegram_api: TelegramApi,
    bot: AsyncMock,
    was_deleted: bool,
    has_finished: bool,
    expected_state_line: MeetingDisplayMessages,
    lang: str,
):
    meeting = create_meetup(id=10, title="Meeting", language=lang)
    create_user(id=1, tg_user_id=100, owned_meetings=[meeting])
    msg = create_message(id=1, inline_message_id=None, chat_id=100, message_id=501, meetup_id=10)
    meeting.messages = [msg]

    await telegram_api.update_meeting_messages(
        meeting=meeting,
        current_message=msg,
        was_deleted=was_deleted,
        has_finished=has_finished,
    )

    call = rich_call(bot)
    assert_card_is_inert(call)
    assert expected_state_line.rich(lang=lang).html in call.body_html


# ---------------------------------------------------------------------------
# BotAdapter.flush_metrics
# ---------------------------------------------------------------------------


async def test_bot_adapter_flush_metrics(bot: AsyncMock):

    adapter = BotAdapter(bot=bot, metrics=make_test_metrics_client())
    await adapter.flush_metrics()


# ---------------------------------------------------------------------------
# edit_message
# ---------------------------------------------------------------------------


async def test_edit_message_with_effective_message(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.effective_message.chat.id = 123
    update.effective_message.id = 456
    sentinel = MagicMock(spec=Message)
    bot.do_api_request.return_value = sentinel

    result = await telegram_api.edit_message(update, "hello")

    call = only_rich_call(bot)
    assert call.endpoint == EDIT_MESSAGE_TEXT_ENDPOINT
    assert call.api_kwargs == {
        "chat_id": 123,
        "message_id": 456,
        "rich_message": {"html": "hello", "skip_entity_detection": True},
    }
    assert result is sentinel


async def test_edit_message_with_inline_message_id(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.effective_message = None
    update.callback_query.inline_message_id = "inline_999"
    sentinel = MagicMock()
    bot.do_api_request.return_value = sentinel

    result = await telegram_api.edit_message(update, "hello inline")

    call = only_rich_call(bot)
    assert call.api_kwargs == {
        "inline_message_id": "inline_999",
        "rich_message": {"html": "hello inline", "skip_entity_detection": True},
    }
    assert result is sentinel


async def test_edit_message_with_mitup_view(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.effective_message.chat.id = 123
    update.effective_message.id = 456
    view = MitupView(message=RichContent("view text"), menu=[])
    bot.do_api_request.return_value = MagicMock(spec=Message)

    await telegram_api.edit_message(update, view)

    call = rich_call(bot)
    assert call.body_html == "view text"
    assert call.button_rows == []


async def test_edit_message_raises_no_message_available(telegram_api: TelegramApi):
    update = MagicMock(spec=Update)
    update.effective_message = None
    update.callback_query = None

    with pytest.raises(NoMessageAvailable):
        await telegram_api.edit_message(update, "text")


# ---------------------------------------------------------------------------
# answer_inline_query
# ---------------------------------------------------------------------------


def make_inline_view(keyboard: Keyboard | None = None) -> MitupInlineView:
    return MitupInlineView(
        message=RichContent("desc"), title="Title", inline_description="short", id="1", menu=keyboard or []
    )


async def test_answer_inline_query_with_button(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    bot.do_api_request.return_value = True
    button = InlineResultsButton(text="Go", start_parameter="start")

    await telegram_api.answer_inline_query(update, [make_inline_view()], button=button)

    call = only_rich_call(bot)
    assert call.endpoint == ANSWER_INLINE_QUERY_ENDPOINT
    api_kwargs = call.api_kwargs
    assert api_kwargs["button"] == {"text": "Go", "start_parameter": "start"}
    assert api_kwargs["cache_time"] == 60


async def test_answer_inline_query_without_a_button_names_none(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    bot.do_api_request.return_value = True

    await telegram_api.answer_inline_query(update, [make_inline_view()], cache_time=0)

    api_kwargs = only_rich_call(bot).api_kwargs
    assert "button" not in api_kwargs
    assert api_kwargs["cache_time"] == 0


async def test_an_inline_result_carries_a_classic_keyboard_beside_its_rich_content(
    telegram_api: TelegramApi, bot: AsyncMock
):
    """The picked result sends a rich body with its buttons as a classic keyboard on the result:
    chosen_inline_result only delivers inline_message_id for a classic keyboard (see
    classic_markup), and without that id the shared card can never be claimed."""
    update = MagicMock(spec=Update)
    bot.do_api_request.return_value = True
    view = make_inline_view(keyboard=[[ButtonConfig(text="Join", callback_data="join")]])

    await telegram_api.answer_inline_query(update, [view])

    api_kwargs = only_rich_call(bot).api_kwargs
    assert api_kwargs["results"] == [
        {
            "type": "article",
            "id": "1",
            "title": "Title",
            "description": "short",
            "input_message_content": {
                "rich_message": {
                    "html": "desc",
                    "skip_entity_detection": True,
                }
            },
            "reply_markup": {"inline_keyboard": [[{"text": "Join", "callback_data": "join"}]]},
        }
    ]


async def test_the_query_id_addresses_the_answer(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.inline_query.id = "query-7"
    bot.do_api_request.return_value = True

    await telegram_api.answer_inline_query(update, [make_inline_view()])

    api_kwargs = only_rich_call(bot).api_kwargs
    assert api_kwargs["inline_query_id"] == "query-7"


async def test_a_shared_card_edits_into_the_same_content_it_was_sent_as(telegram_api: TelegramApi, bot: AsyncMock):
    """The result a user picks and the later edits of the card it became are rendered by one view
    factory through one producer, so a shared card never changes shape once it is edited."""
    meeting, card = make_inline_meeting()
    shared = meeting_views.inline_view(meeting, chat_instance=card.chat_instance)

    await telegram_api.update_single_meeting_message(card, meeting)

    edited_html = only_rich_call(bot).html
    assert edited_html == shared.inline_result()["input_message_content"]["rich_message"]["html"]


async def test_answer_inline_query_raises_on_api_failure(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    bot.do_api_request.return_value = False

    with pytest.raises(AnswerInlineQueryError):
        await telegram_api.answer_inline_query(update, [])


# ---------------------------------------------------------------------------
# answer_callback_query
# ---------------------------------------------------------------------------


async def test_answer_callback_query(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    text = "short text"

    await telegram_api.answer_callback_query(update, text, show_alert=False)

    bot.answer_callback_query.assert_awaited_once_with(update.callback_query.id, text=text, show_alert=False)


async def test_answer_callback_query_accepts_text_at_the_limit(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    text = "x" * CALLBACK_QUERY_TEXT_LIMIT

    await telegram_api.answer_callback_query(update, text, show_alert=False)

    bot.answer_callback_query.assert_awaited_once_with(update.callback_query.id, text=text, show_alert=False)


async def test_answer_callback_query_rejects_text_over_telegram_limit(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)

    with pytest.raises(CallbackQueryTextTooLong):
        await telegram_api.answer_callback_query(update, "x" * (CALLBACK_QUERY_TEXT_LIMIT + 1), show_alert=False)

    bot.answer_callback_query.assert_not_awaited()


async def test_answer_callback_query_raises_when_formatted_text_has_entities(
    telegram_api: TelegramApi,
):
    from telegram import MessageEntity

    update = MagicMock(spec=Update)
    # FormattedText with at least one entity — should trigger the ValueError guard
    ft = FormattedText("hello", [MessageEntity(type="bold", offset=0, length=5)])

    with pytest.raises(ValueError, match="Callback query text should not contain entities"):
        await telegram_api.answer_callback_query(update, ft, show_alert=False)


# ---------------------------------------------------------------------------
# Outbox capture: begin_capture / immediate / validation at enqueue
# ---------------------------------------------------------------------------


async def test_capture_defers_calls_and_drains_in_enqueue_order(telegram_api: TelegramApi, bot: AsyncMock):
    user1 = create_user(id=1, tg_user_id=100)
    user2 = create_user(id=2, tg_user_id=200)
    bot.do_api_request.return_value = MagicMock(spec=Message)

    outbox = telegram_api.begin_capture()
    assert await telegram_api.send_message_to_user(user1, "first") is None
    assert await telegram_api.send_message_to_user(user2, "second") is None
    telegram_api.end_capture()

    # Nothing reached the bot while the queue was being built (i.e. inside the transaction).
    bot.do_api_request.assert_not_called()

    await telegram_api.execute_queued(outbox)

    assert [call.chat_id for call in rich_calls(bot)] == [100, 200]
    assert [call.html for call in rich_calls(bot)] == ["first", "second"]


def test_nested_begin_capture_raises(telegram_api: TelegramApi):
    telegram_api.begin_capture()

    with pytest.raises(RuntimeError, match="cannot nest"):
        telegram_api.begin_capture()


async def test_immediate_bypasses_capture_and_restores_it(telegram_api: TelegramApi, bot: AsyncMock):
    user = create_user(id=1, tg_user_id=100)
    bot.do_api_request.return_value = MagicMock(spec=Message)

    outbox = telegram_api.begin_capture()
    await telegram_api.immediate.send_message_to_user(user, "in-transaction")

    # The lifted call executed right away and did not land on the queue...
    bot.do_api_request.assert_awaited_once()
    assert outbox.calls == []

    # ...and capture mode is restored afterwards: the next call enqueues again.
    await telegram_api.send_message_to_user(user, "queued")
    bot.do_api_request.assert_awaited_once()
    assert len(outbox.calls) == 1


async def test_update_meeting_messages_current_first_order_survives_capture(telegram_api: TelegramApi, bot: AsyncMock):
    """No queue is in service here, so the whole fan-out drains in the order it was captured in."""
    meeting = create_meetup(id=10, title="Meeting", language="en")
    create_user(id=1, tg_user_id=100, owned_meetings=[meeting])
    current_msg = create_message(id=1, inline_message_id=None, chat_id=100, message_id=501, meetup_id=10)
    other_msg = create_message(
        id=2, inline_message_id="inline_other", chat_instance="ci", chat_id=None, message_id=None, meetup_id=10
    )
    # Storage order deliberately puts the current message last.
    meeting.messages = [other_msg, current_msg]

    outbox = telegram_api.begin_capture()
    await telegram_api.update_meeting_messages(meeting=meeting, current_message=current_msg)
    telegram_api.end_capture()
    bot.do_api_request.assert_not_called()

    await telegram_api.execute_queued(outbox)

    first, second = rich_calls(bot)
    assert first.message_id == 501  # the current message is still edited first
    assert second.inline_message_id == "inline_other"


async def test_capture_snapshots_rendered_payload_at_enqueue(telegram_api: TelegramApi, bot: AsyncMock):
    meeting, msg = make_bot_chat_meeting()  # title "Test Meeting"

    outbox = telegram_api.begin_capture()
    await telegram_api.update_meeting_messages(meeting=meeting, current_message=msg)
    # The handler keeps mutating after the fan-out call — still inside the transaction. The
    # queued payload was rendered at enqueue time and must not pick this up.
    meeting.title = "Renamed after enqueue"
    telegram_api.end_capture()

    await telegram_api.execute_queued(outbox)

    html = rich_call(bot).html
    assert "Test Meeting" in html
    assert "Renamed after enqueue" not in html


async def test_enqueue_validates_edit_target_inside_transaction(telegram_api: TelegramApi):
    update = MagicMock(spec=Update)
    update.effective_message = None
    update.callback_query = None

    outbox = telegram_api.begin_capture()

    # The missing edit target surfaces at enqueue time — inside the transaction, where the
    # global error handler still rolls everything back — not during the post-commit drain.
    with pytest.raises(NoMessageAvailable):
        await telegram_api.edit_message(update, "text")
    assert outbox.calls == []


async def test_enqueue_validates_users_views_mismatch_inside_transaction(telegram_api: TelegramApi):
    users = [create_user(id=1), create_user(id=2, tg_user_id=456)]
    views: list[MitupView | str] = ["only one"]

    outbox = telegram_api.begin_capture()

    with pytest.raises(ValueError, match="number of users and views must be the same"):
        await telegram_api.send_messages_to_users(users, views)
    assert outbox.calls == []


@pytest.mark.parametrize(
    "callback_kwargs",
    [{"on_success": [MagicMock()]}, {"on_error": [MagicMock()]}, {"on_unreachable": [MagicMock()]}],
    ids=["on_success", "on_error", "on_unreachable"],
)
async def test_send_messages_to_users_callbacks_rejected_under_capture(
    telegram_api: TelegramApi, callback_kwargs: dict[str, Any]
):
    user = create_user(id=1, tg_user_id=100)

    outbox = telegram_api.begin_capture()

    # The callbacks would run against live ORM objects after commit and their effects would
    # be lost — callers must opt into pre-commit execution via context.api.immediate.
    with pytest.raises(ValueError, match="use context.api.immediate"):
        await telegram_api.send_messages_to_users([user], ["msg"], **callback_kwargs)
    assert outbox.calls == []


# ---------------------------------------------------------------------------
# execute_queued: per-call isolation during the post-commit drain
# ---------------------------------------------------------------------------


async def test_execute_queued_not_modified_continues_without_fault(
    telegram_api: TelegramApi, api_metrics: MetricAssertions, api_metrics_client: MetricsClient
):
    executed: list[str] = []

    async def not_modified():
        raise BadRequest("Message is not modified: nothing changed")

    async def ok():
        executed.append("second")

    outbox = ApiOutbox(calls=[QueuedApiCall("edit_message", not_modified), QueuedApiCall("send_message", ok)])
    await telegram_api.execute_queued(outbox)
    await api_metrics_client.flush()

    # "Not modified" is a success post-commit: the drain continued and no fault was counted.
    assert executed == ["second"]
    api_metrics.assert_not_emitted(name=MetricKey.FAULT)


@pytest.mark.parametrize(
    "error",
    [RuntimeError("render failed"), BadRequest("Something else went wrong")],
    ids=["runtime_error", "unrecognized_bad_request"],
)
async def test_execute_queued_generic_failure_counts_fault_and_continues(
    telegram_api: TelegramApi,
    api_metrics: MetricAssertions,
    api_metrics_client: MetricsClient,
    error: Exception,
):
    executed: list[str] = []

    async def boom():
        raise error

    async def ok():
        executed.append("second")

    outbox = ApiOutbox(calls=[QueuedApiCall("send_message_to_user", boom), QueuedApiCall("edit_message", ok)])
    await telegram_api.execute_queued(outbox)
    await api_metrics_client.flush()

    # The DB is already committed, so one failed rendering never blocks the other deliveries.
    assert executed == ["second"]
    # A delivery failure counts on its own series and leaves the invocation outcome alone: the
    # aggregate Fault belongs to the handler, which is still on its way to completing normally.
    api_metrics.assert_emitted(name=MetricKey.POST_COMMIT_API_FAULT, value=1)
    api_metrics.assert_not_emitted(name=MetricKey.FAULT)
    api_metrics.assert_not_emitted(name=MetricKey.FAULT.with_prefix(type(error).__name__))


async def test_execute_queued_failure_leaves_the_invocation_fault_a_single_datapoint(
    telegram_api: TelegramApi, api_metrics: MetricAssertions, api_metrics_client: MetricsClient
):
    """EMF appends repeated values under one metric name, so a second writer would serialise
    `Fault: [1, 0]` — an array that the fault alarms average and the triage query stops matching."""

    async def boom():
        raise RuntimeError("render failed")

    await telegram_api.execute_queued(ApiOutbox(calls=[QueuedApiCall("send_message_to_user", boom)]))
    # What the registry emits once the handler completes, after the drain.
    telegram_api.adapter.emit_metric(MetricKey.FAULT, 0)
    await api_metrics_client.flush()

    # One Fault datapoint in the whole record, and it is the completing handler's scalar 0.
    api_metrics.assert_emitted(name=MetricKey.FAULT, times=1)
    api_metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)


@pytest.mark.parametrize(
    "connectivity_error",
    [NetworkError("bridge down"), TimedOut()],
    ids=["network_error", "timed_out"],
)
async def test_execute_queued_network_failure_never_surfaces_to_the_caller(
    telegram_api: TelegramApi,
    api_metrics: MetricAssertions,
    api_metrics_client: MetricsClient,
    connectivity_error: NetworkError,
):
    executed: list[str] = []

    async def boom():
        raise connectivity_error

    async def ok():
        executed.append("second")

    outbox = ApiOutbox(calls=[QueuedApiCall("send_message", boom), QueuedApiCall("send_message", ok)])

    # The transaction committed before the drain started: the user's action succeeded, so a
    # failure to render it is recorded and the next (independent) delivery still goes out.
    await telegram_api.execute_queued(outbox)
    await api_metrics_client.flush()

    assert executed == ["second"]
    api_metrics.assert_emitted(name=MetricKey.POST_COMMIT_API_FAULT)
    api_metrics.assert_not_emitted(name=MetricKey.FAULT)


def with_cause[ErrorT: BaseException](error: ErrorT, cause: Exception) -> ErrorT:
    """PTB raises its network errors ``from`` the underlying httpx exception; that cause is
    where the drain reads whether the request ever left this process."""
    error.__cause__ = cause
    return error


@pytest.mark.parametrize(
    "idempotent, error, expected_attempts",
    [
        (False, with_cause(TimedOut(), httpx.ReadTimeout("read")), 1),
        (True, with_cause(TimedOut(), httpx.ReadTimeout("read")), QUEUED_CALL_ATTEMPTS),
        (False, with_cause(TimedOut(), httpx.ConnectTimeout("connect")), QUEUED_CALL_ATTEMPTS),
        (False, with_cause(NetworkError("httpx.ConnectError"), httpx.ConnectError("refused")), QUEUED_CALL_ATTEMPTS),
        (False, TimedOut(), 1),
        (True, BadRequest("Chat not found"), 1),
    ],
    ids=[
        "send_read_timeout_not_retried",
        "edit_read_timeout_retried",
        "send_connect_timeout_retried",
        "send_connect_error_retried",
        "send_unattributable_timeout_not_retried",
        "bad_request_never_retried",
    ],
)
async def test_execute_queued_retries_only_when_a_repeat_cannot_double_post(
    telegram_api: TelegramApi,
    no_retry_backoff: AsyncMock,
    idempotent: bool,
    error: Exception,
    expected_attempts: int,
):
    """Telegram has no idempotency key: a read timeout leaves it unknown whether the call was
    applied, so only a call that cannot deliver twice — or one that never reached Telegram —
    may be repeated."""
    attempts = 0

    async def failing():
        nonlocal attempts
        attempts += 1
        raise error

    outbox = ApiOutbox(calls=[QueuedApiCall("queued_call", failing, idempotent=idempotent)])
    await telegram_api.execute_queued(outbox)

    assert attempts == expected_attempts


async def test_execute_queued_retry_that_lands_reports_no_failure(
    telegram_api: TelegramApi,
    no_retry_backoff: AsyncMock,
    api_metrics: MetricAssertions,
    api_metrics_client: MetricsClient,
):
    attempts = 0

    async def flaky():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise with_cause(TimedOut(), httpx.ConnectTimeout("connect"))

    outbox = ApiOutbox(calls=[QueuedApiCall("send_message", flaky)])
    await telegram_api.execute_queued(outbox)
    await api_metrics_client.flush()

    assert attempts == 2
    api_metrics.assert_not_emitted(name=MetricKey.FAULT)


async def test_execute_queued_abandons_the_queue_once_telegram_looks_unreachable(
    telegram_api: TelegramApi, no_retry_backoff: AsyncMock
):
    """A dead network would otherwise cost one timeout per remaining delivery, inside a handler
    whose work is already committed."""
    attempted: list[str] = []

    def make_call(name: str, fails: bool) -> QueuedApiCall:
        async def invoke():
            attempted.append(name)
            if fails:
                raise TimedOut()

        return QueuedApiCall(name, invoke)

    outbox = ApiOutbox(
        calls=[make_call(f"call_{index}", fails=True) for index in range(4)] + [make_call("call_4", fails=False)]
    )

    with capture_logs() as logs:
        await telegram_api.execute_queued(outbox)

    assert attempted == ["call_0", "call_1", "call_2"]
    abort_logs = [entry for entry in logs if entry["event"] == "Post-commit drain abandoned: Telegram unreachable"]
    assert abort_logs[0]["abandoned_calls"] == ["call_3", "call_4"]
    finished = next(entry for entry in logs if entry["event"] == "Post-commit drain finished")
    assert (finished["queued"], finished["sent"], finished["failed"], finished["abandoned"]) == (5, 0, 3, 2)


async def test_execute_queued_keeps_draining_while_calls_still_reach_telegram(
    telegram_api: TelegramApi, no_retry_backoff: AsyncMock
):
    """The abort needs *consecutive* network failures: a delivery that lands proves the network
    is alive, so the count starts over."""
    attempted: list[str] = []

    def make_call(name: str, fails: bool) -> QueuedApiCall:
        async def invoke():
            attempted.append(name)
            if fails:
                raise TimedOut()

        return QueuedApiCall(name, invoke)

    outbox = ApiOutbox(
        calls=[
            make_call("first_failure", fails=True),
            make_call("second_failure", fails=True),
            make_call("delivered", fails=False),
            make_call("third_failure", fails=True),
            make_call("fourth_failure", fails=True),
            make_call("last", fails=False),
        ]
    )

    await telegram_api.execute_queued(outbox)

    assert len(attempted) == len(outbox.calls)


async def test_enqueued_calls_declare_whether_a_repeat_can_double_post(telegram_api: TelegramApi):
    """The drain sees opaque closures, so idempotency is declared where the method is known."""
    update = MagicMock(spec=Update)
    update.effective_chat.id = 42
    update.effective_message.chat.id = 42
    meeting, msg = make_bot_chat_meeting()

    outbox = telegram_api.begin_capture()
    await telegram_api.send_message(update, "sent")
    await telegram_api.send_message_to_user(create_user(id=1, tg_user_id=100), "dm")
    await telegram_api.edit_message(update, "edited")
    await telegram_api.answer_callback_query(update, "ack", show_alert=False)
    await telegram_api.update_single_meeting_message(msg, meeting)
    telegram_api.end_capture()

    assert {call.name: call.idempotent for call in outbox.calls} == {
        "send_message": False,
        "send_message_to_user": False,
        "edit_message": True,
        "answer_callback_query": True,
        "update_meeting_message": True,
    }


@pytest.mark.parametrize(
    "side_effect",
    [Forbidden("Forbidden: bot was blocked by the user"), BadRequest("Chat not found")],
    ids=["forbidden", "bad_request_not_found"],
)
async def test_execute_queued_unreachable_user_recorded_for_reconcile(
    telegram_api: TelegramApi,
    bot: AsyncMock,
    api_metrics: MetricAssertions,
    api_metrics_client: MetricsClient,
    side_effect: Exception,
):
    user1 = create_user(id=1, tg_user_id=100)
    user2 = create_user(id=2, tg_user_id=200)

    outbox = telegram_api.begin_capture()
    await telegram_api.send_messages_to_users([user1, user2], ["msg1", "msg2"])
    telegram_api.end_capture()

    bot.do_api_request.side_effect = [side_effect, MagicMock(spec=Message)]
    await telegram_api.execute_queued(outbox)
    await api_metrics_client.flush()

    # Recorded for the decorator's reconcile transaction — NOT transitioned inline, since the
    # handler's session is gone by drain time...
    assert outbox.inactive_tg_user_ids == [100]
    assert user1.status is UserStatus.MEMBER
    # ...and an unreachable user is expected churn, not a fault; the drain continued.
    assert bot.do_api_request.await_count == 2
    api_metrics.assert_not_emitted(name=MetricKey.FAULT)


async def test_execute_queued_records_dead_message_for_reconcile(
    telegram_api: TelegramApi, bot: AsyncMock, api_metrics: MetricAssertions, api_metrics_client: MetricsClient
):
    meeting, msg = make_bot_chat_meeting()

    outbox = telegram_api.begin_capture()
    await telegram_api.update_meeting_messages(meeting=meeting, current_message=msg)
    telegram_api.end_capture()

    bot.do_api_request.side_effect = BadRequest("Message to edit not found")
    with capture_logs() as logs:
        await telegram_api.execute_queued(outbox)
    await api_metrics_client.flush()

    assert outbox.dead_message_ids == [1]  # msg.id — the reconcile transaction deletes the row
    dropped = next(entry for entry in logs if entry["event"] == "Meeting message unreachable, dropping it")
    assert dropped["reason"] == "message_not_found"
    api_metrics.assert_not_emitted(name=MetricKey.FAULT)


async def test_execute_queued_records_dead_message_on_forbidden(
    telegram_api: TelegramApi, bot: AsyncMock, api_metrics: MetricAssertions, api_metrics_client: MetricsClient
):
    meeting, msg = make_bot_chat_meeting()

    outbox = telegram_api.begin_capture()
    await telegram_api.update_meeting_messages(meeting=meeting, current_message=msg)
    telegram_api.end_capture()

    bot.do_api_request.side_effect = Forbidden("Forbidden: user is deactivated")
    with capture_logs() as logs:
        await telegram_api.execute_queued(outbox)
    await api_metrics_client.flush()

    assert outbox.dead_message_ids == [1]  # msg.id — the reconcile transaction deletes the row
    # The other branch that retires a meeting message: the chat itself, not the message, is gone.
    dropped = next(entry for entry in logs if entry["event"] == "Meeting message unreachable, dropping it")
    assert dropped["log_level"] == "warning"
    assert dropped["reason"] == "chat_forbidden"
    api_metrics.assert_not_emitted(name=MetricKey.FAULT)


async def test_execute_queued_message_without_db_id_gets_no_reconcile_entry(telegram_api: TelegramApi, bot: AsyncMock):
    meeting, _ = make_bot_chat_meeting()
    # A row constructed during this update and never flushed: its id is still None.
    msg = MessageModel(inline_message_id=None, chat_id=100, message_id=556, meetup_id=10)

    outbox = telegram_api.begin_capture()
    await telegram_api.update_meeting_messages(meeting=meeting, current_message=msg)
    telegram_api.end_capture()

    bot.do_api_request.side_effect = BadRequest("Message to edit not found")
    await telegram_api.execute_queued(outbox)

    assert outbox.dead_message_ids == []


# ---------------------------------------------------------------------------
# Render digests: skipping the card edits that would change nothing
# ---------------------------------------------------------------------------


JOIN_ROW: Keyboard = [[ButtonConfig(text="Join", callback_data="join")]]


def make_edit(
    content: RichContent | None = None,
    keyboard: Keyboard | None = None,
    photos: tuple[RichPhoto, ...] = (),
) -> MeetingMessageEdit:
    return MeetingMessageEdit(
        message_db_id=1,
        chat_id=100,
        message_id=555,
        inline_message_id=None,
        content=content if content is not None else RichContent("Card body"),
        keyboard=keyboard,
        photos=photos,
    )


def apply_confirmed_digests(outbox: ApiOutbox, *messages: MessageModel):
    """Stand in for the write lifecycle's reconcile transaction, which is what actually writes
    the digests a drain confirmed back onto the rows."""
    for message in messages:
        if message.id in outbox.confirmed_render_digests:
            message.render_digest = outbox.confirmed_render_digests[message.id]


async def test_execute_queued_records_the_confirmed_digest_for_reconcile(telegram_api: TelegramApi, bot: AsyncMock):
    meeting, msg = make_bot_chat_meeting()

    outbox = telegram_api.begin_capture()
    await telegram_api.update_meeting_messages(meeting=meeting, current_message=msg)
    telegram_api.end_capture()
    await telegram_api.execute_queued(outbox)

    # Keyed by msg.id, for the reconcile transaction to stamp onto the row.
    assert list(outbox.confirmed_render_digests) == [1]
    assert re.fullmatch(r"[0-9a-f]{64}", outbox.confirmed_render_digests[1])


async def test_execute_queued_records_the_digest_when_telegram_answers_not_modified(
    telegram_api: TelegramApi, bot: AsyncMock
):
    meeting, msg = make_bot_chat_meeting()
    bot.do_api_request.side_effect = BadRequest("Message is not modified: nothing changed")

    outbox = telegram_api.begin_capture()
    await telegram_api.update_meeting_messages(meeting=meeting, current_message=msg)
    telegram_api.end_capture()
    await telegram_api.execute_queued(outbox)

    # The content is on Telegram either way, so the swallowed 400 confirms the render.
    assert list(outbox.confirmed_render_digests) == [1]


@pytest.mark.parametrize(
    "failure",
    [BadRequest("Bad Request: something else"), NetworkError("connection reset")],
    ids=["bad_request", "network_error"],
)
async def test_execute_queued_records_no_digest_when_the_edit_fails(
    telegram_api: TelegramApi, bot: AsyncMock, no_retry_backoff: AsyncMock, failure: Exception
):
    meeting, msg = make_bot_chat_meeting()
    bot.do_api_request.side_effect = failure

    outbox = telegram_api.begin_capture()
    await telegram_api.update_meeting_messages(meeting=meeting, current_message=msg)
    telegram_api.end_capture()
    await telegram_api.execute_queued(outbox)

    assert outbox.confirmed_render_digests == {}


async def test_execute_queued_records_no_digest_for_a_dead_message(telegram_api: TelegramApi, bot: AsyncMock):
    meeting, msg = make_bot_chat_meeting()
    bot.do_api_request.side_effect = BadRequest("Message to edit not found")

    outbox = telegram_api.begin_capture()
    await telegram_api.update_meeting_messages(meeting=meeting, current_message=msg)
    telegram_api.end_capture()
    await telegram_api.execute_queued(outbox)

    # The row is on its way out; there is nothing left to stamp a digest onto.
    assert outbox.dead_message_ids == [1]
    assert outbox.confirmed_render_digests == {}


async def test_capture_queues_the_edit_while_no_digest_has_been_confirmed(telegram_api: TelegramApi, bot: AsyncMock):
    meeting, msg = make_bot_chat_meeting()
    assert msg.render_digest is None

    outbox = telegram_api.begin_capture()
    await telegram_api.update_meeting_messages(meeting=meeting, current_message=msg)
    telegram_api.end_capture()

    assert len(outbox.calls) == 1


async def test_capture_skips_the_card_whose_confirmed_digest_still_matches(telegram_api: TelegramApi, bot: AsyncMock):
    meeting, msg = make_bot_chat_meeting()

    first = telegram_api.begin_capture()
    with capture_logs() as first_logs:
        await telegram_api.update_meeting_messages(meeting=meeting, current_message=msg)
    telegram_api.end_capture()
    await telegram_api.execute_queued(first)
    apply_confirmed_digests(first, msg)
    bot.do_api_request.reset_mock()

    # A card whose edit is queued is narrated by the call itself, never by a skip line.
    assert not [entry for entry in first_logs if entry["event"] == "Meeting card edit skipped"]

    second = telegram_api.begin_capture()
    with capture_logs() as second_logs:
        await telegram_api.update_meeting_messages(meeting=meeting, current_message=msg)
    telegram_api.end_capture()
    await telegram_api.execute_queued(second)

    # Nothing about the card changed, so the refresh costs no Telegram round trip at all.
    assert second.calls == []
    bot.do_api_request.assert_not_called()

    # The skipped card no longer appears as a Telegram API call line, so the skip line is the
    # only record of the decision.
    skipped = next(entry for entry in second_logs if entry["event"] == "Meeting card edit skipped")
    assert skipped["log_level"] == "info"
    assert skipped["reason"] == "digest_unchanged"
    assert skipped["message_db_id"] == msg.id


async def test_capture_queues_the_edit_again_once_the_card_changes(telegram_api: TelegramApi, bot: AsyncMock):
    meeting, msg = make_bot_chat_meeting()

    first = telegram_api.begin_capture()
    await telegram_api.update_meeting_messages(meeting=meeting, current_message=msg)
    telegram_api.end_capture()
    await telegram_api.execute_queued(first)
    apply_confirmed_digests(first, msg)

    meeting.title = "Renamed meeting"
    second = telegram_api.begin_capture()
    await telegram_api.update_meeting_messages(meeting=meeting, current_message=msg)
    telegram_api.end_capture()

    assert len(second.calls) == 1


async def test_failed_edit_leaves_the_card_to_be_repaired_by_the_next_refresh(
    telegram_api: TelegramApi, bot: AsyncMock, no_retry_backoff: AsyncMock
):
    meeting, msg = make_bot_chat_meeting()
    bot.do_api_request.side_effect = BadRequest("Bad Request: something else")

    failing = telegram_api.begin_capture()
    await telegram_api.update_meeting_messages(meeting=meeting, current_message=msg)
    telegram_api.end_capture()
    await telegram_api.execute_queued(failing)
    apply_confirmed_digests(failing, msg)

    # The stored digest still describes what is on Telegram, so the unchanged card is edited
    # again rather than left stale until something else happens to it.
    assert msg.render_digest is None
    bot.do_api_request.side_effect = None
    repair = telegram_api.begin_capture()
    await telegram_api.update_meeting_messages(meeting=meeting, current_message=msg)
    telegram_api.end_capture()
    await telegram_api.execute_queued(repair)

    bot.do_api_request.assert_awaited()
    assert list(repair.confirmed_render_digests) == [1]


async def test_immediate_mode_edits_a_card_whose_digest_matches(telegram_api: TelegramApi, bot: AsyncMock):
    meeting, msg = make_bot_chat_meeting()

    outbox = telegram_api.begin_capture()
    await telegram_api.update_meeting_messages(meeting=meeting, current_message=msg)
    telegram_api.end_capture()
    await telegram_api.execute_queued(outbox)
    apply_confirmed_digests(outbox, msg)
    bot.do_api_request.reset_mock()

    # Outside the outbox there is no drain to confirm a delivery, so no digest is consulted.
    await telegram_api.update_single_meeting_message(msg, meeting)

    bot.do_api_request.assert_awaited_once()


async def test_capture_of_a_card_series_reaches_telegram_only_for_the_ones_that_drifted(
    telegram_api: TelegramApi, bot: AsyncMock
):
    """The attach flow walks its same-chat cards through `update_single_meeting_message` one by one
    rather than refreshing the meeting, so the per-card digest is what keeps an already-current
    card off the wire."""
    meeting = create_meetup(id=10, title="Test Meeting", language="en")
    create_user(id=1, tg_user_id=100, owned_meetings=[meeting])
    up_to_date = create_message(id=1, inline_message_id="up_to_date", chat_instance="chat_c", meetup_id=10)
    never_confirmed = create_message(id=2, inline_message_id="never_confirmed", chat_instance="chat_c", meetup_id=10)
    meeting.messages.extend((up_to_date, never_confirmed))

    settled = telegram_api.begin_capture()
    await telegram_api.update_single_meeting_message(up_to_date, meeting)
    telegram_api.end_capture()
    await telegram_api.execute_queued(settled)
    apply_confirmed_digests(settled, up_to_date)
    bot.do_api_request.reset_mock()

    attach = telegram_api.begin_capture()
    for card in (up_to_date, never_confirmed):
        await telegram_api.update_single_meeting_message(card, meeting)
    telegram_api.end_capture()
    await telegram_api.execute_queued(attach)

    edited = [call.inline_message_id for call in rich_calls(bot)]
    assert edited == ["never_confirmed"]


# ---------------------------------------------------------------------------
# Deferring the fan-out to the card-refresh queue
# ---------------------------------------------------------------------------

ORIGIN_UPDATE_ID = 4242


@pytest.fixture
def deferring_queue(telegram_api: TelegramApi, api_metrics_client: MetricsClient) -> RefreshQueue:
    """A queue attached to the api under test, the way `MitupContext.from_update` attaches the
    process's queue to the api it builds for a handler. The queue drains through an api of its
    own, which is what a worker in service has."""
    queue = RefreshQueue(TelegramApi(), api_metrics_client)
    telegram_api.refresh_queue = queue
    return queue


def make_shared_meeting() -> tuple[Meetup, MessageModel, MessageModel]:
    """A meeting the owner has open, also tracked by a card shared into another chat."""
    meeting = create_meetup(id=10, title="Test Meeting", language="en")
    create_user(id=1, tg_user_id=100, owned_meetings=[meeting])
    current = create_message(id=1, inline_message_id=None, chat_id=100, message_id=501, meetup_id=10)
    shared = create_message(
        id=2, inline_message_id="inline_other", chat_instance="ci", chat_id=None, message_id=None, meetup_id=10
    )
    meeting.messages = [current, shared]
    return meeting, current, shared


async def run_fanout(api: TelegramApi, meeting: Meetup, **kwargs: Any) -> ApiOutbox:
    """One write-mode fan-out: capture, then the post-commit drain the write lifecycle runs."""
    outbox = api.begin_capture()
    await api.update_meeting_messages(meeting=meeting, **kwargs)
    api.end_capture()
    await api.execute_queued(outbox)
    return outbox


async def test_the_fanout_defers_every_card_but_the_current_one(
    telegram_api: TelegramApi, bot: AsyncMock, deferring_queue: RefreshQueue
):
    """The user gets the card in front of them on the invocation's own timeline; the rest become
    one job, naming the card already drawn so the worker leaves it alone."""
    meeting, current, _ = make_shared_meeting()

    with bound_contextvars(update_id=ORIGIN_UPDATE_ID):
        outbox = await run_fanout(telegram_api, meeting, current_message=current)

    bot.do_api_request.assert_awaited_once()
    assert rich_call(bot).message_id == 501
    assert len(outbox.calls) == 1
    assert deferring_queue.pending[meeting_job_key(10)] == MeetingRefresh(
        meeting_id=10, skip_message_db_id=1, origin_update_id=ORIGIN_UPDATE_ID
    )


async def test_a_fanout_with_no_current_card_defers_all_of_them(
    telegram_api: TelegramApi, bot: AsyncMock, deferring_queue: RefreshQueue
):
    """Where the caller renders no card at all its feedback is the reply it sends, so the whole
    fan-out goes to the worker and the job may pass over nothing."""
    meeting, _, _ = make_shared_meeting()

    outbox = await run_fanout(telegram_api, meeting)

    bot.do_api_request.assert_not_called()
    assert outbox.calls == []
    assert deferring_queue.pending[meeting_job_key(10)] == MeetingRefresh(meeting_id=10, skip_message_db_id=None)


@pytest.mark.parametrize(
    "state_flag",
    ["was_deleted", "has_finished"],
)
async def test_a_terminal_fanout_draws_every_card_itself(
    telegram_api: TelegramApi, bot: AsyncMock, deferring_queue: RefreshQueue, state_flag: str
):
    """A deletion and a finish render rows that die in the same transaction, so a job reading the
    meeting afterwards would find nothing to draw and the card would never get its banner."""
    meeting, current, _ = make_shared_meeting()

    outbox = await run_fanout(telegram_api, meeting, current_message=current, **{state_flag: True})

    assert len(outbox.calls) == 2
    assert bot.do_api_request.await_count == 2
    assert deferring_queue.pending == {}


async def test_the_fanout_draws_every_card_where_the_queue_takes_no_deferrals(
    telegram_api: TelegramApi, bot: AsyncMock, deferring_queue: RefreshQueue
):
    """The deployed switch is off: every card is back on the invocation's own timeline."""
    deferring_queue.accepts_fanout = False
    meeting, current, _ = make_shared_meeting()

    outbox = await run_fanout(telegram_api, meeting, current_message=current)

    assert len(outbox.calls) == 2
    assert bot.do_api_request.await_count == 2
    assert deferring_queue.pending == {}


async def test_the_fanout_draws_every_card_where_no_queue_is_attached(telegram_api: TelegramApi, bot: AsyncMock):
    """A CLI job and a recurrent event build their api through `build_api`, which attaches none:
    nothing would ever pick a deferral up."""
    assert telegram_api.refresh_queue is None
    meeting, current, _ = make_shared_meeting()

    outbox = await run_fanout(telegram_api, meeting, current_message=current)

    assert len(outbox.calls) == 2
    assert bot.do_api_request.await_count == 2


# --- Scoping a fan-out to the cards the caller's change can reach ---


def make_meeting_with_two_shared_cards() -> tuple[Meetup, MessageModel, MessageModel, MessageModel]:
    """The owner's card and two shared ones, so a scope can name one and leave the other out."""
    meeting, current, shared = make_shared_meeting()
    other = create_message(
        id=3, inline_message_id="inline_third", chat_instance="ci_other", chat_id=None, message_id=None, meetup_id=10
    )
    meeting.messages.append(other)
    return meeting, current, shared, other


async def test_a_scoped_fanout_defers_one_job_naming_the_cards_it_covers(
    telegram_api: TelegramApi, bot: AsyncMock, deferring_queue: RefreshQueue
):
    """A caller that knows which cards its change can reach hands the worker that set, and the job
    carries it so the render at execution time leaves the rest of the meeting alone."""
    meeting, current, shared, _other = make_meeting_with_two_shared_cards()

    await run_fanout(telegram_api, meeting, current_message=current, only_message_db_ids={shared.id})

    assert deferring_queue.pending[meeting_job_key(10)] == MeetingRefresh(
        meeting_id=10, skip_message_db_id=1, message_db_ids=frozenset({shared.id})
    )


async def test_an_unscoped_fanout_defers_a_job_covering_every_card(
    telegram_api: TelegramApi, bot: AsyncMock, deferring_queue: RefreshQueue
):
    """No scope means the worker draws whatever the meeting still tracks when it gets there."""
    meeting, current, _shared, _other = make_meeting_with_two_shared_cards()

    await run_fanout(telegram_api, meeting, current_message=current)

    deferred = deferring_queue.pending[meeting_job_key(10)]
    assert isinstance(deferred, MeetingRefresh)
    assert deferred.message_db_ids is None


async def test_a_scope_matching_no_card_queues_no_job_at_all(
    telegram_api: TelegramApi, bot: AsyncMock, deferring_queue: RefreshQueue
):
    """The card in front of the user is still drawn, but a scope nothing matches would leave the
    worker a job with nothing to draw, so none is recorded."""
    meeting, current, _shared, _other = make_meeting_with_two_shared_cards()

    outbox = await run_fanout(telegram_api, meeting, current_message=current, only_message_db_ids=set())

    bot.do_api_request.assert_awaited_once()
    assert outbox.meeting_refreshes == []
    assert deferring_queue.pending == {}


async def test_a_scoped_fanout_draws_only_its_cards_where_the_queue_takes_no_deferrals(
    telegram_api: TelegramApi, bot: AsyncMock, deferring_queue: RefreshQueue
):
    """The scope is the caller's statement about which cards its change can reach, so it holds
    just as well on the invocation's own timeline."""
    deferring_queue.accepts_fanout = False
    meeting, current, shared, _other = make_meeting_with_two_shared_cards()

    await run_fanout(telegram_api, meeting, current_message=current, only_message_db_ids={shared.id})

    assert bot.do_api_request.await_count == 2
    assert rich_calls(bot)[-1].inline_message_id == shared.inline_message_id


@pytest.mark.parametrize("state_flag", ["was_deleted", "has_finished"])
async def test_a_terminal_fanout_refuses_a_scope(telegram_api: TelegramApi, state_flag: str):
    """A deletion and a finish have to reach every card: the rows die in this transaction, so a
    card left out of a scope would keep showing a meeting that no longer exists."""
    meeting, current, shared, _other = make_meeting_with_two_shared_cards()

    with pytest.raises(AssertionError):
        await run_fanout(
            telegram_api,
            meeting,
            current_message=current,
            only_message_db_ids={shared.id},
            **{state_flag: True},
        )


async def test_an_immediate_fanout_never_defers(
    telegram_api: TelegramApi, bot: AsyncMock, deferring_queue: RefreshQueue
):
    """Outside capture mode there is no post-commit moment to submit from, and a job submitted
    from inside an open transaction would read a meeting the caller may still roll back."""
    meeting, current, _ = make_shared_meeting()

    await telegram_api.update_meeting_messages(meeting=meeting, current_message=current)

    assert bot.do_api_request.await_count == 2
    assert deferring_queue.pending == {}


async def test_a_refresh_only_outbox_still_reaches_the_queue(
    telegram_api: TelegramApi, bot: AsyncMock, deferring_queue: RefreshQueue
):
    """An outbox can hold a refresh and no calls at all — the one card this fan-out rendered was
    skipped as unchanged — and the deferred cards were never rendered here, so an outbox dropped
    for holding no calls loses them with nothing to say so."""
    meeting, current, _ = make_shared_meeting()
    confirming = await run_fanout(telegram_api, meeting, current_message=current)
    apply_confirmed_digests(confirming, current)
    # That pass queued a refresh of its own; the assertions below are about the second one.
    deferring_queue.pending.clear()
    bot.do_api_request.reset_mock()

    outbox = await run_fanout(telegram_api, meeting, current_message=current)

    assert outbox.calls == []
    bot.do_api_request.assert_not_called()
    assert deferring_queue.pending[meeting_job_key(10)] == MeetingRefresh(meeting_id=10, skip_message_db_id=1)


async def test_a_refused_submit_loses_the_fanout_without_failing_the_invocation(
    telegram_api: TelegramApi, deferring_queue: RefreshQueue
):
    """The user's action is already committed, so a queue at its cap or on its way down costs
    the deferred cards their refresh and nothing else."""
    deferring_queue.accepting = False
    meeting, current, _ = make_shared_meeting()

    outbox = await run_fanout(telegram_api, meeting, current_message=current)

    assert len(outbox.meeting_refreshes) == 1
    assert deferring_queue.pending == {}


# ---------------------------------------------------------------------------
# Supergroup membership helpers: chat_member_is_present / chat_member_is_admin / chat_member_is_banned
# ---------------------------------------------------------------------------


def make_chat_member(status: ChatMemberStatus, *, is_member: bool | None = None) -> ChatMember:
    if status is ChatMemberStatus.RESTRICTED:
        restricted = MagicMock(spec=ChatMemberRestricted)
        restricted.status = status
        restricted.is_member = is_member
        return restricted
    member = MagicMock(spec=ChatMember)
    member.status = status
    return member


@pytest.mark.parametrize(
    "status, is_member, expected",
    [
        (ChatMemberStatus.OWNER, None, True),
        (ChatMemberStatus.ADMINISTRATOR, None, True),
        (ChatMemberStatus.MEMBER, None, True),
        (ChatMemberStatus.RESTRICTED, True, True),
        (ChatMemberStatus.RESTRICTED, False, False),
        (ChatMemberStatus.LEFT, None, False),
        (ChatMemberStatus.BANNED, None, False),
    ],
    ids=["owner", "administrator", "member", "restricted_present", "restricted_left", "left", "banned"],
)
def test_chat_member_is_present(status: ChatMemberStatus, is_member: bool | None, expected: bool):
    assert chat_member_is_present(make_chat_member(status, is_member=is_member)) is expected


@pytest.mark.parametrize(
    "status, expected",
    [
        (ChatMemberStatus.OWNER, True),
        (ChatMemberStatus.ADMINISTRATOR, True),
        (ChatMemberStatus.MEMBER, False),
        (ChatMemberStatus.RESTRICTED, False),
        (ChatMemberStatus.LEFT, False),
        (ChatMemberStatus.BANNED, False),
    ],
    ids=["owner", "administrator", "member", "restricted", "left", "banned"],
)
def test_chat_member_is_admin(status: ChatMemberStatus, expected: bool):
    is_member = True if status is ChatMemberStatus.RESTRICTED else None
    assert chat_member_is_admin(make_chat_member(status, is_member=is_member)) is expected


@pytest.mark.parametrize(
    "status, expected",
    [
        (ChatMemberStatus.BANNED, True),
        (ChatMemberStatus.OWNER, False),
        (ChatMemberStatus.ADMINISTRATOR, False),
        (ChatMemberStatus.MEMBER, False),
        (ChatMemberStatus.RESTRICTED, False),
        (ChatMemberStatus.LEFT, False),
    ],
    ids=["banned", "owner", "administrator", "member", "restricted", "left"],
)
def test_chat_member_is_banned(status: ChatMemberStatus, expected: bool):
    is_member = True if status is ChatMemberStatus.RESTRICTED else None
    assert chat_member_is_banned(make_chat_member(status, is_member=is_member)) is expected


# ---------------------------------------------------------------------------
# Supergroup admin operations: approve/decline/ban/unban
# ---------------------------------------------------------------------------


async def test_approve_chat_join_request_calls_bot(telegram_api: TelegramApi, bot: AsyncMock):
    await telegram_api.approve_chat_join_request(chat_id=-100, tg_user_id=555)

    bot.approve_chat_join_request.assert_awaited_once_with(chat_id=-100, user_id=555)


async def test_decline_chat_join_request_calls_bot(telegram_api: TelegramApi, bot: AsyncMock):
    await telegram_api.decline_chat_join_request(chat_id=-100, tg_user_id=555)

    bot.decline_chat_join_request.assert_awaited_once_with(chat_id=-100, user_id=555)


async def test_ban_chat_member_calls_bot(telegram_api: TelegramApi, bot: AsyncMock):
    await telegram_api.ban_chat_member(chat_id=-100, tg_user_id=555)

    bot.ban_chat_member.assert_awaited_once_with(chat_id=-100, user_id=555)


async def test_unban_chat_member_defaults_to_only_if_banned(telegram_api: TelegramApi, bot: AsyncMock):
    await telegram_api.unban_chat_member(chat_id=-100, tg_user_id=555)

    bot.unban_chat_member.assert_awaited_once_with(chat_id=-100, user_id=555, only_if_banned=True)


async def test_unban_chat_member_can_force_unban(telegram_api: TelegramApi, bot: AsyncMock):
    await telegram_api.unban_chat_member(chat_id=-100, tg_user_id=555, only_if_banned=False)

    bot.unban_chat_member.assert_awaited_once_with(chat_id=-100, user_id=555, only_if_banned=False)


@pytest.mark.parametrize(
    "method_name, bot_attr",
    [
        ("approve_chat_join_request", "approve_chat_join_request"),
        ("decline_chat_join_request", "decline_chat_join_request"),
        ("ban_chat_member", "ban_chat_member"),
        ("unban_chat_member", "unban_chat_member"),
    ],
)
@pytest.mark.parametrize(
    "error",
    [Forbidden("not enough rights"), BadRequest("USER_NOT_PARTICIPANT")],
    ids=["forbidden", "bad_request"],
)
async def test_admin_ops_swallow_telegram_errors(
    telegram_api: TelegramApi, bot: AsyncMock, method_name: str, bot_attr: str, error: Exception
):
    getattr(bot, bot_attr).side_effect = error

    # A Telegram failure must never propagate to the caller (handler / job / render), and the
    # caller must be able to tell that the membership change did not happen.
    assert await getattr(telegram_api, method_name)(chat_id=-100, tg_user_id=555) is False


@pytest.mark.parametrize(
    "method_name",
    ["approve_chat_join_request", "decline_chat_join_request", "ban_chat_member", "unban_chat_member"],
)
async def test_admin_ops_report_an_applied_change(telegram_api: TelegramApi, bot: AsyncMock, method_name: str):
    assert await getattr(telegram_api, method_name)(chat_id=-100, tg_user_id=555) is True


@pytest.mark.parametrize(
    ("error", "expected_reason"),
    [
        pytest.param(Forbidden("not enough rights"), "forbidden", id="forbidden"),
        pytest.param(BadRequest("USER_NOT_PARTICIPANT"), "bad_request", id="bad_request"),
    ],
)
async def test_a_swallowed_membership_failure_names_which_kind_it_was(
    telegram_api: TelegramApi,
    bot: AsyncMock,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    expected_reason: str,
):
    """`forbidden` means the bot lost its rights in the chat and every later call fails the same
    way; `bad_request` means this one call was rejected. Only the reason separates them."""
    caplog.set_level(logging.INFO)
    bot.approve_chat_join_request.side_effect = error

    await telegram_api.approve_chat_join_request(chat_id=-100, tg_user_id=555)

    record = log_record(caplog, "Failed to approve chat join request")
    assert record.levelname == "WARNING"
    assert record.__dict__["reason"] == expected_reason


# ---------------------------------------------------------------------------
# is_chat_member / is_chat_admin / is_chat_banned
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status, is_member, expected",
    [
        (ChatMemberStatus.MEMBER, None, True),
        (ChatMemberStatus.RESTRICTED, False, False),
        (ChatMemberStatus.LEFT, None, False),
    ],
    ids=["member", "restricted_left", "left"],
)
async def test_is_chat_member_maps_status(
    telegram_api: TelegramApi, bot: AsyncMock, status: ChatMemberStatus, is_member: bool | None, expected: bool
):
    bot.get_chat_member.return_value = make_chat_member(status, is_member=is_member)

    assert await telegram_api.is_chat_member(chat_id=-100, tg_user_id=555) is expected
    bot.get_chat_member.assert_awaited_once_with(chat_id=-100, user_id=555)


@pytest.mark.parametrize(
    "status, expected",
    [
        (ChatMemberStatus.OWNER, True),
        (ChatMemberStatus.ADMINISTRATOR, True),
        (ChatMemberStatus.MEMBER, False),
    ],
    ids=["owner", "administrator", "member"],
)
async def test_is_chat_admin_maps_status(
    telegram_api: TelegramApi, bot: AsyncMock, status: ChatMemberStatus, expected: bool
):
    bot.get_chat_member.return_value = make_chat_member(status)

    assert await telegram_api.is_chat_admin(chat_id=-100, tg_user_id=555) is expected


@pytest.mark.parametrize(
    "error",
    [Forbidden("member list is inaccessible"), BadRequest("user not found")],
    ids=["forbidden", "bad_request"],
)
async def test_is_chat_member_degrades_to_false_on_error(telegram_api: TelegramApi, bot: AsyncMock, error: Exception):
    bot.get_chat_member.side_effect = error

    # A failed lookup must never break the caller; it degrades to "not a member".
    assert await telegram_api.is_chat_member(chat_id=-100, tg_user_id=555) is False


@pytest.mark.parametrize(
    "error",
    [Forbidden("member list is inaccessible"), BadRequest("user not found")],
    ids=["forbidden", "bad_request"],
)
async def test_is_chat_admin_degrades_to_false_on_error(telegram_api: TelegramApi, bot: AsyncMock, error: Exception):
    bot.get_chat_member.side_effect = error

    assert await telegram_api.is_chat_admin(chat_id=-100, tg_user_id=555) is False


@pytest.mark.parametrize(
    "status, expected",
    [
        (ChatMemberStatus.BANNED, True),
        (ChatMemberStatus.MEMBER, False),
        (ChatMemberStatus.LEFT, False),
    ],
    ids=["banned", "member", "left"],
)
async def test_is_chat_banned_maps_status(
    telegram_api: TelegramApi, bot: AsyncMock, status: ChatMemberStatus, expected: bool
):
    bot.get_chat_member.return_value = make_chat_member(status)

    assert await telegram_api.is_chat_banned(chat_id=-100, tg_user_id=555) is expected


@pytest.mark.parametrize(
    "error",
    [Forbidden("member list is inaccessible"), BadRequest("user not found")],
    ids=["forbidden", "bad_request"],
)
async def test_is_chat_banned_degrades_to_false_on_error(telegram_api: TelegramApi, bot: AsyncMock, error: Exception):
    bot.get_chat_member.side_effect = error

    assert await telegram_api.is_chat_banned(chat_id=-100, tg_user_id=555) is False


# ---------------------------------------------------------------------------
# Failure-path payload logging
# ---------------------------------------------------------------------------


async def test_send_message_failure_logs_the_attempted_payload(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.effective_chat.id = 42
    view = MitupView(message=RichContent("hello there"), menu=[[ButtonConfig(text="Go", callback_data="cb")]])
    bot.do_api_request.side_effect = BadRequest("Chat not found")

    with capture_logs() as logs:
        with pytest.raises(BadRequest):
            await telegram_api.send_message(update, view)

    failure_logs = [entry for entry in logs if entry["event"] == "Telegram call failed"]
    assert len(failure_logs) == 1
    assert failure_logs[0]["telegram_call"] == "send_message"
    payload = failure_logs[0]["payload"]
    assert payload["chat_id"] == 42
    assert payload["text"] == "hello there"
    assert payload["markup"] == [[{"text": "Go", "callback_data": "cb"}]]


async def test_blocked_user_send_is_not_logged_as_telegram_call_failure(telegram_api: TelegramApi, bot: AsyncMock):
    bot.do_api_request.side_effect = Forbidden("blocked")
    user = create_user(1)

    with capture_logs() as logs:
        with pytest.raises(InactiveUserInteraction):
            await telegram_api.send_message_to_user(user, "hi")

    assert not [entry for entry in logs if entry["event"] == "Telegram call failed"]


@pytest.mark.parametrize(
    "log_card_text, card_fields",
    [(True, {"text", "markup"}), (False, {"text_len"})],
    ids=["handler_api", "background_api"],
)
async def test_a_meeting_card_payload_carries_its_text_only_where_it_is_wanted(
    telegram_api: TelegramApi, log_card_text: bool, card_fields: set[str]
):
    """A handler edits the few cards of the meeting in front of it and its failure line has to
    show what it tried to deliver. A background fanout repeats that line for every card tracking
    the meeting, and the card is prose its users wrote, so the api the refresh queue owns
    describes it by size instead."""
    meeting, card = make_bot_chat_meeting()
    telegram_api.log_card_text = log_card_text
    outbox = telegram_api.begin_capture()

    await telegram_api.update_single_meeting_message(card, meeting)

    telegram_api.end_capture()
    payload = outbox.calls[0].payload
    assert set(payload) == {"chat_id", "message_id", "inline_message_id"} | card_fields


async def test_capture_mode_enqueues_the_payload_snapshot(telegram_api: TelegramApi):
    update = MagicMock(spec=Update)
    update.effective_chat.id = 42
    outbox = telegram_api.begin_capture()

    await telegram_api.send_message(update, "hello")

    telegram_api.end_capture()
    assert outbox.calls[0].payload["chat_id"] == 42
    assert outbox.calls[0].payload["text"] == "hello"


async def test_execute_queued_failure_logs_the_attempted_payload(telegram_api: TelegramApi):
    async def boom():
        raise BadRequest("Chat not found")

    attempted = {"chat_id": 7, "text": "queued text", "markup": None}
    outbox = ApiOutbox(calls=[QueuedApiCall("send_message_to_user", boom, attempted)])

    with capture_logs() as logs:
        await telegram_api.execute_queued(outbox)

    failure_logs = [entry for entry in logs if entry["event"] == "Queued Telegram call failed after commit"]
    assert len(failure_logs) == 1
    assert failure_logs[0]["payload"] == attempted


# ---------------------------------------------------------------------------
# The rich-message payload
# ---------------------------------------------------------------------------

BOLD_ENTITY = MessageEntity(type=MessageEntity.BOLD, offset=3, length=4)
CUSTOM_EMOJI_ENTITY = MessageEntity(type=MessageEntity.CUSTOM_EMOJI, offset=0, length=2, custom_emoji_id="123456")
# The digest of `make_edit()`. Every `render_digest` stored in the database is one of these, so a
# change to the recipe would silently re-edit every card in existence: it has to fail here instead.
PLAIN_CARD_DIGEST = "5b30eff1fdef3de5414e2544d97278b2e987bd736d990dd02c82cf090a8c6e14"


def chat_update(chat_id: int) -> MagicMock:
    update = MagicMock(spec=Update)
    update.effective_chat.id = chat_id
    return update


def formatted_view() -> MitupView:
    return MitupView(
        RichContent.from_markup('<tg-emoji emoji-id="123456">😀</tg-emoji> <b>bold</b>'),
        [[ButtonConfig(text="Join", callback_data="join")]],
    )


# --- Sending ---


async def test_a_send_carries_the_whole_body_as_html(telegram_api: TelegramApi, bot: AsyncMock):
    await telegram_api.send_message(chat_update(42), formatted_view())

    call = only_rich_call(bot)
    assert call.endpoint == SEND_RICH_MESSAGE_ENDPOINT
    # An exact comparison is the assertion: `text` and `rich_message` are mutually exclusive on the
    # wire, and a rich message has no link-preview parameter to pass.
    assert call.api_kwargs == {
        "chat_id": 42,
        "rich_message": {
            "html": (
                '<tg-emoji emoji-id="123456">😀</tg-emoji> <b>bold</b>'
                '<hr/><tg-button-row><tg-button type="callback_data" data="join">Join</tg-button></tg-button-row>'
            ),
            "skip_entity_detection": True,
        },
    }
    assert bot.do_api_request.call_args.kwargs["return_type"] is Message


async def test_a_send_without_a_keyboard_closes_the_content_with_no_rows(telegram_api: TelegramApi, bot: AsyncMock):
    await telegram_api.send_message(chat_update(42), "plain")

    call = only_rich_call(bot)
    assert call.html == "plain"
    assert call.button_rows == []


async def test_a_send_to_a_user_addresses_their_private_chat(telegram_api: TelegramApi, bot: AsyncMock):
    user = create_user(id=1, tg_user_id=777)

    await telegram_api.send_message_to_user(user, "hi")

    call = only_rich_call(bot)
    assert call.endpoint == SEND_RICH_MESSAGE_ENDPOINT
    assert call.chat_id == 777


async def test_a_user_who_blocked_the_bot_is_reported_unreachable(telegram_api: TelegramApi, bot: AsyncMock):
    bot.do_api_request.side_effect = Forbidden("bot was blocked by the user")

    with pytest.raises(InactiveUserInteraction):
        await telegram_api.send_message_to_user(create_user(id=1, tg_user_id=777), "hi")


# --- The file a message carries ---

DOCUMENT_BLOCK = f'<tg-document src="tg://document?id={DOCUMENT_MEDIA_ID}"></tg-document>'


def export_view() -> MitupView:
    return MitupView(
        RichContent("Your data"),
        [[ButtonConfig(text="Privacy", callback_data="send;privacy")]],
        document=RichDocument(content=b"{}", filename="export.json"),
    )


async def test_a_send_carrying_a_file_attaches_it_to_the_rich_message(telegram_api: TelegramApi, bot: AsyncMock):
    await telegram_api.send_message(chat_update(42), export_view())

    call = only_rich_call(bot)
    assert call.endpoint == SEND_RICH_MESSAGE_ENDPOINT
    # The block sits under the text captioning it and above the rows closing the message.
    assert call.body_html == f"Your data{DOCUMENT_BLOCK}"
    assert call.button_rows
    assert call.media == [
        {"id": DOCUMENT_MEDIA_ID, "media": {"type": "document", "media": f"attach://{DOCUMENT_ATTACH_NAME}"}}
    ]
    upload = call.uploads[DOCUMENT_ATTACH_NAME]
    assert (upload.filename, upload.input_file_content) == ("export.json", b"{}")


async def test_a_send_carrying_no_file_names_no_media(telegram_api: TelegramApi, bot: AsyncMock):
    await telegram_api.send_message(chat_update(42), "plain")

    call = only_rich_call(bot)
    assert call.media == []
    assert call.uploads == {}


async def test_the_file_is_uploaded_again_on_the_custom_emoji_retry(telegram_api: TelegramApi, bot: AsyncMock):
    """Every attempt builds its own upload: the file a refused request already read cannot be
    handed to the one replacing it."""
    bot.do_api_request.side_effect = [BadRequest("Custom emoji entities are not allowed"), None]
    view = MitupView(
        RichContent.from_markup('<tg-emoji emoji-id="123456">😀</tg-emoji>'),
        document=RichDocument(content=b"{}", filename="export.json"),
    )

    await telegram_api.send_message(chat_update(42), view)

    assert bot.do_api_request.await_count == 2
    assert rich_call(bot, 1).uploads[DOCUMENT_ATTACH_NAME].input_file_content == b"{}"


async def test_a_queued_send_carries_its_file_to_the_drain(telegram_api: TelegramApi, bot: AsyncMock):
    outbox = telegram_api.begin_capture()
    assert await telegram_api.send_message(chat_update(42), export_view()) is None
    telegram_api.end_capture()
    bot.do_api_request.assert_not_called()

    await telegram_api.execute_queued(outbox)

    call = only_rich_call(bot)
    assert call.body_html == f"Your data{DOCUMENT_BLOCK}"
    assert call.uploads[DOCUMENT_ATTACH_NAME].input_file_content == b"{}"


async def test_an_edit_carrying_a_file_uploads_it_beside_the_rich_message(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.effective_message.chat.id = 123
    update.effective_message.id = 456
    bot.do_api_request.return_value = MagicMock(spec=Message)

    await telegram_api.edit_message(update, export_view())

    call = only_rich_call(bot)
    assert call.endpoint == EDIT_MESSAGE_TEXT_ENDPOINT
    assert call.body_html == f"Your data{DOCUMENT_BLOCK}"
    assert call.media == [
        {"id": DOCUMENT_MEDIA_ID, "media": {"type": "document", "media": f"attach://{DOCUMENT_ATTACH_NAME}"}}
    ]
    upload = call.uploads[DOCUMENT_ATTACH_NAME]
    assert (upload.filename, upload.input_file_content) == ("export.json", b"{}")


async def test_a_failed_send_reports_the_file_it_tried_to_deliver(telegram_api: TelegramApi, bot: AsyncMock):
    bot.do_api_request.side_effect = BadRequest("Chat not found")

    with capture_logs() as logs:
        with pytest.raises(BadRequest):
            await telegram_api.send_message(chat_update(42), export_view())

    payload = next(entry for entry in logs if entry["event"] == "Telegram call failed")["payload"]
    assert payload["document_filename"] == "export.json"
    assert payload["document_bytes"] == 2


# --- Editing ---


async def test_a_card_edit_sends_the_rendered_meeting_as_html(telegram_api: TelegramApi, bot: AsyncMock):
    meeting, card = make_bot_chat_meeting()

    await telegram_api.update_single_meeting_message(card, meeting)

    call = only_rich_call(bot)
    assert call.endpoint == EDIT_MESSAGE_TEXT_ENDPOINT
    assert call.chat_id == card.chat_id
    assert call.message_id == card.message_id
    assert "Test Meeting" in call.body_html
    assert call.button_rows


async def test_a_card_edit_of_an_inline_card_names_no_chat(telegram_api: TelegramApi, bot: AsyncMock):
    """A stored inline card keeps a chat id alongside its inline message id, and the two address
    different messages: only the inline one may reach a raw edit."""
    meeting, card = make_inline_meeting()

    await telegram_api.update_single_meeting_message(card, meeting)

    call = only_rich_call(bot)
    assert call.inline_message_id == "inline_123"
    assert "chat_id" not in call.api_kwargs
    assert "message_id" not in call.api_kwargs


async def test_an_edit_still_swallows_an_unchanged_message(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.effective_message.chat.id = 123
    update.effective_message.id = 456
    bot.do_api_request.side_effect = BadRequest("Message is not modified: nothing changed")

    assert await telegram_api.edit_message(update, "hello") is False


# --- The custom-emoji retry ---


async def test_a_send_retries_with_html_rebuilt_from_the_stripped_body(telegram_api: TelegramApi, bot: AsyncMock):
    """The retry must re-serialize the stripped body rather than resend the html Telegram just
    rejected, which is the whole point of stripping the entity."""
    bot.do_api_request.side_effect = [BadRequest("Custom emoji entities are not allowed"), None]

    await telegram_api.send_message(chat_update(42), formatted_view())

    assert bot.do_api_request.await_count == 2
    assert rich_call(bot, 0).body_html == '<tg-emoji emoji-id="123456">😀</tg-emoji> <b>bold</b>'
    assert rich_call(bot, 1).body_html == "😀 <b>bold</b>"
    # The labels are plain text, so nothing about the rows changes between the attempts.
    assert rich_call(bot, 1).button_rows == rich_call(bot, 0).button_rows


async def test_an_edit_retries_with_html_rebuilt_from_the_stripped_body(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.effective_message.chat.id = 123
    update.effective_message.id = 456
    bot.do_api_request.side_effect = [BadRequest("can't parse custom emoji entity"), None]

    await telegram_api.edit_message(update, formatted_view())

    assert bot.do_api_request.await_count == 2
    assert rich_call(bot, 1).body_html == "😀 <b>bold</b>"


async def test_a_send_rejected_for_another_reason_is_not_retried(telegram_api: TelegramApi, bot: AsyncMock):
    bot.do_api_request.side_effect = BadRequest("Chat not found")

    with pytest.raises(BadRequest):
        await telegram_api.send_message(chat_update(42), formatted_view())

    assert bot.do_api_request.await_count == 1


# --- The render digest ---


def test_the_digest_is_pinned_to_the_payload_it_hashes():
    assert make_edit().digest == PLAIN_CARD_DIGEST


def test_the_digest_is_stable_across_equal_payloads():
    [MessageEntity(type=MessageEntity.BOLD, offset=0, length=4)]

    assert (
        make_edit(keyboard=JOIN_ROW).digest
        == make_edit(keyboard=[[ButtonConfig(text="Join", callback_data="join")]]).digest
    )


@pytest.mark.parametrize(
    "changed",
    [
        make_edit(RichContent("Different body"), keyboard=JOIN_ROW),
        make_edit(RichContent.from_markup("<b>Card</b> body"), keyboard=JOIN_ROW),
        make_edit(),
        make_edit(keyboard=[[ButtonConfig(text="Leave", callback_data="join")]]),
        make_edit(keyboard=[[ButtonConfig(text="Join", callback_data="leave")]]),
        make_edit(keyboard=[JOIN_ROW[0], JOIN_ROW[0]]),
    ],
    ids=["text", "entities", "no_keyboard", "button_label", "button_callback", "row_count"],
)
def test_the_digest_changes_with_every_part_of_the_payload(changed: MeetingMessageEdit):
    """The buttons are inside the html the digest hashes, so a relabelled or re-pointed button is
    a content change like any other and the card is re-edited for it."""
    assert changed.digest != make_edit(keyboard=JOIN_ROW).digest


# --- The photos a card shows ---

BANNER = RichPhoto(media_id="AQADHRJrGzSd4FB-", file_id="AgACAgQAAxkBAAIB")


def banner_edit(photo: RichPhoto = BANNER) -> MeetingMessageEdit:
    return make_edit(RichContent("Card body").prepend(photo_content(photo.media_id)), photos=(photo,))


def test_a_card_names_its_photos_in_the_media_list():
    assert banner_edit().rich_message().to_api_dict()["media"] == [
        {"id": "AQADHRJrGzSd4FB-", "media": {"type": "photo", "media": "AgACAgQAAxkBAAIB"}}
    ]


async def test_a_meeting_card_carries_the_meeting_photos(telegram_api: TelegramApi, bot: AsyncMock):
    meeting, msg = make_bot_chat_meeting()
    meeting.images = [MeetingImage(meetup_id=meeting.id, position=0, file_id="AgACAgQAAxkBAAIB", file_unique_id="AQAD")]

    await telegram_api.update_single_meeting_message(msg, meeting)

    assert rich_call(bot).media == [{"id": "AQAD", "media": {"type": "photo", "media": "AgACAgQAAxkBAAIB"}}]


async def test_the_meeting_photos_travel_in_the_order_they_are_held(telegram_api: TelegramApi, bot: AsyncMock):
    meeting, msg = make_bot_chat_meeting()
    meeting.images = [
        MeetingImage(meetup_id=meeting.id, position=0, file_id="first_file", file_unique_id="AQADfirst"),
        MeetingImage(meetup_id=meeting.id, position=1, file_id="second_file", file_unique_id="AQADsecond"),
    ]

    await telegram_api.update_single_meeting_message(msg, meeting)

    assert [entry["id"] for entry in rich_call(bot).media] == ["AQADfirst", "AQADsecond"]


def test_the_digest_changes_when_a_shown_photo_changes():
    replaced = RichPhoto(media_id="AQADdifferent", file_id=BANNER.file_id)

    assert banner_edit(replaced).digest != banner_edit().digest


def test_the_digest_is_unchanged_when_only_the_file_behind_a_photo_changes():
    """The digest hashes the html, which names a photo by its media id, and that id is stable across
    uploads. A re-uploaded file of the same picture is the same card and needs no edit."""
    reuploaded = RichPhoto(media_id=BANNER.media_id, file_id="AgACAgQAAxkBAAIZ")

    assert banner_edit(reuploaded).digest == banner_edit().digest


# --- PTB's advice on an endpoint it models ---


@pytest.mark.parametrize("endpoint", [EDIT_MESSAGE_TEXT_ENDPOINT, ANSWER_INLINE_QUERY_ENDPOINT])
async def test_the_advisory_is_what_ptb_actually_raises(endpoint: str, monkeypatch: pytest.MonkeyPatch):
    """The filters match the advisory text, so a PTB release that rewords it would silently stop
    suppressing anything. Drive the real `do_api_request` and compare what it raises."""
    monkeypatch.setattr(Bot, "_post", AsyncMock(return_value=True))
    bot = Bot("123456:abcdefghijklmnopqrstuvwxyzABCDEFGHI")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await bot.do_api_request(endpoint, api_kwargs={"chat_id": 1})

    assert [str(warning.message) for warning in caught] == [modelled_endpoint_advisory(endpoint)]


async def test_the_rich_send_endpoint_draws_no_advisory(monkeypatch: pytest.MonkeyPatch):
    """PTB models no `send_rich_message`, so that endpoint needs no filter and must not get one."""
    monkeypatch.setattr(Bot, "_post", AsyncMock(return_value=True))
    bot = Bot("123456:abcdefghijklmnopqrstuvwxyzABCDEFGHI")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await bot.do_api_request(SEND_RICH_MESSAGE_ENDPOINT, api_kwargs={"chat_id": 1})

    assert caught == []


@pytest.mark.parametrize("endpoint", [EDIT_MESSAGE_TEXT_ENDPOINT, ANSWER_INLINE_QUERY_ENDPOINT])
def test_silencing_the_advisories_leaves_every_other_warning_audible(endpoint: str):
    """The filters live for the life of the process, so each is matched on its one advisory rather
    than on the category they share."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        silence_modelled_endpoint_advisory()
        warnings.warn(modelled_endpoint_advisory(endpoint), PTBUserWarning, stacklevel=1)
        warnings.warn("something else entirely", PTBUserWarning, stacklevel=1)

    assert [str(warning.message) for warning in caught] == ["something else entirely"]


# ---------------------------------------------------------------------------
# Keyed sends and held fan-outs
# ---------------------------------------------------------------------------

ALBUM_STRATEGY = OutboxStrategy(("album", "media-group-1"), hold_seconds=1.5)


async def test_a_send_with_a_strategy_reaches_the_worker_instead_of_the_chat(
    telegram_api: TelegramApi, bot: AsyncMock, deferring_queue: RefreshQueue
):
    """The worker sends the message once no more submits arrive under its key."""
    outbox = telegram_api.begin_capture()
    with bound_contextvars(update_id=ORIGIN_UPDATE_ID):
        assert await telegram_api.send_message(chat_update(42), "hello", strategy=ALBUM_STRATEGY) is None
    telegram_api.end_capture()
    await telegram_api.execute_queued(outbox)

    bot.do_api_request.assert_not_called()
    queued = deferring_queue.pending[ALBUM_STRATEGY.key]
    assert isinstance(queued, KeyedSend)
    assert (queued.chat_id, queued.hold_seconds, queued.origin_update_id) == (42, 1.5, ORIGIN_UPDATE_ID)
    assert queued.payload.html == "hello"


async def test_a_send_without_a_strategy_goes_out_on_the_drain(
    telegram_api: TelegramApi, bot: AsyncMock, deferring_queue: RefreshQueue
):
    outbox = telegram_api.begin_capture()
    assert await telegram_api.send_message(chat_update(42), "hello") is None
    telegram_api.end_capture()
    await telegram_api.execute_queued(outbox)

    assert rich_call(bot).chat_id == 42
    assert deferring_queue.pending == {}


async def test_a_strategy_with_no_worker_to_hand_it_to_sends_the_message(telegram_api: TelegramApi, bot: AsyncMock):
    """A CLI job and a recurrent event run without a queue, so the message is sent directly."""
    await telegram_api.send_message(chat_update(42), "hello", strategy=ALBUM_STRATEGY)

    assert rich_call(bot).chat_id == 42


async def test_a_rendered_payload_is_sent_again_without_its_custom_emoji(telegram_api: TelegramApi, bot: AsyncMock):
    """The worker has only the rendered payload, not the view, so the retry strips the custom emoji
    from the payload's html."""
    bot.do_api_request.side_effect = [BadRequest("Custom emoji entities are not allowed"), None]

    await telegram_api.send_rich_payload(42, formatted_view().rich_message())

    assert bot.do_api_request.await_count == 2
    assert "tg-emoji" not in rich_call(bot, 1).html
    assert "😀" in rich_call(bot, 1).html


async def test_a_fanout_strategy_holds_the_job_it_defers(
    telegram_api: TelegramApi, bot: AsyncMock, deferring_queue: RefreshQueue
):
    meeting, current, _ = make_shared_meeting()

    await run_fanout(
        telegram_api,
        meeting,
        current_message=current,
        strategy=OutboxStrategy(meeting_job_key(10), hold_seconds=1.5),
    )

    assert deferring_queue.pending[meeting_job_key(10)].hold_seconds == 1.5


async def test_a_fanout_strategy_naming_another_meeting_is_refused(
    telegram_api: TelegramApi, deferring_queue: RefreshQueue
):
    """A fan-out job is keyed by the meeting it draws, so a strategy keyed to another meeting is a
    caller bug."""
    meeting, current, _ = make_shared_meeting()

    with pytest.raises(AssertionError):
        await run_fanout(telegram_api, meeting, current_message=current, strategy=OutboxStrategy(meeting_job_key(11)))
