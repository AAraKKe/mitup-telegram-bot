import pytest
from telegram import Update

import mitup_bot.utils.callbacks as cb
from mitup_bot.handlers.meeting.enums import MeetingHandlerId
from mitup_bot.models import JoinedUsers, Settings, User, utils
from mitup_bot.models.joined_users import JOINED_USERS_UNIQUE_CONSTRAINT
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import Feature, MetricKey, MetricUnit
from mitup_bot.utils.messages import MeetingDisplayMessages, MeetingJoinMessages
from mitup_bot.views import MitupView
from tests.helpers import (
    AnyFloat,
    HandlerContext,
    MockDbSession,
    UpdateRequest,
    assert_locked_meetup_select,
    call_handler,
    create_meetup,
    create_message,
    integrity_error,
)
from tests.helpers.monitoring import MetricAssertions


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.JOIN.with_id(1))], indirect=True)
async def test_existing_user_joins_own_meeting(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])
    meeting = user_with_settings.meetups[0]

    # Before calling the handler, the meeting has no user joined and messages registered
    assert len(meeting.joined_links) == 0
    assert len(meeting.messages) == 0

    # Call the handler
    context, _ = await call_handler(MeetingHandlerId.JOIN, handler_context=handler_context)

    # The user should have joined the meeting
    assert len(meeting.joined_links) == 1
    assert meeting.joined_links[0].user == user_with_settings
    assert len(meeting.messages) == 1
    # Single flush: the savepoint flush inside racy_flush; everything else lands at commit.
    mock_session.assert_flushed()

    # We have emited a feature metric for user joined
    metrics.assert_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.JOIN_MEETING)})

    # The user has been notified
    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingJoinMessages.JOIN_SUCCESS.get(lang=user_with_settings.lang),
        show_alert=False,
    )

    # All messages have been updated
    context.api.assert_update_meeting_messages_called(
        session=None,
        meeting=meeting,
        current_message=meeting.message_from_update(handler_context.update),
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.JOIN.with_id(1))], indirect=True)
async def test_user_already_join_does_not_join(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])
    meeting = user_with_settings.meetups[0]
    # The user is already in the meeting
    # Need to provide ids because the id is not added by the ssession without connection
    JoinedUsers(meetup=meeting, user=user_with_settings, meetup_id=meeting.id, user_id=user_with_settings.id)
    assert len(meeting.joined_links) == 1
    assert len(meeting.messages) == 0

    context, _ = await call_handler(MeetingHandlerId.JOIN, handler_context=handler_context)

    assert len(meeting.joined_links) == 1
    # The mssage has been registered
    assert len(meeting.messages) == 1
    # No explicit flush: nothing racy was inserted and the rest lands at commit.
    mock_session.assert_not_flushed()

    # No feature metric has been emitted
    metrics.assert_not_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.JOIN_MEETING)})

    # The user has been notified
    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingJoinMessages.JOIN_ALREADY_JOINED.get(lang=user_with_settings.lang),
        show_alert=False,
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.JOIN.with_id(1))], indirect=True)
async def test_concurrent_duplicate_join_is_idempotent_noop(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """A join that slips past the Python fast path but collides with the (user_id, meetup_id) unique
    constraint is reported as "already joined" — no fault, no double feature metric, and the surrounding
    work (the Message row) still persists via the shared flush."""
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])
    meeting = user_with_settings.meetups[0]

    # The user is NOT recorded as joined in the loaded Python state, so the fast path lets the join
    # through; the clash only surfaces when racy_flush's savepoint flush hits the DB constraint.
    mock_session.flush.side_effect = integrity_error(JOINED_USERS_UNIQUE_CONSTRAINT)

    assert len(meeting.messages) == 0

    context, _ = await call_handler(MeetingHandlerId.JOIN, handler_context=handler_context)

    # The Message row created before the operation is still there — the savepoint rolled back only the
    # duplicate insert, leaving the outer transaction consistent; the row lands at commit.
    assert len(meeting.messages) == 1
    # Only racy_flush's savepoint flush ran (and raised the clash).
    mock_session.assert_flushed()

    # No feature metric for a membership that was not actually inserted, and no fault raised — the
    # clash is an expected, handled outcome.
    metrics.assert_not_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.JOIN_MEETING)})
    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1.0)

    # The user is told they are already joined.
    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingJoinMessages.JOIN_ALREADY_JOINED.get(lang=user_with_settings.lang),
        show_alert=False,
    )

    # The surrounding message-update work still runs.
    context.api.assert_update_meeting_messages_called(
        session=None,
        meeting=meeting,
        current_message=meeting.message_from_update(handler_context.update),
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.JOIN.with_id(1))], indirect=True)
async def test_join_loads_meeting_with_row_lock(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    """Wiring guard for the per-meeting mutex (#187): the join/leave path must load the meeting
    with by_id(for_update=True). The actual serialization behavior is covered on real Postgres in
    tests/models/db_behavior/test_meeting_row_locks.py; this only pins the call site so a refactor
    cannot silently drop the lock."""
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    await call_handler(MeetingHandlerId.JOIN, handler_context=handler_context)

    assert_locked_meetup_select(mock_session)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.JOIN.with_id(1))], indirect=True)
async def test_join_with_existing_message_does_not_create_new_one(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """When message_from_update finds an existing message, no new Message is created (branch 168->173)."""
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])
    meeting = user_with_settings.meetups[0]

    # Pre-populate a message that matches the update's effective_message.message_id (default 123)
    from tests.helpers.constants import DEFAULT_CHAT_ID, DEFAULT_MESSAGE_ID

    existing_message = create_message(
        meetup_id=meeting.db_id,
        message_id=DEFAULT_MESSAGE_ID,
        chat_id=DEFAULT_CHAT_ID,
        inline_message_id=None,
    )
    meeting.messages.append(existing_message)

    assert len(meeting.messages) == 1

    context, _ = await call_handler(MeetingHandlerId.JOIN, handler_context=handler_context)

    # No new message was created — the existing one is reused
    assert len(meeting.messages) == 1
    # Single flush: the savepoint flush inside racy_flush; everything else lands at commit.
    mock_session.assert_flushed()

    metrics.assert_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.JOIN_MEETING)})


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.JOIN.with_id(123))], indirect=True)
async def test_user_cannot_join_if_the_meeting_is_full(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    owner = User(first_name="Owner", tg_user_id=1, settings=Settings())
    meeting = create_meetup(id=123, title="My Meeting", max_members=1, waiting_list=False, owner=owner)
    JoinedUsers(user=owner, meetup=meeting)
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, _ = await call_handler(MeetingHandlerId.JOIN, handler_context=handler_context)

    # The user should not have joined the meeting
    assert len(meeting.joined_links) == 1
    mock_session.assert_not_flushed()

    # No feature metric has been emitted
    metrics.assert_not_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.JOIN_MEETING)})

    # The user has been notified
    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingJoinMessages.JOIN_FULL.get(lang=user_with_settings.lang),
        show_alert=False,
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.JOIN.with_id(999))], indirect=True)
async def test_user_join_for_non_existing_meeting(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    context, _ = await call_handler(MeetingHandlerId.JOIN, handler_context=handler_context)

    # No feature metric has been emitted
    metrics.assert_emitted(name=MetricKey.STALE_MEETING_MESSAGE, value=1.0)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0.0, times=2)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=2)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=2)

    # The user has been notified
    context.api.assert_edit_message_called(
        update=handler_context.update,
        view=MeetingDisplayMessages.DELETED_BANNER.get(lang=user_with_settings.lang),
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.JOIN.with_id(1))], indirect=True)
async def test_non_existent_user_joins_meeting(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    # User is not in the database but meeting is
    mock_session.add_object(user_with_settings.meetups[0])

    context, _ = await call_handler(MeetingHandlerId.JOIN, handler_context=handler_context)

    # Assert user has been registered
    user = utils.user_from_update(handler_context.update, status=UserStatus.JOINED_ONLY)
    mock_session.assert_object_added(user)

    # Message has been updated
    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingJoinMessages.JOIN_UNREGISTERED.get(),
        show_alert=True,
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.LEAVE.with_id(1))], indirect=True)
async def test_user_leaves_meeting(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])
    meeting = user_with_settings.meetups[0]
    JoinedUsers(meetup=meeting, user=user_with_settings, meetup_id=meeting.id, user_id=user_with_settings.id)

    context, _ = await call_handler(MeetingHandlerId.LEAVE, handler_context=handler_context)

    # The user is no longer in the meeting
    assert not meeting.has_participant(user_with_settings.db_id)

    # We have emited a feature metric for user left
    metrics.assert_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.LEAVE_MEETING)})

    # The user has been notified
    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingJoinMessages.LEAVE_SUCCESS.get(lang=user_with_settings.lang),
        show_alert=False,
    )

    # All messages have been updated
    context.api.assert_update_meeting_messages_called(
        session=None,
        meeting=meeting,
        current_message=meeting.message_from_update(handler_context.update),
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.LEAVE.with_id(1))], indirect=True)
async def test_non_existing_user_leaves_meeting(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    # User is not in the database but meeting is
    mock_session.add_object(user_with_settings.meetups[0])

    context, _ = await call_handler(MeetingHandlerId.LEAVE, handler_context=handler_context)

    # Assert user has been registered
    user = utils.user_from_update(handler_context.update, status=UserStatus.JOINED_ONLY)
    mock_session.assert_object_added(user)

    # Message has been updated
    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingJoinMessages.LEAVE_UNREGISTERED.get(),
        show_alert=True,
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.LEAVE.with_id(999))], indirect=True)
async def test_user_leave_for_non_existing_meeting(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    context, _ = await call_handler(MeetingHandlerId.LEAVE, handler_context=handler_context)

    # No feature metric has been emitted
    metrics.assert_emitted(name=MetricKey.STALE_MEETING_MESSAGE, value=1.0)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0.0, times=2)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=2)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=2)

    # The user has been notified
    context.api.assert_edit_message_called(
        update=handler_context.update,
        view=MeetingDisplayMessages.DELETED_BANNER.get(lang=user_with_settings.lang),
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.LEAVE.with_id(1))], indirect=True)
async def test_leave_creates_new_message_when_no_existing_message_found(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    """When message_from_update returns None during a leave operation (branch 168→170),
    a new Message is created and appended to the meeting's messages list."""
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])
    meeting = user_with_settings.meetups[0]
    # Join the user so they can leave
    JoinedUsers(meetup=meeting, user=user_with_settings, meetup_id=meeting.id, user_id=user_with_settings.id)

    # Confirm no messages exist before the handler runs
    assert len(meeting.messages) == 0

    context, _ = await call_handler(MeetingHandlerId.LEAVE, handler_context=handler_context)

    # A new message was created (branch 168→170 in handle_join_leave_operation)
    assert len(meeting.messages) == 1
    # No explicit flush on the leave path: the removal lands at commit.
    mock_session.assert_not_flushed()

    context.api.assert_update_meeting_messages_called(
        session=None,
        meeting=meeting,
        current_message=meeting.messages[0],
    )


@pytest.mark.parametrize(
    "update, handler_id",
    [
        (UpdateRequest(callback_query=cb.JOIN.with_id(50)), MeetingHandlerId.JOIN),
        (UpdateRequest(callback_query=cb.LEAVE.with_id(50)), MeetingHandlerId.LEAVE),
    ],
    indirect=["update"],
    ids=["join", "leave"],
)
async def test_action_on_inactive_meeting_shows_finished_message(
    update: Update,
    handler_id: MeetingHandlerId,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    inactive_meeting = create_meetup(id=50, title="Past Meeting", active=False, owner=user_with_settings)
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(inactive_meeting)

    context, _ = await call_handler(handler_id, handler_context=handler_context)

    # The "meeting has finished" message is shown with no buttons
    context.api.assert_edit_message_called(
        update=handler_context.update,
        view=MitupView(
            description=MeetingDisplayMessages.FINISHED_BANNER.get(lang=inactive_meeting.lang),
            keyboard=[],
        ),
    )

    # No join/leave operation was performed
    context.api.assert_method_just_called("answer_callback_query", times=0)
    context.api.assert_method_just_called("update_meeting_messages", times=0)
