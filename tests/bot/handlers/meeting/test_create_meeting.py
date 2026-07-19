import datetime as dt
from typing import cast

import pytest
from freezegun import freeze_time
from telegram import Chat, Message, MessageEntity, Update
from telegram import User as TelegramUser
from telegram.ext import Application, ConversationHandler

from mitup_bot import supporter
from mitup_bot.config import LimitsConfig
from mitup_bot.custom_context import ContextId
from mitup_bot.handlers.meeting.create_meeting import ValidTitleFilter, callback_query_create_meeting
from mitup_bot.handlers.meeting.enums import ConversationMeetingState, MeetingHandlerId
from mitup_bot.models import Meetup, User
from mitup_bot.monitoring import MetricsClient
from mitup_bot.utils import MeetingCreationMessages, SupporterMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import RenderContext
from mitup_bot.views import factory as views_factory
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.collaborate import supporter_upsell_view
from tests.helpers import (
    ConversationStep,
    ConversationTester,
    HandlerContext,
    MockDbSession,
    StubMitupContext,
    UpdateRequest,
    call_handler,
)
from tests.helpers.constants import DEFAULT_CHAT_ID, DEFAULT_MESSAGE_ID, DEFAULT_TEST_DATE, DEFAULT_TG_USER_PARAMS

# ---------------------------------------------------------------------------
# Helpers for entity-based updates (not expressible via UpdateRequest)
# ---------------------------------------------------------------------------


def make_message_update(text: str, entities: list[MessageEntity] | None = None) -> Update:
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
    message = MeetingCreationMessages.SUCCESS.get(title=new_meeting.title, lang=user_with_settings.lang)
    view = meeting_views.edit_view(new_meeting).with_context(message)
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
        views_factory.main_menu_view(RenderContext(lang=user_with_settings.lang)),
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
    assert on_exit.message == MeetingCreationMessages.ON_EXIT.get(lang=user_with_settings.lang)
    assert on_exit.cancel_callback == cb.CANCEL_CREATE_MEETING


# ---------------------------------------------------------------------------
# create_meeting_message_handler — date_time entity: strips title, sets datetime
#
# UpdateRequest does not expose a message entity field, so the title update
# is built manually and the handler is invoked directly via its individual ID.
# ---------------------------------------------------------------------------


async def test_meeting_creation_with_date_entity_preserves_full_title_and_sets_datetime(
    user_with_settings: User,
    mock_session: MockDbSession,
    conversation: ConversationTester,
    metrics_client: MetricsClient,
):
    """A single date_time entity no longer strips the title — the full user input is stored.
    The meetup.datetime field is still derived from the entity's unix_time.
    """
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    # The entry step uses ConversationTester. The title step must be invoked via
    # call_handler with a manually built Update because UpdateRequest does not expose
    # a message entities field — the date_time entity is required to exercise this path.
    entry_steps = [
        ConversationStep.callback(cb.CREATE_MEETING, expected_state=ConversationMeetingState.TITLE),
    ]
    await conversation.run(handler_id=MeetingHandlerId.CREATE_MEETING_CONVERSATION, steps=entry_steps)

    unix_ts = 1_700_000_000
    unix_dt = dt.datetime.fromtimestamp(unix_ts, tz=dt.UTC)
    # Text: "Board meeting tomorrow" -- "tomorrow" starts at offset 14, length 8
    date_entity = MessageEntity(type=MessageEntity.DATE_TIME, offset=14, length=8, unix_time=unix_dt)
    title_update = make_message_update("Board meeting tomorrow", entities=[date_entity])

    ctx = HandlerContext(update=title_update, app=conversation.app, metrics_client=metrics_client)
    context, _ = await call_handler(
        MeetingHandlerId.CREATE_MEETING_CONVERSATION,
        handler_context=ctx,
    )

    assert len(mock_session.objects_added) == 1
    new_meeting: Meetup = cast(Meetup, mock_session.objects_added[0])
    # Full user input is preserved — entity span is NOT stripped from the title.
    assert new_meeting.title == "Board meeting tomorrow"
    # datetime is still derived from the entity's unix_time.
    expected_dt = dt.datetime.fromtimestamp(unix_ts, tz=dt.UTC)
    assert new_meeting.datetime == expected_dt


async def test_meeting_creation_with_single_word_datetime_title_preserves_title(
    user_with_settings: User,
    mock_session: MockDbSession,
    conversation: ConversationTester,
    metrics_client: MetricsClient,
):
    """A title that is entirely a single date_time entity (e.g. 'today') is stored as-is,
    not reduced to an empty string.
    """
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    entry_steps = [
        ConversationStep.callback(cb.CREATE_MEETING, expected_state=ConversationMeetingState.TITLE),
    ]
    await conversation.run(handler_id=MeetingHandlerId.CREATE_MEETING_CONVERSATION, steps=entry_steps)

    unix_ts = 1_700_000_000
    unix_dt = dt.datetime.fromtimestamp(unix_ts, tz=dt.UTC)
    # "today" spans the entire text: offset=0, length=5
    date_entity = MessageEntity(type=MessageEntity.DATE_TIME, offset=0, length=5, unix_time=unix_dt)
    title_update = make_message_update("today", entities=[date_entity])

    ctx = HandlerContext(update=title_update, app=conversation.app, metrics_client=metrics_client)
    context, _ = await call_handler(
        MeetingHandlerId.CREATE_MEETING_CONVERSATION,
        handler_context=ctx,
    )

    assert len(mock_session.objects_added) == 1
    new_meeting: Meetup = cast(Meetup, mock_session.objects_added[0])
    # Title must be "today", not "" (regression guard against the old strip behaviour).
    assert new_meeting.title == "today"
    # datetime is derived from the entity even though the title was not modified.
    expected_dt = dt.datetime.fromtimestamp(unix_ts, tz=dt.UTC)
    assert new_meeting.datetime == expected_dt


# ---------------------------------------------------------------------------
# create_meeting_message_handler — date_entity without unix_time: title stripped, datetime not set
# ---------------------------------------------------------------------------


async def test_meeting_creation_with_date_entity_without_unix_time_preserves_title_and_leaves_datetime_none(
    user_with_settings: User,
    mock_session: MockDbSession,
    app: Application,
    metrics_client: MetricsClient,
):
    """A date_time entity whose to_dict() lacks 'unix_time' preserves the full title and does NOT
    set meetup.datetime (unix_time is None → the timestamp branch is skipped)."""
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    # Build a plain MessageEntity with type="date_time" that has no unix_time in to_dict().
    # MessageEntity.to_dict() only serialises known fields, so a bare date_time entity
    # without the unix_time attribute will not include it in the dict.
    date_entity = MessageEntity(type="date_time", offset=14, length=8)
    title_update = make_message_update("Board meeting tomorrow", entities=[date_entity])

    ctx = HandlerContext(update=title_update, app=app, metrics_client=metrics_client)
    context, _ = await call_handler(
        MeetingHandlerId.CREATE_MEETING_TITLE_MESSAGE,
        handler_context=ctx,
    )

    assert len(mock_session.objects_added) == 1
    new_meeting: Meetup = cast(Meetup, mock_session.objects_added[0])
    # Full user input is preserved — the entity span is NOT stripped from the title.
    assert new_meeting.title == "Board meeting tomorrow"
    # datetime was NOT set because unix_time was absent in to_dict().
    assert new_meeting.datetime is None


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
        make_message_update(
            "tomorrow later",
            entities=[
                MessageEntity(
                    type=MessageEntity.DATE_TIME,
                    offset=0,
                    length=8,
                    unix_time=dt.datetime.fromtimestamp(1_700_000_000, tz=dt.UTC),
                ),
                MessageEntity(
                    type=MessageEntity.DATE_TIME,
                    offset=9,
                    length=5,
                    unix_time=dt.datetime.fromtimestamp(1_700_001_000, tz=dt.UTC),
                ),
            ],
        ),
    ],
    ids=["multiple_date_entities"],
)
async def test_invalid_title_fires_fallback_handler(
    user_with_settings: User,
    mock_session: MockDbSession,
    app: Application,
    bad_update: Update,
    metrics_client: MetricsClient,
):
    """Text with unsupported entities triggers the fallback handler, keeping TITLE state."""
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    # Call the fallback handler directly. PTB does not persist state from fallback
    # handlers when going through the ConversationHandler, so using the individual
    # handler ID lets us assert the actual return value.
    ctx = HandlerContext(update=bad_update, app=app, metrics_client=metrics_client)
    context, state = await call_handler(
        MeetingHandlerId.CREATE_MEETING_INVALID_TITLE_MESSAGE,
        handler_context=ctx,
    )

    # No meeting created
    assert len(mock_session.objects_added) == 0
    # Handler returns TITLE to signal the conversation should remain in TITLE state
    assert state == ConversationMeetingState.TITLE
    # Error view sent to the user
    error_msg = MeetingCreationMessages.INVALID_TITLE_ENTITY.get(lang=user_with_settings.lang)
    error_view = views_factory.create_meeting_view(RenderContext(lang=user_with_settings.lang), message=error_msg)
    context.api.assert_send_message_called(bad_update, error_view)


# ---------------------------------------------------------------------------
# ValidTitleFilter unit tests (no DB, no session)
# ---------------------------------------------------------------------------


def test_valid_title_filter_plain_text_passes():
    title_filter = ValidTitleFilter()
    update = make_message_update("Board meeting")
    assert update.effective_message is not None
    assert title_filter.filter(update.effective_message) is True


def test_valid_title_filter_no_text_rejects():
    title_filter = ValidTitleFilter()
    tg_user = TelegramUser(**DEFAULT_TG_USER_PARAMS)
    tg_chat = Chat(id=DEFAULT_CHAT_ID, type="private")
    message = Message(
        message_id=DEFAULT_MESSAGE_ID,
        date=DEFAULT_TEST_DATE,
        chat=tg_chat,
        from_user=tg_user,
        text=None,
    )
    assert title_filter.filter(message) is False


def test_valid_title_filter_command_rejects():
    # BOT_COMMAND entity at offset 0 means it is a /command — must be rejected
    title_filter = ValidTitleFilter()
    update = make_message_update(
        "/start",
        entities=[MessageEntity(type=MessageEntity.BOT_COMMAND, offset=0, length=6)],
    )
    assert update.effective_message is not None
    assert title_filter.filter(update.effective_message) is False


def test_valid_title_filter_one_date_entity_passes():
    title_filter = ValidTitleFilter()
    date_entity = MessageEntity(
        type=MessageEntity.DATE_TIME,
        offset=0,
        length=8,
        unix_time=dt.datetime.fromtimestamp(1_700_000_000, tz=dt.UTC),
    )
    update = make_message_update("tomorrow", entities=[date_entity])
    assert update.effective_message is not None
    assert title_filter.filter(update.effective_message) is True


def test_valid_title_filter_date_entity_with_other_entities_passes():
    # One date_time entity alongside a bold entity is still valid
    title_filter = ValidTitleFilter()
    date_entity = MessageEntity(
        type=MessageEntity.DATE_TIME,
        offset=0,
        length=8,
        unix_time=dt.datetime.fromtimestamp(1_700_000_000, tz=dt.UTC),
    )
    bold_entity = MessageEntity(type=MessageEntity.BOLD, offset=9, length=5)
    update = make_message_update("tomorrow board", entities=[date_entity, bold_entity])
    assert update.effective_message is not None
    assert title_filter.filter(update.effective_message) is True


def test_valid_title_filter_non_command_entity_passes():
    # A bold entity without any date_time entity is a valid title
    title_filter = ValidTitleFilter()
    bold_entity = MessageEntity(type=MessageEntity.BOLD, offset=0, length=5)
    update = make_message_update("hello", entities=[bold_entity])
    assert update.effective_message is not None
    assert title_filter.filter(update.effective_message) is True


def test_valid_title_filter_multiple_date_entities_rejects():
    # Two date_time entities must be rejected (sum > 1)
    title_filter = ValidTitleFilter()
    first_date_entity = MessageEntity(
        type=MessageEntity.DATE_TIME,
        offset=0,
        length=8,
        unix_time=dt.datetime.fromtimestamp(1_700_000_000, tz=dt.UTC),
    )
    second_date_entity = MessageEntity(
        type=MessageEntity.DATE_TIME,
        offset=9,
        length=5,
        unix_time=dt.datetime.fromtimestamp(1_700_001_000, tz=dt.UTC),
    )
    update = make_message_update("tomorrow later", entities=[first_date_entity, second_date_entity])
    assert update.effective_message is not None
    assert title_filter.filter(update.effective_message) is False


# ---------------------------------------------------------------------------
# Free-tier limits: active-meetings cap and scheduling horizon
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.CREATE_MEETING)], indirect=True)
async def test_create_meeting_entry_blocked_at_cap_shows_upsell(
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    mock_session: MockDbSession,
    monkeypatch: pytest.MonkeyPatch,
):
    """The New Meeting button stops at the cap: an alert is shown and the conversation never starts."""
    # The fixture owner already has two active meetings.
    monkeypatch.setattr(supporter.PolicyState, "config", LimitsConfig(free_active_meetings=2))
    mock_session.add_object(user_with_settings, "tg_user_id")

    state = await callback_query_create_meeting(update, context)

    assert state == ConversationHandler.END
    assert context.user_data is not None
    assert ContextId.CREATE_MEETING not in context.user_data.registry  # flow not entered
    context.api.assert_answer_callback_query_called(
        update=update,
        text=SupporterMessages.ACTIVE_MEETINGS_CAP.get_text(lang=user_with_settings.lang, cap=2),
        show_alert=True,
    )


async def test_create_meeting_title_blocked_at_cap_sends_message(
    user_with_settings: User,
    mock_session: MockDbSession,
    app: Application,
    metrics_client: MetricsClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """The title-message path (used by the inline deep link) backstops the cap with a sent message."""
    monkeypatch.setattr(supporter.PolicyState, "config", LimitsConfig(free_active_meetings=2))
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    title_update = make_message_update("My meeting")
    ctx = HandlerContext(update=title_update, app=app, metrics_client=metrics_client)
    context, state = await call_handler(MeetingHandlerId.CREATE_MEETING_TITLE_MESSAGE, handler_context=ctx)

    assert state == ConversationHandler.END
    assert len(mock_session.objects_added) == 0  # no meeting created
    context.api.assert_send_message_called(
        title_update,
        supporter_upsell_view(
            SupporterMessages.ACTIVE_MEETINGS_CAP.get(lang=user_with_settings.lang, cap=2),
            user_with_settings.lang,
        ),
    )


@freeze_time("2025-01-15 12:00:00", tz_offset=0)
async def test_create_meeting_title_date_beyond_horizon_stays_in_title(
    user_with_settings: User,
    mock_session: MockDbSession,
    app: Application,
    metrics_client: MetricsClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """A title-embedded date past the free horizon is rejected, keeping the user in TITLE."""
    monkeypatch.setattr(
        supporter.PolicyState, "config", LimitsConfig(free_active_meetings=5, free_scheduling_horizon_days=31)
    )
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    # 60 days ahead of the frozen now — beyond the 31-day free horizon.
    far_dt = dt.datetime(2025, 3, 16, 12, 0, tzinfo=dt.UTC)
    date_entity = MessageEntity(type=MessageEntity.DATE_TIME, offset=0, length=3, unix_time=far_dt)
    title_update = make_message_update("Ski trip", entities=[date_entity])

    ctx = HandlerContext(update=title_update, app=app, metrics_client=metrics_client)
    context, state = await call_handler(MeetingHandlerId.CREATE_MEETING_TITLE_MESSAGE, handler_context=ctx)

    assert state == ConversationMeetingState.TITLE
    assert len(mock_session.objects_added) == 0  # no meeting created
    context.api.assert_send_message_called(
        title_update,
        supporter_upsell_view(
            SupporterMessages.SCHEDULING_HORIZON_TITLE.get_text(lang=user_with_settings.lang, days=31),
            user_with_settings.lang,
        ),
    )


@freeze_time("2025-01-15 12:00:00", tz_offset=0)
async def test_create_meeting_title_date_within_horizon_is_created(
    user_with_settings: User,
    mock_session: MockDbSession,
    app: Application,
    metrics_client: MetricsClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """A title-embedded date exactly on the horizon boundary is accepted and the meeting is created."""
    monkeypatch.setattr(
        supporter.PolicyState, "config", LimitsConfig(free_active_meetings=5, free_scheduling_horizon_days=31)
    )
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    # Exactly 31 days ahead (Europe/Madrid date) — the boundary is allowed.
    on_horizon = dt.datetime(2025, 2, 15, 12, 0, tzinfo=dt.UTC)
    date_entity = MessageEntity(type=MessageEntity.DATE_TIME, offset=0, length=3, unix_time=on_horizon)
    title_update = make_message_update("Gig", entities=[date_entity])

    ctx = HandlerContext(update=title_update, app=app, metrics_client=metrics_client)
    _, state = await call_handler(MeetingHandlerId.CREATE_MEETING_TITLE_MESSAGE, handler_context=ctx)

    assert state == ConversationHandler.END
    assert len(mock_session.objects_added) == 1
    new_meeting: Meetup = cast(Meetup, mock_session.objects_added[0])
    assert new_meeting.datetime == on_horizon
