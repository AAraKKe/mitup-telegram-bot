import pytest
from aws_embedded_metrics.unit import Unit

import mitup_bot.utils.callbacks as cb
from mitup_bot.handlers.edit_meeting.enums import EditMeetingHandlerId
from mitup_bot.models import JoinedUsers, Settings, User, utils
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.utils.messages import MeetingMessages
from tests.helpers import AnyFloat, HandlerContext, MockApi, MockDbSession, UpdateRequest, call_handler, create_meetup


@pytest.fixture
def api():
    with MockApi.start("mitup_bot.handlers.edit_meeting.join_leave") as api:
        yield api


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.JOIN.with_id(1))], indirect=True)
async def test_existing_user_joins_own_meeting(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    api: MockApi,
):
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])
    meeting = user_with_settings.meetups[0]

    # Before calling the handler, the meeting has no user joined and messages registered
    assert len(meeting.joined_links) == 0
    assert len(meeting.messages) == 0

    # Call the handler
    context, _ = await call_handler(handler_context.update, handler_context.app, EditMeetingHandlerId.JOIN)

    # The user should have joined the meeting
    assert len(meeting.joined_links) == 1
    assert meeting.joined_links[0].user == user_with_settings
    assert len(meeting.messages) == 1
    mock_session.assert_flushed()

    # We have emited a feature metric for user joined
    context.metrics_engine.assert_feature_metrics_emitted(Feature.JOIN_MEETING)

    # The user has been notified
    api.assert_answer_callback_query_called(
        context=context,
        update=handler_context.update,
        text=MeetingMessages.JOINED_MEETING_SUCCESS.get(lang=user_with_settings.lang, plain=True),
        show_alert=False,
    )

    # All messages have been updated
    api.assert_update_meeting_messages_called(
        session=mock_session,
        context=context,
        meeting=meeting,
        current_message=meeting.message_from_update(handler_context.update),
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.JOIN.with_id(1))], indirect=True)
async def test_user_already_join_does_not_join(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    api: MockApi,
):
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])
    meeting = user_with_settings.meetups[0]
    # The user is already in the meeting
    # Need to provide ids because the id is not added by the ssession without connection
    JoinedUsers(meetup=meeting, user=user_with_settings, meetup_id=meeting.id, user_id=user_with_settings.id)
    assert len(meeting.joined_links) == 1
    assert len(meeting.messages) == 0

    context, _ = await call_handler(handler_context.update, handler_context.app, EditMeetingHandlerId.JOIN)

    assert len(meeting.joined_links) == 1
    # The mssage has been registered
    assert len(meeting.messages) == 1
    mock_session.assert_flushed()

    # No feature metric has been emitted
    context.metrics_engine.assert_feature_metrcs_not_emitted(Feature.JOIN_MEETING)

    # The user has been notified
    api.assert_answer_callback_query_called(
        context=context,
        update=handler_context.update,
        text=MeetingMessages.JOINED_MEETING_ALREADY.get(lang=user_with_settings.lang),
        show_alert=False,
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.JOIN.with_id(123))], indirect=True)
async def test_user_cannot_join_if_the_meeting_is_full(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    api: MockApi,
):
    owner = User(first_name="Owner", tg_user_id=1, settings=Settings())
    meeting = create_meetup(id=123, title="My Meeting", max_members=1, waiting_list=False, owner=owner)
    JoinedUsers(user=owner, meetup=meeting)
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, _ = await call_handler(handler_context.update, handler_context.app, EditMeetingHandlerId.JOIN)

    # The user should not have joined the meeting
    assert len(meeting.joined_links) == 1
    mock_session.assert_flushed()

    # No feature metric has been emitted
    context.metrics_engine.assert_feature_metrcs_not_emitted(Feature.JOIN_MEETING)

    # The user has been notified
    api.assert_answer_callback_query_called(
        context=context,
        update=handler_context.update,
        text=MeetingMessages.JOINED_MEETING_FULL.get(lang=user_with_settings.lang, plain=True),
        show_alert=False,
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.JOIN.with_id(999))], indirect=True)
async def test_user_join_for_non_existing_meeting(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    api: MockApi,
):
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    context, _ = await call_handler(handler_context.update, handler_context.app, EditMeetingHandlerId.JOIN)

    # No feature metric has been emitted
    context.metrics_engine.assert_metrics_emited(
        [MetricKey.STALE_MEETING_MESSAGE, MetricKey.FAULT, MetricKey.TIME, MetricKey.DB_CONNECTIONS_LEAKED],
        [1.0, 0.0, AnyFloat(), 0],
        [Unit.COUNT, Unit.COUNT, Unit.MILLISECONDS, Unit.COUNT],
        add_handler_dimensions=False,
    )

    # The user has been notified
    api.assert_edit_message_called(
        context=context,
        update=handler_context.update,
        view=MeetingMessages.MEETING_HAS_BEEN_DELETED.get(lang=user_with_settings.lang),
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.JOIN.with_id(1))], indirect=True)
async def test_non_existent_user_joins_meeting(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    api: MockApi,
):
    # User is not in the database but meeting is
    mock_session.add_object(user_with_settings.meetups[0])

    context, _ = await call_handler(handler_context.update, handler_context.app, EditMeetingHandlerId.JOIN)

    # Assert user has been registered
    user = utils.user_from_update(handler_context.update)
    mock_session.assert_object_added(user)

    # Message has been updated
    api.assert_answer_callback_query_called(
        context=context,
        update=handler_context.update,
        text=MeetingMessages.JOINED_MEETING_UNREGISTERED.get(plain=True),
        show_alert=True,
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.LEAVE.with_id(1))], indirect=True)
async def test_user_leaves_meeting(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    api: MockApi,
):
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])
    meeting = user_with_settings.meetups[0]
    JoinedUsers(meetup=meeting, user=user_with_settings, meetup_id=meeting.id, user_id=user_with_settings.id)

    context, _ = await call_handler(handler_context.update, handler_context.app, EditMeetingHandlerId.LEAVE)

    # The user is no longer in the meeting
    assert not meeting.has_participant(user_with_settings.db_id)

    # We have emited a feature metric for user left
    context.metrics_engine.assert_feature_metrics_emitted(Feature.LEAVE_MEETING)

    # The user has been notified
    api.assert_answer_callback_query_called(
        context=context,
        update=handler_context.update,
        text=MeetingMessages.LEFT_MEETING_SUCCESS.get(lang=user_with_settings.lang, plain=True),
        show_alert=False,
    )

    # All messages have been updated
    api.assert_update_meeting_messages_called(
        session=mock_session,
        context=context,
        meeting=meeting,
        current_message=meeting.message_from_update(handler_context.update),
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.LEAVE.with_id(1))], indirect=True)
async def test_non_existing_user_leaves_meeting(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    api: MockApi,
):
    # User is not in the database but meeting is
    mock_session.add_object(user_with_settings.meetups[0])

    context, _ = await call_handler(handler_context.update, handler_context.app, EditMeetingHandlerId.LEAVE)

    # Assert user has been registered
    user = utils.user_from_update(handler_context.update)
    mock_session.assert_object_added(user)

    # Message has been updated
    api.assert_answer_callback_query_called(
        context=context,
        update=handler_context.update,
        text=MeetingMessages.LEFT_MEETING_UNREGISTERED.get(plain=True),
        show_alert=True,
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.LEAVE.with_id(999))], indirect=True)
async def test_user_leave_for_non_existing_meeting(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    api: MockApi,
):
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    context, _ = await call_handler(handler_context.update, handler_context.app, EditMeetingHandlerId.LEAVE)

    # No feature metric has been emitted
    context.metrics_engine.assert_metrics_emited(
        [MetricKey.STALE_MEETING_MESSAGE, MetricKey.FAULT, MetricKey.TIME, MetricKey.DB_CONNECTIONS_LEAKED],
        [1.0, 0.0, AnyFloat(), 0],
        [Unit.COUNT, Unit.COUNT, Unit.MILLISECONDS, Unit.COUNT],
        add_handler_dimensions=False,
    )

    # The user has been notified
    api.assert_edit_message_called(
        context=context,
        update=handler_context.update,
        view=MeetingMessages.MEETING_HAS_BEEN_DELETED.get(lang=user_with_settings.lang),
    )
