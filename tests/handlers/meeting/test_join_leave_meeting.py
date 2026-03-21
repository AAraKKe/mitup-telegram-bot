import pytest
from telegram import Update

import mitup_bot.utils.callbacks as cb
from mitup_bot.handlers.meeting.enums import MeetingHandlerId
from mitup_bot.models import JoinedUsers, Settings, User, utils
from mitup_bot.monitoring import Feature, MetricKey, MetricUnit
from mitup_bot.utils.messages import MeetingMessages
from mitup_bot.views import MitupView
from tests.helpers import AnyFloat, HandlerContext, MockDbSession, UpdateRequest, call_handler, create_meetup
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
    mock_session.assert_flushed()

    # We have emited a feature metric for user joined
    metrics.assert_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.JOIN_MEETING)})

    # The user has been notified
    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingMessages.JOINED_MEETING_SUCCESS.get(lang=user_with_settings.lang),
        show_alert=False,
    )

    # All messages have been updated
    context.api.assert_update_meeting_messages_called(
        session=mock_session,
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
    mock_session.assert_flushed()

    # No feature metric has been emitted
    metrics.assert_not_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.JOIN_MEETING)})

    # The user has been notified
    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingMessages.JOINED_MEETING_ALREADY.get(lang=user_with_settings.lang),
        show_alert=False,
    )


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
    mock_session.assert_flushed()

    # No feature metric has been emitted
    metrics.assert_not_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.JOIN_MEETING)})

    # The user has been notified
    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingMessages.JOINED_MEETING_FULL.get(lang=user_with_settings.lang),
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
        view=MeetingMessages.MEETING_HAS_BEEN_DELETED.get(lang=user_with_settings.lang),
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
    user = utils.user_from_update(handler_context.update)
    mock_session.assert_object_added(user)

    # Message has been updated
    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingMessages.JOINED_MEETING_UNREGISTERED.get(),
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
        text=MeetingMessages.LEFT_MEETING_SUCCESS.get(lang=user_with_settings.lang),
        show_alert=False,
    )

    # All messages have been updated
    context.api.assert_update_meeting_messages_called(
        session=mock_session,
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
    user = utils.user_from_update(handler_context.update)
    mock_session.assert_object_added(user)

    # Message has been updated
    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingMessages.LEFT_MEETING_UNREGISTERED.get(),
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
        view=MeetingMessages.MEETING_HAS_BEEN_DELETED.get(lang=user_with_settings.lang),
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
    mock_session.assert_flushed()

    context.api.assert_update_meeting_messages_called(
        session=mock_session,
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
            description=MeetingMessages.MEETING_HAS_FINISHED.get(lang=inactive_meeting.lang),
            keyboard=[],
        ),
    )

    # No join/leave operation was performed
    context.api.assert_method_just_called("answer_callback_query", times=0)
    context.api.assert_method_just_called("update_meeting_messages", times=0)
