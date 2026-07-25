"""Tests for the real TelegramApi class with a mocked bot."""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from structlog.testing import capture_logs
from telegram import ChatMember, ChatMemberRestricted, Message, MessageEntity, Update
from telegram.constants import ChatMemberStatus
from telegram.error import BadRequest, Forbidden, NetworkError, TimedOut
from telegram.ext import ExtBot

from mitup_bot.api_wrapper import (
    CALLBACK_QUERY_TEXT_LIMIT,
    TELEMGRAM_API_TIME_PREFIX,
    ApiOutbox,
    BotAdapter,
    QueuedApiCall,
    TelegramApi,
    build_api,
    chat_member_is_admin,
    chat_member_is_banned,
    chat_member_is_present,
    handle_edit_errors,
)
from mitup_bot.exceptions import (
    AnswerInlineQueryError,
    CallbackQueryTextTooLong,
    InactiveUserInteraction,
    NoDocumentAvailable,
    NoMessageAvailable,
)
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup
from mitup_bot.models import Message as MessageModel
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.protocols import ContextOrBotAdapter
from mitup_bot.utils.entities import FormattedText
from mitup_bot.views import InlineResultsButton, MitupInlineView, MitupView, ViewDocument
from tests.helpers import AnyFloat, make_test_metrics_client
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
    bot.send_message.return_value = sentinel

    result = await telegram_api.send_message(update, "hello")

    bot.send_message.assert_awaited_once_with(
        chat_id=42, text="hello", entities=None, reply_markup=None, disable_web_page_preview=True
    )
    assert result is sentinel


async def test_send_message_with_mitup_view(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.effective_chat.id = 99
    view = MitupView(description="view text", keyboard=[])
    sentinel = MagicMock(spec=Message)
    bot.send_message.return_value = sentinel

    result = await telegram_api.send_message(update, view)

    bot.send_message.assert_awaited_once_with(
        chat_id=99, text="view text", entities=None, reply_markup=view.markup, disable_web_page_preview=True
    )
    assert result is sentinel


# ---------------------------------------------------------------------------
# send_document
# ---------------------------------------------------------------------------


async def test_send_document_sends_bytes_with_filename(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.effective_chat.id = 42
    sentinel = MagicMock(spec=Message)
    bot.send_document.return_value = sentinel
    view = MitupView("a caption", keyboard=[], document=ViewDocument(content=b"{}", filename="export.json"))

    result = await telegram_api.send_document(update, view)

    bot.send_document.assert_awaited_once_with(
        chat_id=42,
        document=b"{}",
        filename="export.json",
        caption="a caption",
        caption_entities=None,
        reply_markup=None,
    )
    assert result is sentinel


async def test_send_document_with_formatted_caption(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.effective_chat.id = 42
    caption_entity = MessageEntity(type=MessageEntity.BOLD, offset=0, length=4)
    caption = FormattedText("bold caption", entities=[caption_entity])
    view = MitupView(caption, keyboard=[], document=ViewDocument(content=b"{}", filename="export.json"))

    await telegram_api.send_document(update, view)

    bot.send_document.assert_awaited_once_with(
        chat_id=42,
        document=b"{}",
        filename="export.json",
        caption="bold caption",
        caption_entities=[caption_entity],
        reply_markup=None,
    )


async def test_send_document_with_keyboard(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.effective_chat.id = 42
    keyboard = [[ButtonConfig(text="Privacy", callback_data="send;privacy")]]
    view = MitupView("a caption", keyboard=keyboard, document=ViewDocument(content=b"{}", filename="export.json"))

    await telegram_api.send_document(update, view)

    bot.send_document.assert_awaited_once_with(
        chat_id=42,
        document=b"{}",
        filename="export.json",
        caption="a caption",
        caption_entities=None,
        reply_markup=MitupView.keyboard_to_markup(keyboard),
    )


async def test_send_document_raises_when_the_view_carries_no_document(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.effective_chat.id = 42

    with pytest.raises(NoDocumentAvailable):
        await telegram_api.send_document(update, MitupView("a caption", keyboard=[]))

    bot.send_document.assert_not_called()


async def test_send_document_is_queued_under_capture(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.effective_chat.id = 42
    view = MitupView("a caption", keyboard=[], document=ViewDocument(content=b"{}", filename="export.json"))

    outbox = telegram_api.begin_capture()
    assert await telegram_api.send_document(update, view) is None
    telegram_api.end_capture()

    bot.send_document.assert_not_called()

    await telegram_api.execute_queued(outbox)

    bot.send_document.assert_awaited_once_with(
        chat_id=42,
        document=b"{}",
        filename="export.json",
        caption="a caption",
        caption_entities=None,
        reply_markup=None,
    )


async def test_send_document_keyboard_is_preserved_under_capture(telegram_api: TelegramApi, bot: AsyncMock):
    # The markup is rendered at enqueue time and carried by the queued call as plain data,
    # so the replay after commit sends the exact keyboard the handler attached.
    update = MagicMock(spec=Update)
    update.effective_chat.id = 42
    keyboard = [[ButtonConfig(text="Privacy", callback_data="send;privacy")]]
    view = MitupView("a caption", keyboard=keyboard, document=ViewDocument(content=b"{}", filename="export.json"))

    outbox = telegram_api.begin_capture()
    assert await telegram_api.send_document(update, view) is None
    telegram_api.end_capture()

    bot.send_document.assert_not_called()

    await telegram_api.execute_queued(outbox)

    bot.send_document.assert_awaited_once_with(
        chat_id=42,
        document=b"{}",
        filename="export.json",
        caption="a caption",
        caption_entities=None,
        reply_markup=MitupView.keyboard_to_markup(keyboard),
    )


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
    # the write-mode reconcile; here the error is only swallowed and counted.
    async with handle_edit_errors(cast(ContextOrBotAdapter, adapter)):
        raise BadRequest(error_message)


async def test_handle_edit_errors_reraises_other_bad_request(adapter: BotAdapter):
    with pytest.raises(BadRequest, match="Something else"):
        async with handle_edit_errors(cast(ContextOrBotAdapter, adapter)):
            raise BadRequest("Something else went wrong")


# ---------------------------------------------------------------------------
# send_message_to_user
# ---------------------------------------------------------------------------


async def test_send_message_to_user_with_mitup_view(telegram_api: TelegramApi, bot: AsyncMock):
    user = create_user(id=1, tg_user_id=456)
    view = MitupView(description="Hello world", keyboard=[])
    sentinel = MagicMock(spec=Message)
    bot.send_message.return_value = sentinel

    result = await telegram_api.send_message_to_user(user, view)

    bot.send_message.assert_awaited_once_with(
        chat_id=456,
        text="Hello world",
        entities=None,
        reply_markup=view.markup,
        disable_web_page_preview=True,
    )
    assert result is sentinel


async def test_send_message_to_user_with_plain_string(telegram_api: TelegramApi, bot: AsyncMock):
    user = create_user(id=1, tg_user_id=789)
    sentinel = MagicMock(spec=Message)
    bot.send_message.return_value = sentinel

    result = await telegram_api.send_message_to_user(user, "plain text")

    bot.send_message.assert_awaited_once_with(
        chat_id=789,
        text="plain text",
        entities=None,
        reply_markup=None,
        disable_web_page_preview=True,
    )
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
    bot.send_message.side_effect = side_effect

    with pytest.raises(InactiveUserInteraction) as exc_info:
        await telegram_api.send_message_to_user(user, "test")

    assert exc_info.value.tg_user_id == tg_user_id


async def test_send_message_to_user_other_bad_request_reraised(telegram_api: TelegramApi, bot: AsyncMock):
    user = create_user(id=1, tg_user_id=333)
    bot.send_message.side_effect = BadRequest("Something else")

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
    bot.send_message.return_value = MagicMock(spec=Message)

    await telegram_api.send_messages_to_users([user1, user2], ["msg1", "msg2"], on_success=[on_success_1, on_success_2])

    on_success_1.assert_called_once_with(user1)
    on_success_2.assert_called_once_with(user2)


async def test_send_messages_to_users_inactive_user_marked_inactive(
    telegram_api: TelegramApi, bot: AsyncMock, api_metrics: MetricAssertions, api_metrics_client: MetricsClient
):
    """MEMBER user blocking the bot must transition to LEFT and emit INACTIVE_USER_SET."""
    user1 = create_user(id=1, tg_user_id=100)
    user2 = create_user(id=2, tg_user_id=200)
    bot.send_message.side_effect = [
        Forbidden("Forbidden: bot was blocked by the user"),
        MagicMock(spec=Message),
    ]

    await telegram_api.send_messages_to_users([user1, user2], ["msg1", "msg2"])
    await api_metrics_client.flush()

    assert user1.status is UserStatus.LEFT
    assert user2.status is UserStatus.MEMBER
    # Only the MEMBER → LEFT transition emits the metric.
    api_metrics.assert_emitted(name=MetricKey.INACTIVE_USER_SET, times=1)


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
    bot.send_message.side_effect = Forbidden("Forbidden: bot was blocked by the user")

    await telegram_api.send_messages_to_users([joined_only_user], ["msg1"])
    await api_metrics_client.flush()

    # Status stays JOINED_ONLY — mark_inactive() returns False.
    assert joined_only_user.status is UserStatus.JOINED_ONLY
    api_metrics.assert_not_emitted(name=MetricKey.INACTIVE_USER_SET)


async def test_send_messages_to_users_general_error_calls_on_error(telegram_api: TelegramApi, bot: AsyncMock):
    user1 = create_user(id=1, tg_user_id=100)
    on_error_1 = MagicMock()
    bot.send_message.side_effect = RuntimeError("network failure")

    await telegram_api.send_messages_to_users([user1], ["msg1"], on_error=[on_error_1])

    on_error_1.assert_called_once()
    call_args = on_error_1.call_args
    assert call_args[0][0] is user1
    assert isinstance(call_args[0][1], RuntimeError)


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
    bot.send_message.return_value = MagicMock(spec=Message)

    await telegram_api.notify_users_promoted_from_waiting_list([organic_link, invited_link], meeting)

    assert bot.send_message.await_count == 1
    assert bot.send_message.call_args.kwargs["chat_id"] == 200


# ---------------------------------------------------------------------------
# update_single_meeting_message
# ---------------------------------------------------------------------------


def make_bot_chat_meeting() -> tuple[Meetup, MessageModel]:
    meeting = create_meetup(id=10, title="Test Meeting", language="en")
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

    bot.edit_message_text.assert_awaited_once()
    call_kwargs = bot.edit_message_text.call_args.kwargs
    assert call_kwargs["chat_id"] == msg.chat_id
    assert call_kwargs["message_id"] == msg.message_id
    assert call_kwargs["reply_markup"] is not None
    # The rendered keyboard is persisted onto the row via MutableModel change tracking with
    # the caller's surrounding transaction.
    assert msg.buttons.keyboard


async def test_update_single_meeting_message_inline(telegram_api: TelegramApi, bot: AsyncMock):
    meeting, msg = make_inline_meeting()

    await telegram_api.update_single_meeting_message(msg, meeting)

    bot.edit_message_text.assert_awaited_once()
    call_kwargs = bot.edit_message_text.call_args.kwargs
    assert call_kwargs["inline_message_id"] == "inline_123"
    assert call_kwargs["reply_markup"] is not None


@pytest.mark.parametrize(
    "was_deleted, has_finished, expected_text_fragment",
    [
        (True, False, "deleted"),
        (False, True, "finished"),
    ],
    ids=["was_deleted", "has_finished"],
)
async def test_update_single_meeting_message_state_flags(
    telegram_api: TelegramApi,
    bot: AsyncMock,
    was_deleted: bool,
    has_finished: bool,
    expected_text_fragment: str,
):
    meeting, msg = make_bot_chat_meeting()

    await telegram_api.update_single_meeting_message(msg, meeting, was_deleted=was_deleted, has_finished=has_finished)

    call_kwargs = bot.edit_message_text.call_args.kwargs
    assert call_kwargs["reply_markup"] is None
    assert expected_text_fragment in call_kwargs["text"].lower()


async def test_update_single_meeting_message_inline_vs_bot_chat_different_views(
    telegram_api: TelegramApi, bot: AsyncMock
):
    meeting_bot, msg_bot = make_bot_chat_meeting()
    meeting_inline, msg_inline = make_inline_meeting()

    await telegram_api.update_single_meeting_message(msg_bot, meeting_bot)
    bot_chat_text = bot.edit_message_text.call_args.kwargs["text"]

    bot.reset_mock()

    await telegram_api.update_single_meeting_message(msg_inline, meeting_inline)
    inline_text = bot.edit_message_text.call_args.kwargs["text"]

    assert isinstance(bot_chat_text, str)
    assert isinstance(inline_text, str)


async def test_update_single_meeting_message_not_modified_is_swallowed(telegram_api: TelegramApi, bot: AsyncMock):
    meeting, msg = make_bot_chat_meeting()
    bot.edit_message_text.side_effect = BadRequest("Message is not modified: ...")

    # Verifies the wiring: update_single_meeting_message routes errors through handle_edit_errors
    await telegram_api.update_single_meeting_message(msg, meeting)


async def test_update_single_meeting_message_not_found_is_swallowed_in_immediate_mode(
    telegram_api: TelegramApi, bot: AsyncMock
):
    meeting, msg = make_bot_chat_meeting()
    bot.edit_message_text.side_effect = BadRequest("Message to edit not found")

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
    bot.edit_message_text.side_effect = Forbidden(error_message)

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

    assert bot.edit_message_text.await_count == 2


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

    assert bot.edit_message_text.await_count == 1


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

    assert bot.edit_message_text.await_count == 2


@pytest.mark.parametrize(
    "was_deleted, has_finished, expected_text_fragment",
    [
        (True, False, "deleted"),
        (False, True, "finished"),
    ],
    ids=["was_deleted", "has_finished"],
)
async def test_update_meeting_messages_state_flag_propagated(
    telegram_api: TelegramApi,
    bot: AsyncMock,
    was_deleted: bool,
    has_finished: bool,
    expected_text_fragment: str,
):
    meeting = create_meetup(id=10, title="Meeting", language="en")
    create_user(id=1, tg_user_id=100, owned_meetings=[meeting])
    msg = create_message(id=1, inline_message_id=None, chat_id=100, message_id=501, meetup_id=10)
    meeting.messages = [msg]

    await telegram_api.update_meeting_messages(
        meeting=meeting,
        current_message=msg,
        was_deleted=was_deleted,
        has_finished=has_finished,
    )

    call_kwargs = bot.edit_message_text.call_args.kwargs
    assert call_kwargs["reply_markup"] is None
    assert expected_text_fragment in call_kwargs["text"].lower()


# ---------------------------------------------------------------------------
# BotAdapter.flush_metrics
# ---------------------------------------------------------------------------


async def test_bot_adapter_flush_metrics(bot: AsyncMock):

    adapter = BotAdapter(bot=bot, metrics=make_test_metrics_client())
    await adapter.flush_metrics()


# ---------------------------------------------------------------------------
# _with_time_metrics_context — CallbackContext adapter path (lines 195-196)
# ---------------------------------------------------------------------------


async def test_send_message_to_user_with_context_adapter_uses_time_metric(bot: AsyncMock):
    from mitup_bot.custom_context import MitupContext

    mock_context = MagicMock(spec=MitupContext)
    mock_context.__class__ = MitupContext
    mock_context.bot = bot
    bot.send_message.return_value = MagicMock(spec=Message)

    api = TelegramApi()
    api.adapter = cast(ContextOrBotAdapter, mock_context)
    user = create_user(id=1, tg_user_id=456)

    await api.send_message_to_user(user, "test")

    mock_context.with_time_metric.assert_called_once_with(prefix=TELEMGRAM_API_TIME_PREFIX)


# ---------------------------------------------------------------------------
# edit_message
# ---------------------------------------------------------------------------


async def test_edit_message_with_effective_message(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.effective_message.chat.id = 123
    update.effective_message.id = 456
    sentinel = MagicMock(spec=Message)
    bot.edit_message_text.return_value = sentinel

    result = await telegram_api.edit_message(update, "hello")

    bot.edit_message_text.assert_awaited_once_with(
        text="hello",
        entities=None,
        chat_id=123,
        message_id=456,
        inline_message_id=None,
        reply_markup=None,
        disable_web_page_preview=True,
    )
    assert result is sentinel


async def test_edit_message_with_inline_message_id(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.effective_message = None
    update.callback_query.inline_message_id = "inline_999"
    sentinel = MagicMock()
    bot.edit_message_text.return_value = sentinel

    result = await telegram_api.edit_message(update, "hello inline")

    bot.edit_message_text.assert_awaited_once_with(
        text="hello inline",
        entities=None,
        chat_id=None,
        message_id=None,
        inline_message_id="inline_999",
        reply_markup=None,
        disable_web_page_preview=True,
    )
    assert result is sentinel


async def test_edit_message_with_mitup_view(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.effective_message.chat.id = 123
    update.effective_message.id = 456
    view = MitupView(description="view text", keyboard=[])
    bot.edit_message_text.return_value = MagicMock(spec=Message)

    await telegram_api.edit_message(update, view)

    call_kwargs = bot.edit_message_text.call_args.kwargs
    assert call_kwargs["text"] == "view text"
    assert call_kwargs["reply_markup"] == view.markup


async def test_edit_message_raises_no_message_available(telegram_api: TelegramApi):
    update = MagicMock(spec=Update)
    update.effective_message = None
    update.callback_query = None

    with pytest.raises(NoMessageAvailable):
        await telegram_api.edit_message(update, "text")


# ---------------------------------------------------------------------------
# clear_reply_markup
# ---------------------------------------------------------------------------


async def test_clear_reply_markup_with_effective_message(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.effective_message.chat.id = 77
    update.effective_message.id = 88

    await telegram_api.clear_reply_markup(update)

    bot.edit_message_reply_markup.assert_awaited_once_with(
        chat_id=77,
        message_id=88,
        inline_message_id=None,
        reply_markup=None,
    )


async def test_clear_reply_markup_with_inline_message_id(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.effective_message = None
    update.callback_query.inline_message_id = "inline_42"

    await telegram_api.clear_reply_markup(update)

    bot.edit_message_reply_markup.assert_awaited_once_with(
        chat_id=None,
        message_id=None,
        inline_message_id="inline_42",
        reply_markup=None,
    )


async def test_clear_reply_markup_suppresses_message_not_modified(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.effective_message.chat.id = 1
    update.effective_message.id = 2
    # "Message is not modified" is silently ignored by handle_edit_errors
    bot.edit_message_reply_markup.side_effect = BadRequest(
        "Message is not modified: specified new message content and reply markup are exactly the same"
    )

    # Must not raise
    await telegram_api.clear_reply_markup(update)


async def test_clear_reply_markup_raises_no_message_available(telegram_api: TelegramApi):
    update = MagicMock(spec=Update)
    update.effective_message = None
    update.callback_query = None

    with pytest.raises(NoMessageAvailable):
        await telegram_api.clear_reply_markup(update)


# ---------------------------------------------------------------------------
# answer_inline_query
# ---------------------------------------------------------------------------


async def test_answer_inline_query_with_button(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    bot.answer_inline_query.return_value = True
    view = MitupInlineView(description="desc", title="Title", inline_description="short", id="1", keyboard=[])
    button = InlineResultsButton(text="Go", start_parameter="start")

    await telegram_api.answer_inline_query(update, [view], button=button)

    call_kwargs = bot.answer_inline_query.call_args.kwargs
    assert call_kwargs["button"] is not None
    assert call_kwargs["cache_time"] == 60


async def test_answer_inline_query_raises_on_api_failure(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    bot.answer_inline_query.return_value = False

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

    from mitup_bot.utils.entities import FormattedText

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
    bot.send_message.return_value = MagicMock(spec=Message)

    outbox = telegram_api.begin_capture()
    assert await telegram_api.send_message_to_user(user1, "first") is None
    assert await telegram_api.send_message_to_user(user2, "second") is None
    telegram_api.end_capture()

    # Nothing reached the bot while the queue was being built (i.e. inside the transaction).
    bot.send_message.assert_not_called()

    await telegram_api.execute_queued(outbox)

    assert [call.kwargs["chat_id"] for call in bot.send_message.call_args_list] == [100, 200]
    assert [call.kwargs["text"] for call in bot.send_message.call_args_list] == ["first", "second"]


def test_nested_begin_capture_raises(telegram_api: TelegramApi):
    telegram_api.begin_capture()

    with pytest.raises(RuntimeError, match="cannot nest"):
        telegram_api.begin_capture()


async def test_immediate_bypasses_capture_and_restores_it(telegram_api: TelegramApi, bot: AsyncMock):
    user = create_user(id=1, tg_user_id=100)
    bot.send_message.return_value = MagicMock(spec=Message)

    outbox = telegram_api.begin_capture()
    await telegram_api.immediate.send_message_to_user(user, "in-transaction")

    # The lifted call executed right away and did not land on the queue...
    bot.send_message.assert_awaited_once()
    assert outbox.calls == []

    # ...and capture mode is restored afterwards: the next call enqueues again.
    await telegram_api.send_message_to_user(user, "queued")
    bot.send_message.assert_awaited_once()
    assert len(outbox.calls) == 1


async def test_update_meeting_messages_current_first_order_survives_capture(telegram_api: TelegramApi, bot: AsyncMock):
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
    bot.edit_message_text.assert_not_called()

    await telegram_api.execute_queued(outbox)

    first, second = bot.edit_message_text.call_args_list
    assert first.kwargs["message_id"] == 501  # the current message is still edited first
    assert second.kwargs["inline_message_id"] == "inline_other"


async def test_capture_snapshots_rendered_payload_at_enqueue(telegram_api: TelegramApi, bot: AsyncMock):
    meeting, msg = make_bot_chat_meeting()  # title "Test Meeting"

    outbox = telegram_api.begin_capture()
    await telegram_api.update_meeting_messages(meeting=meeting, current_message=msg)
    # The handler keeps mutating after the fan-out call — still inside the transaction. The
    # queued payload was rendered at enqueue time and must not pick this up.
    meeting.title = "Renamed after enqueue"
    telegram_api.end_capture()

    await telegram_api.execute_queued(outbox)

    text = bot.edit_message_text.call_args.kwargs["text"]
    assert "Test Meeting" in text
    assert "Renamed after enqueue" not in text


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
    [{"on_success": [MagicMock()]}, {"on_error": [MagicMock()]}],
    ids=["on_success", "on_error"],
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
async def test_execute_queued_connectivity_error_stops_drain(
    telegram_api: TelegramApi, connectivity_error: NetworkError
):
    executed: list[str] = []

    async def boom():
        raise connectivity_error

    async def never():
        executed.append("never")

    outbox = ApiOutbox(calls=[QueuedApiCall("send_message", boom), QueuedApiCall("send_message", never)])

    # Every remaining call would fail the same way — the drain aborts and the error surfaces
    # to the global error handler.
    with pytest.raises(type(connectivity_error)):
        await telegram_api.execute_queued(outbox)
    assert executed == []


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

    bot.send_message.side_effect = [side_effect, MagicMock(spec=Message)]
    await telegram_api.execute_queued(outbox)
    await api_metrics_client.flush()

    # Recorded for the decorator's reconcile transaction — NOT transitioned inline, since the
    # handler's session is gone by drain time...
    assert outbox.inactive_tg_user_ids == [100]
    assert user1.status is UserStatus.MEMBER
    # ...and an unreachable user is expected churn, not a fault; the drain continued.
    assert bot.send_message.await_count == 2
    api_metrics.assert_not_emitted(name=MetricKey.FAULT)
    api_metrics.assert_not_emitted(name=MetricKey.INACTIVE_USER_SET)


async def test_execute_queued_records_dead_message_for_reconcile(
    telegram_api: TelegramApi, bot: AsyncMock, api_metrics: MetricAssertions, api_metrics_client: MetricsClient
):
    meeting, msg = make_bot_chat_meeting()

    outbox = telegram_api.begin_capture()
    await telegram_api.update_meeting_messages(meeting=meeting, current_message=msg)
    telegram_api.end_capture()

    bot.edit_message_text.side_effect = BadRequest("Message to edit not found")
    await telegram_api.execute_queued(outbox)
    await api_metrics_client.flush()

    assert outbox.dead_message_ids == [1]  # msg.id — the reconcile transaction deletes the row
    api_metrics.assert_emitted(name=MetricKey.MESSAGE_DELETED, times=1)
    api_metrics.assert_not_emitted(name=MetricKey.FAULT)


async def test_execute_queued_records_dead_message_on_forbidden(
    telegram_api: TelegramApi, bot: AsyncMock, api_metrics: MetricAssertions, api_metrics_client: MetricsClient
):
    meeting, msg = make_bot_chat_meeting()

    outbox = telegram_api.begin_capture()
    await telegram_api.update_meeting_messages(meeting=meeting, current_message=msg)
    telegram_api.end_capture()

    bot.edit_message_text.side_effect = Forbidden("Forbidden: user is deactivated")
    await telegram_api.execute_queued(outbox)
    await api_metrics_client.flush()

    assert outbox.dead_message_ids == [1]  # msg.id — the reconcile transaction deletes the row
    api_metrics.assert_emitted(name=MetricKey.MESSAGE_DELETED, times=1)
    api_metrics.assert_not_emitted(name=MetricKey.FAULT)


async def test_execute_queued_message_without_db_id_gets_no_reconcile_entry(telegram_api: TelegramApi, bot: AsyncMock):
    meeting, _ = make_bot_chat_meeting()
    # A row constructed during this update and never flushed: its id is still None.
    msg = MessageModel(inline_message_id=None, chat_id=100, message_id=556, meetup_id=10)

    outbox = telegram_api.begin_capture()
    await telegram_api.update_meeting_messages(meeting=meeting, current_message=msg)
    telegram_api.end_capture()

    bot.edit_message_text.side_effect = BadRequest("Message to edit not found")
    await telegram_api.execute_queued(outbox)

    assert outbox.dead_message_ids == []


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

    # A Telegram failure must never propagate to the caller (handler / job / render).
    await getattr(telegram_api, method_name)(chat_id=-100, tg_user_id=555)


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
# BotAdapter.with_time_metric
# ---------------------------------------------------------------------------


async def test_bot_adapter_time_metric_success_emits_time_and_fault_zero(
    adapter: BotAdapter, api_metrics: MetricAssertions, api_metrics_client: MetricsClient
):
    with adapter.with_time_metric(TELEMGRAM_API_TIME_PREFIX):
        pass

    await api_metrics_client.flush()

    api_metrics.assert_emitted(
        name="TelegramApiTime",
        value=AnyFloat(),
        unit=MetricUnit.MILLISECONDS,
        properties={},
        properties_exact=True,
    )
    api_metrics.assert_emitted(name="TelegramApiFault", value=0)


async def test_bot_adapter_time_metric_emits_even_when_the_call_raises(
    adapter: BotAdapter, api_metrics: MetricAssertions, api_metrics_client: MetricsClient
):
    with pytest.raises(TimedOut):
        with adapter.with_time_metric(TELEMGRAM_API_TIME_PREFIX):
            raise TimedOut()

    await api_metrics_client.flush()

    api_metrics.assert_emitted(
        name="TelegramApiTime",
        value=AnyFloat(),
        unit=MetricUnit.MILLISECONDS,
        properties={},
        properties_exact=True,
    )
    api_metrics.assert_emitted(name="TelegramApiFault", value=1)


async def test_bot_adapter_time_metric_keeps_each_call_outcome_on_its_own_fault_sample(
    adapter: BotAdapter, api_metrics: MetricAssertions, api_metrics_client: MetricsClient
):
    # A flush window batches every dimensionless emission into one EMF document, where values
    # accumulate into an array but properties overwrite. The per-call outcome must therefore live
    # on the fault series — one 0/1 sample per call — and never on a property of the timing metric.
    with adapter.with_time_metric(TELEMGRAM_API_TIME_PREFIX):
        pass
    with pytest.raises(TimedOut):
        with adapter.with_time_metric(TELEMGRAM_API_TIME_PREFIX):
            raise TimedOut()

    await api_metrics_client.flush()

    api_metrics.assert_emitted(name="TelegramApiFault", value=0, times=1)
    api_metrics.assert_emitted(name="TelegramApiFault", value=1, times=1)
    api_metrics.assert_emitted(
        name="TelegramApiTime",
        unit=MetricUnit.MILLISECONDS,
        properties={},
        properties_exact=True,
        times=2,
    )


# ---------------------------------------------------------------------------
# Failure-path payload logging
# ---------------------------------------------------------------------------


async def test_send_message_failure_logs_the_attempted_payload(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.effective_chat.id = 42
    view = MitupView(description="hello there", keyboard=[[ButtonConfig(text="Go", callback_data="cb")]])
    bot.send_message.side_effect = BadRequest("Chat not found")

    with capture_logs() as logs:
        with pytest.raises(BadRequest):
            await telegram_api.send_message(update, view)

    failure_logs = [entry for entry in logs if entry["event"] == "Telegram call failed"]
    assert len(failure_logs) == 1
    assert failure_logs[0]["telegram_call"] == "send_message"
    payload = failure_logs[0]["payload"]
    assert payload["chat_id"] == 42
    assert payload["text"] == "hello there"
    assert view.markup is not None
    assert payload["markup"] == view.markup.to_dict()


async def test_blocked_user_send_is_not_logged_as_telegram_call_failure(telegram_api: TelegramApi, bot: AsyncMock):
    bot.send_message.side_effect = Forbidden("blocked")
    user = create_user(1)

    with capture_logs() as logs:
        with pytest.raises(InactiveUserInteraction):
            await telegram_api.send_message_to_user(user, "hi")

    assert not [entry for entry in logs if entry["event"] == "Telegram call failed"]


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
