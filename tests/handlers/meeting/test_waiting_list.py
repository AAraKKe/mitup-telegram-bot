import datetime as dt

import pytest

from mitup_bot.handlers.edit_meeting.enums import EditMeetingHandlerId
from mitup_bot.models import JoinedUsers, Meetup, Settings, User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import MeetingMessages
from tests.helpers import HandlerContext, MockApi, MockDbSession, UpdateRequest, call_handler, create_meetup


@pytest.fixture
def api():
    with MockApi.start("mitup_bot.handlers.edit_meeting.join_leave") as api:
        yield api


@pytest.fixture
def full_meeting():
    owner = User(first_name="Owner", tg_user_id=1, settings=Settings())
    meeting = create_meetup(id=123, title="My Meeting", max_members=1, waiting_list=True, owner=owner)
    JoinedUsers(user=owner, meetup=meeting)
    return meeting


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.JOIN.with_id(123))], indirect=True)
async def test_user_joins_waiting_list_with_full_meeting(
    full_meeting: Meetup,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    api: MockApi,
):
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(full_meeting)

    # Before calling the handler, the meeting has no user joined and messages registered
    assert len(full_meeting.joined_links) == 1

    # Call the handler
    context, _ = await call_handler(handler_context.update, handler_context.app, EditMeetingHandlerId.JOIN)

    # The user should have joined the meeting
    assert len(full_meeting.joined_links) == 2
    assert full_meeting.n_joined == 1
    assert full_meeting.n_waiting == 1

    mock_session.assert_flushed()

    # The user has been notified
    api.assert_answer_callback_query_called(
        context=context,
        update=handler_context.update,
        text=MeetingMessages.JOINED_MEETING_FULL_WAITING_LIST.get(lang=user_with_settings.lang, plain=True),
        show_alert=False,
    )

    # All messages have been updated
    api.assert_update_meeting_messages_called(
        session=mock_session,
        context=context,
        meeting=full_meeting,
        current_message=full_meeting.message_from_update(handler_context.update),
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.LEAVE.with_id(123))], indirect=True)
async def test_user_leaves_and_waiting_list_promotes(
    full_meeting: Meetup,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    api: MockApi,
):
    # Setup the meeting to have a second user on the waiting list and user with settings is going to leave
    full_meeting.max_members = 2
    second_user = User(first_name="Second", tg_user_id=2, settings=user_with_settings.settings)
    JoinedUsers(
        user=user_with_settings,
        meetup_id=full_meeting.id,
        user_id=user_with_settings.id,
        meetup=full_meeting,
        is_waiting_list=False,
    )
    JoinedUsers(
        user=second_user,
        meetup=full_meeting,
        meetup_id=full_meeting.id,
        user_id=second_user.id,
        is_waiting_list=True,
    )

    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(full_meeting)

    # Before calling the handler, the meeting has two users joined and one in the waiting list
    assert full_meeting.n_joined == 2
    assert full_meeting.n_waiting == 1

    # Ensure that when the link is deleted it disappears from the meeting
    mock_session.delete.side_effect = lambda link: full_meeting.joined_links.remove(link)

    # Call the handler for the user leaving
    context, _ = await call_handler(handler_context.update, handler_context.app, EditMeetingHandlerId.LEAVE)

    # The user should have left the meeting and the waiting list user should be promoted
    assert full_meeting.n_joined == 2
    assert full_meeting.n_waiting == 0
    users_joined = {link.user.first_name for link in full_meeting.joined_links}
    assert "Second" in users_joined
    assert "Owner" in users_joined

    mock_session.assert_flushed()

    # The user who was promoted has been notified
    api.assert_send_to_user_called(
        context=context,
        user=second_user,
        view=MeetingMessages.PROMOTED_FROM_THE_WAITING_LIST.get(
            lang=second_user.lang, meeting_title=full_meeting.title
        ),
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.LEAVE.with_id(123))], indirect=True)
async def test_user_leaves_and_first_waiting_list_user_promoted(
    full_meeting: Meetup,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    api: MockApi,
):
    # Setup the meeting to have two users on the waiting list and user with settings is going to leave
    full_meeting.max_members = 2
    first_waiting_user = User(first_name="FirstWaiting", tg_user_id=2, settings=user_with_settings.settings)
    second_waiting_user = User(first_name="SecondWaiting", tg_user_id=3, settings=user_with_settings.settings)
    JoinedUsers(
        user=user_with_settings,
        meetup_id=full_meeting.id,
        user_id=user_with_settings.id,
        meetup=full_meeting,
        is_waiting_list=False,
    )
    JoinedUsers(
        user=first_waiting_user,
        meetup=full_meeting,
        meetup_id=full_meeting.id,
        user_id=first_waiting_user.id,
        is_waiting_list=True,
        created_time=dt.datetime.now(),
    )
    JoinedUsers(
        user=second_waiting_user,
        meetup=full_meeting,
        meetup_id=full_meeting.id,
        user_id=second_waiting_user.id,
        is_waiting_list=True,
        created_time=dt.datetime.now() - dt.timedelta(hours=1),
    )

    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(full_meeting)

    # Before calling the handler, the meeting has two users joined and two in the waiting list
    assert full_meeting.n_joined == 2
    assert full_meeting.n_waiting == 2

    # Ensure that when the link is deleted it disappears from the meeting
    mock_session.delete.side_effect = lambda link: full_meeting.joined_links.remove(link)

    # Call the handler for the user leaving
    context, _ = await call_handler(handler_context.update, handler_context.app, EditMeetingHandlerId.LEAVE)

    # The user should have left the meeting and the first waiting list user should be promoted
    assert full_meeting.n_joined == 2
    assert full_meeting.n_waiting == 1
    users_joined = {link.user.first_name for link in full_meeting.joined_links}
    assert "SecondWaiting" in users_joined
    assert "Owner" in users_joined

    mock_session.assert_flushed()

    # The user who was promoted has been notified
    api.assert_send_to_user_called(
        context=context,
        user=second_waiting_user,
        view=MeetingMessages.PROMOTED_FROM_THE_WAITING_LIST.get(
            lang=first_waiting_user.lang, meeting_title=full_meeting.title
        ),
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.LEAVE.with_id(123))], indirect=True)
async def test_user_leaves_and_multiple_waiting_list_users_promoted(
    full_meeting: Meetup,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    api: MockApi,
):
    # Setup the meeting to have three users on the waiting list and user with settings is going to leave
    full_meeting.max_members = 3
    first_waiting_user = User(first_name="FirstWaiting", tg_user_id=2, settings=user_with_settings.settings)
    second_waiting_user = User(first_name="SecondWaiting", tg_user_id=3, settings=user_with_settings.settings)
    third_waiting_user = User(first_name="ThirdWaiting", tg_user_id=4, settings=user_with_settings.settings)
    JoinedUsers(
        user=user_with_settings,
        meetup_id=full_meeting.id,
        user_id=user_with_settings.id,
        meetup=full_meeting,
        is_waiting_list=False,
    )
    JoinedUsers(
        user=first_waiting_user,
        meetup=full_meeting,
        meetup_id=full_meeting.id,
        user_id=first_waiting_user.id,
        is_waiting_list=True,
        created_time=dt.datetime.now(),
    )
    JoinedUsers(
        user=second_waiting_user,
        meetup=full_meeting,
        meetup_id=full_meeting.id,
        user_id=second_waiting_user.id,
        is_waiting_list=True,
        created_time=dt.datetime.now() - dt.timedelta(hours=1),
    )
    JoinedUsers(
        user=third_waiting_user,
        meetup=full_meeting,
        meetup_id=full_meeting.id,
        user_id=third_waiting_user.id,
        is_waiting_list=True,
        created_time=dt.datetime.now() - dt.timedelta(hours=2),
    )

    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(full_meeting)

    # Before calling the handler, the meeting has two users joined and three in the waiting list
    assert full_meeting.n_joined == 2
    assert full_meeting.n_waiting == 3

    # Ensure that when the link is deleted it disappears from the meeting
    mock_session.delete.side_effect = lambda link: full_meeting.joined_links.remove(link)

    # Call the handler for the user leaving
    context, _ = await call_handler(handler_context.update, handler_context.app, EditMeetingHandlerId.LEAVE)

    # The user should have left the meeting and the first two waiting list users should be promoted
    assert full_meeting.n_joined == 3
    assert full_meeting.n_waiting == 1
    users_joined = {link.user.first_name for link in full_meeting.joined_links}
    assert "SecondWaiting" in users_joined
    assert "ThirdWaiting" in users_joined
    assert "Owner" in users_joined

    mock_session.assert_flushed()

    # The users who were promoted have been notified
    api.assert_send_to_user_called(
        context=context,
        user=second_waiting_user,
        view=MeetingMessages.PROMOTED_FROM_THE_WAITING_LIST.get(
            lang=second_waiting_user.lang, meeting_title=full_meeting.title
        ),
        times=2,
    )
    api.assert_send_to_user_called(
        context=context,
        user=third_waiting_user,
        view=MeetingMessages.PROMOTED_FROM_THE_WAITING_LIST.get(
            lang=third_waiting_user.lang, meeting_title=full_meeting.title
        ),
        times=2,
    )
