import datetime as dt
from typing import cast

import pytest
from telegram import Chat, Message, MessageEntity, Update
from telegram import User as TelegramUser

from mitup_bot.custom_context import ContextId
from mitup_bot.handlers.meeting.create_meeting import ValidTitleFilter, callback_query_create_meeting
from mitup_bot.handlers.meeting.enums import ConversationMeetingState, MeetingHandlerId
from mitup_bot.models import Meetup, User
from mitup_bot.utils import MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import DateTimeMessageEntity
from mitup_bot.views import factory as views_factory
from tests.helpers import (
    ConversationStep,
    ConversationTester,
    MockDbSession,
    StubMitupContext,
    call_handler,
)
from tests.helpers.constants import DEFAULT_CHAT_ID, DEFAULT_MESSAGE_ID, DEFAULT_TEST_DATE, DEFAULT_TG_USER_PARAMS

# ---------------------------------------------------------------------------
# Helpers for entity-based updates (not expressible via UpdateRequest)
# ---------------------------------------------------------------------------


def _make_message_update(text: str, entities: list[MessageEntity] | None = None) -> Update:
    """Build an Update carrying a Message with the given text and entities."""
    tg_user = TelegramUser(**DEFAULT_TG_USER_PARAMS)
    tg_chat = Chat(id=DEFAULT_CHAT_ID, type="private")
    message = Message(
        message_id=DEFAULT_MESSAGE_ID,
        date=DEFAULT_TEST_DATE,
        chat=tg_chat,
        from_user=tg_user,
        text=text,
        entities=entities,
    )
    return Update(update_id=DEFAULT_MESSAGE_ID, message=message)


# ---------------------------------------------------------------------------
# create_meeting conversation — entry and title steps
# ---------------------------------------------------------------------------


async def test_meeting_creation_successful(
    user_with_settings: User,
    mock_session: MockDbSession,
    conversation: ConversationTester,
):
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    steps = [
        ConversationStep.callback(cb.CREATE_MEETING, expected_state=ConversationMeetingState.TITLE),
        ConversationStep.message("My test meeting"),
    ]

    result = await conversation.run(handler_id=MeetingHandlerId.CREATE_MEETING_CONVERSATION, steps=steps)

    assert len(mock_session.objects_added) == 1
    new_meeting: Meetup = cast(Meetup, mock_session.objects_added[0])
    assert new_meeting.title == "My test meeting"
    assert new_meeting.datetime is None  # plain-text title carries no date entity

    title_step = result.get_step(1)
    message = MeetingMessages.CREATED_SUCCESS.get(title=new_meeting.title, lang=user_with_settings.lang)
    view = new_meeting.edit_view.with_context(message)
    title_step.context.api.assert_send_message_called(title_step.context.get_update(), view)


async def test_meeting_creation_cancelled(
    user_with_settings: User,
    mock_session: MockDbSession,
    conversation: ConversationTester,
):
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    steps = [
        ConversationStep.callback(cb.CREATE_MEETING, expected_state=ConversationMeetingState.TITLE),
        ConversationStep.callback(cb.CANCEL_CREATE_MEETING),
    ]

    result = await conversation.run(handler_id=MeetingHandlerId.CREATE_MEETING_CONVERSATION, steps=steps)

    assert len(mock_session.objects_added) == 0

    cancel_step = result.get_step(1)
    cancel_step.context.api.assert_edit_message_called(
        cancel_step.context.get_update(),
        views_factory.main_menu_view(lang=user_with_settings.lang),
        times=1,
    )


async def test_callback_query_create_meeting_stores_on_exit(
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    mock_session: MockDbSession,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    await callback_query_create_meeting(update, context)

    assert context.user_data is not None
    on_exit = context.user_data.registry[ContextId.CREATE_MEETING].on_exit
    assert on_exit is not None
    assert on_exit.message == MeetingMessages.CREATE_MEETING_ON_EXIT.get(lang=user_with_settings.lang)
    assert on_exit.cancel_callback == cb.CANCEL_CREATE_MEETING


# ---------------------------------------------------------------------------
# create_meeting_message_handler — date_time entity: strips title, sets datetime
#
# UpdateRequest does not expose a message entity field, so the title update
# is built manually and the handler is invoked directly via its individual ID.
# ---------------------------------------------------------------------------


async def test_meeting_creation_with_date_entity_strips_title_and_sets_datetime(
    user_with_settings: User,
    mock_session: MockDbSession,
    conversation: ConversationTester,
):
    """A single date_time entity strips that span from the title and sets meetup.datetime."""
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    # The entry step uses ConversationTester. The title step must be invoked via
    # call_handler with a manually built Update because UpdateRequest does not expose
    # a message entities field — the date_time entity is required to exercise this path.
    entry_steps = [
        ConversationStep.callback(cb.CREATE_MEETING, expected_state=ConversationMeetingState.TITLE),
    ]
    await conversation.run(handler_id=MeetingHandlerId.CREATE_MEETING_CONVERSATION, steps=entry_steps)

    unix_ts = 1_700_000_000
    # Text: "Board meeting tomorrow" -- "tomorrow" starts at offset 14, length 8
    date_entity = DateTimeMessageEntity(offset=14, length=8, unix_time=unix_ts)
    title_update = _make_message_update("Board meeting tomorrow", entities=[date_entity])

    context, _ = await call_handler(
        MeetingHandlerId.CREATE_MEETING_CONVERSATION, update=title_update, app=conversation.app
    )

    assert len(mock_session.objects_added) == 1
    new_meeting: Meetup = cast(Meetup, mock_session.objects_added[0])
    # "tomorrow" (offset 14, length 8) is stripped and surrounding whitespace removed
    assert new_meeting.title == "Board meeting"
    # datetime derived from unix_time
    expected_dt = dt.datetime.fromtimestamp(unix_ts, tz=dt.UTC)
    assert new_meeting.datetime == expected_dt


# ---------------------------------------------------------------------------
# create_meeting_invalid_title_message_handler — unsupported entity fires fallback
#
# PTB does not persist conversation state for fallback handlers, so these
# tests invoke the fallback handler directly via its own ID. This lets us
# assert both the return value (TITLE) and the API call without entering the
# full conversation.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_update",
    [
        _make_message_update(
            "tomorrow later",
            entities=[
                DateTimeMessageEntity(offset=0, length=8, unix_time=1_700_000_000),
                DateTimeMessageEntity(offset=9, length=5, unix_time=1_700_001_000),
            ],
        ),
    ],
    ids=["multiple_date_entities"],
)
async def test_invalid_title_fires_fallback_handler(
    user_with_settings: User,
    mock_session: MockDbSession,
    app,
    bad_update: Update,
):
    """Text with unsupported entities triggers the fallback handler, keeping TITLE state."""
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    # Call the fallback handler directly. PTB does not persist state from fallback
    # handlers when going through the ConversationHandler, so using the individual
    # handler ID lets us assert the actual return value.
    context, state = await call_handler(
        MeetingHandlerId.CREATE_MEETING_INVALID_TITLE_MESSAGE, update=bad_update, app=app
    )

    # No meeting created
    assert len(mock_session.objects_added) == 0
    # Handler returns TITLE to signal the conversation should remain in TITLE state
    assert state == ConversationMeetingState.TITLE
    # Error view sent to the user
    error_msg = MeetingMessages.TITLE_WITH_UNSUPPORTED_ENTITY.get(lang=user_with_settings.lang)
    error_view = views_factory.create_meeting_view(lang=user_with_settings.lang, message=error_msg)
    context.api.assert_send_message_called(bad_update, error_view)


# ---------------------------------------------------------------------------
# ValidTitleFilter unit tests (no DB, no session)
# ---------------------------------------------------------------------------


def test_valid_title_filter_plain_text_passes():
    f = ValidTitleFilter()
    update = _make_message_update("Board meeting")
    assert update.effective_message is not None
    assert f.filter(update.effective_message) is True


def test_valid_title_filter_no_text_rejects():
    f = ValidTitleFilter()
    tg_user = TelegramUser(**DEFAULT_TG_USER_PARAMS)
    tg_chat = Chat(id=DEFAULT_CHAT_ID, type="private")
    message = Message(
        message_id=DEFAULT_MESSAGE_ID,
        date=DEFAULT_TEST_DATE,
        chat=tg_chat,
        from_user=tg_user,
        text=None,
    )
    assert f.filter(message) is False


def test_valid_title_filter_command_rejects():
    # BOT_COMMAND entity at offset 0 means it is a /command — must be rejected
    f = ValidTitleFilter()
    update = _make_message_update(
        "/start",
        entities=[MessageEntity(type=MessageEntity.BOT_COMMAND, offset=0, length=6)],
    )
    assert update.effective_message is not None
    assert f.filter(update.effective_message) is False


def test_valid_title_filter_one_date_entity_passes():
    f = ValidTitleFilter()
    date_entity = DateTimeMessageEntity(offset=0, length=8, unix_time=1_700_000_000)
    update = _make_message_update("tomorrow", entities=[date_entity])
    assert update.effective_message is not None
    assert f.filter(update.effective_message) is True


def test_valid_title_filter_date_entity_with_other_entities_passes():
    # One date_time entity alongside a bold entity is still valid
    f = ValidTitleFilter()
    date_entity = DateTimeMessageEntity(offset=0, length=8, unix_time=1_700_000_000)
    bold_entity = MessageEntity(type=MessageEntity.BOLD, offset=9, length=5)
    update = _make_message_update("tomorrow board", entities=[date_entity, bold_entity])
    assert update.effective_message is not None
    assert f.filter(update.effective_message) is True


def test_valid_title_filter_non_command_entity_passes():
    # A bold entity without any date_time entity is a valid title
    f = ValidTitleFilter()
    bold_entity = MessageEntity(type=MessageEntity.BOLD, offset=0, length=5)
    update = _make_message_update("hello", entities=[bold_entity])
    assert update.effective_message is not None
    assert f.filter(update.effective_message) is True


def test_valid_title_filter_multiple_date_entities_rejects():
    # Two date_time entities must be rejected (sum > 1)
    f = ValidTitleFilter()
    e1 = DateTimeMessageEntity(offset=0, length=8, unix_time=1_700_000_000)
    e2 = DateTimeMessageEntity(offset=9, length=5, unix_time=1_700_001_000)
    update = _make_message_update("tomorrow later", entities=[e1, e2])
    assert update.effective_message is not None
    assert f.filter(update.effective_message) is False
