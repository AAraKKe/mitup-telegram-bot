"""Tests for the real TelegramApi class with a mocked bot."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import Session
from telegram import Message, Update
from telegram.error import BadRequest, Forbidden
from telegram.ext import ExtBot

from mitup_bot.api_wrapper import TELEMGRAM_API_TIME_PREFIX, BotAdapter, TelegramApi, build_api, handle_edit_errors
from mitup_bot.exceptions import AnswerInlineQueryError, InactiveUserInteraction, NoMessageAvailable
from mitup_bot.models import Meetup
from mitup_bot.models import Message as MessageModel
from mitup_bot.protocols import ContextOrBotAdapter
from mitup_bot.views import InlineResultsButton, MitupInlineView, MitupView
from tests.helpers.fixtures import create_joined_link, create_meetup, create_message, create_user


@pytest.fixture
def bot() -> AsyncMock:
    return AsyncMock(spec=ExtBot)


@pytest.fixture
def adapter(bot: AsyncMock) -> BotAdapter:
    return BotAdapter(bot=bot)


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


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


async def test_send_message_with_string_view(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.effective_chat.id = 42
    sentinel = MagicMock(spec=Message)
    bot.send_message.return_value = sentinel

    result = await telegram_api.send_message(update, "hello")

    bot.send_message.assert_awaited_once_with(chat_id=42, text="hello", entities=None, reply_markup=None)
    assert result is sentinel


async def test_send_message_with_mitup_view(telegram_api: TelegramApi, bot: AsyncMock):
    update = MagicMock(spec=Update)
    update.effective_chat.id = 99
    view = MitupView(description="view text", keyboard=[])
    sentinel = MagicMock(spec=Message)
    bot.send_message.return_value = sentinel

    result = await telegram_api.send_message(update, view)

    bot.send_message.assert_awaited_once_with(chat_id=99, text="view text", entities=None, reply_markup=view.markup)
    assert result is sentinel


# ---------------------------------------------------------------------------
# handle_edit_errors
# ---------------------------------------------------------------------------


def test_handle_edit_errors_ignores_message_not_modified(adapter: BotAdapter):
    with handle_edit_errors(cast(ContextOrBotAdapter, adapter)):
        raise BadRequest("Message is not modified: specified new message content and reply markup are exactly the same")


@pytest.mark.parametrize(
    "error_message",
    [
        "Message_id_invalid",
        "Message to edit not found",
    ],
    ids=["message_id_invalid", "message_to_edit_not_found"],
)
def test_handle_edit_errors_deletes_message_on_not_found(adapter: BotAdapter, error_message: str):
    session = MagicMock(spec=Session)
    message = create_message(id=5, inline_message_id="msg_5")
    with handle_edit_errors(cast(ContextOrBotAdapter, adapter), message=message, session=session):
        raise BadRequest(error_message)
    session.delete.assert_called_once_with(message)


def test_handle_edit_errors_no_delete_when_session_or_message_missing(adapter: BotAdapter):
    # No session or message — should still not raise, just emit metric
    with handle_edit_errors(cast(ContextOrBotAdapter, adapter)):
        raise BadRequest("Message_id_invalid")


def test_handle_edit_errors_reraises_other_bad_request(adapter: BotAdapter):
    with pytest.raises(BadRequest, match="Something else"):
        with handle_edit_errors(cast(ContextOrBotAdapter, adapter)):
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

    assert exc_info.value.user_id == tg_user_id


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


async def test_send_messages_to_users_inactive_user_marked_inactive(telegram_api: TelegramApi, bot: AsyncMock):
    user1 = create_user(id=1, tg_user_id=100)
    user2 = create_user(id=2, tg_user_id=200)
    bot.send_message.side_effect = [
        Forbidden("Forbidden: bot was blocked by the user"),
        MagicMock(spec=Message),
    ]

    await telegram_api.send_messages_to_users([user1, user2], ["msg1", "msg2"])

    assert user1.is_active is False
    assert user2.is_active is True


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


def _make_bot_chat_meeting() -> tuple[Meetup, MessageModel]:
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


def _make_inline_meeting() -> tuple[Meetup, MessageModel]:
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
    meeting, msg = _make_bot_chat_meeting()
    session = MagicMock(spec=Session)

    await telegram_api.update_single_meeting_message(msg, session, meeting)

    bot.edit_message_text.assert_awaited_once()
    call_kwargs = bot.edit_message_text.call_args.kwargs
    assert call_kwargs["chat_id"] == msg.chat_id
    assert call_kwargs["message_id"] == msg.message_id
    assert call_kwargs["reply_markup"] is not None
    session.add.assert_called_once_with(msg)


async def test_update_single_meeting_message_inline(telegram_api: TelegramApi, bot: AsyncMock):
    meeting, msg = _make_inline_meeting()
    session = MagicMock(spec=Session)

    await telegram_api.update_single_meeting_message(msg, session, meeting)

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
    meeting, msg = _make_bot_chat_meeting()
    session = MagicMock(spec=Session)

    await telegram_api.update_single_meeting_message(
        msg, session, meeting, was_deleted=was_deleted, has_finished=has_finished
    )

    call_kwargs = bot.edit_message_text.call_args.kwargs
    assert call_kwargs["reply_markup"] is None
    assert expected_text_fragment in call_kwargs["text"].lower()


async def test_update_single_meeting_message_inline_vs_bot_chat_different_views(
    telegram_api: TelegramApi, bot: AsyncMock
):
    meeting_bot, msg_bot = _make_bot_chat_meeting()
    meeting_inline, msg_inline = _make_inline_meeting()
    session = MagicMock(spec=Session)

    await telegram_api.update_single_meeting_message(msg_bot, session, meeting_bot)
    bot_chat_text = bot.edit_message_text.call_args.kwargs["text"]

    bot.reset_mock()

    await telegram_api.update_single_meeting_message(msg_inline, session, meeting_inline)
    inline_text = bot.edit_message_text.call_args.kwargs["text"]

    assert isinstance(bot_chat_text, str)
    assert isinstance(inline_text, str)


async def test_update_single_meeting_message_not_modified_is_swallowed(telegram_api: TelegramApi, bot: AsyncMock):
    meeting, msg = _make_bot_chat_meeting()
    session = MagicMock(spec=Session)
    bot.edit_message_text.side_effect = BadRequest("Message is not modified: ...")

    # Verifies the wiring: update_single_meeting_message routes errors through handle_edit_errors
    await telegram_api.update_single_meeting_message(msg, session, meeting)


async def test_update_single_meeting_message_not_found_deletes_message(telegram_api: TelegramApi, bot: AsyncMock):
    meeting, msg = _make_bot_chat_meeting()
    session = MagicMock(spec=Session)
    bot.edit_message_text.side_effect = BadRequest("Message to edit not found")

    await telegram_api.update_single_meeting_message(msg, session, meeting)

    session.delete.assert_called_once_with(msg)


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
    session = MagicMock(spec=Session)

    await telegram_api.update_meeting_messages(
        session=session,
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
    session = MagicMock(spec=Session)

    await telegram_api.update_meeting_messages(
        session=session,
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
    session = MagicMock(spec=Session)

    await telegram_api.update_meeting_messages(
        session=session,
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
    session = MagicMock(spec=Session)

    await telegram_api.update_meeting_messages(
        session=session,
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
    adapter = BotAdapter(bot=bot)
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


@pytest.mark.parametrize(
    "text",
    ["short text", "x" * 201],
    ids=["short_text", "long_text"],
)
async def test_answer_callback_query(telegram_api: TelegramApi, bot: AsyncMock, text: str):
    update = MagicMock(spec=Update)

    await telegram_api.answer_callback_query(update, text, show_alert=False)

    bot.answer_callback_query.assert_awaited_once_with(update.callback_query.id, text=text, show_alert=False)
