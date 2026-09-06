import pytest
from telegram import Update

from mitup_bot.handlers.meeting.edit.enums import EditMeetingHandlerId
from mitup_bot.handlers.meeting.edit.views import kick_out_users_view
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models.users import User
from mitup_bot.utils import ButtonMessages, MeetingEditParticipantsMessages, MeetingJoinMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.rich_message import button_markup
from mitup_bot.views import RenderContext
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.factory import confirmation_view
from mitup_bot.views.mitup_view import MitupView
from tests.helpers import (
    UpdateRequest,
    assert_locked_meetup_select,
    call_handler,
    create_joined_link,
    create_meetup,
    create_user,
)
from tests.helpers.handler_context import HandlerContext
from tests.helpers.stub_db import MockDbSession


def participants_list(num_participants: int):
    return [create_user(id=i, username=f"user_{i}", first_name=f"User {i}") for i in range(1, num_participants + 1)]


def test_kickout_view_lists_every_participant_with_a_kick_chip():
    """The whole list rides in the body, one line per participant with their own danger chip;
    the owner is the one member without a line."""
    meetup = create_meetup(id=1, title="Test Meetup")
    current_user = create_user(id=99, username="current_user", first_name="Current User")
    create_joined_link(user=current_user, meetup=meetup)
    participants = participants_list(21)
    for participant in participants:
        create_joined_link(user=participant, meetup=meetup)

    view = kick_out_users_view(meeting=meetup, current_user=current_user)

    html = view.message.html
    for participant in participants:
        assert participant.display_name in html
        assert (
            button_markup(
                ButtonConfig(
                    text=ButtonMessages.REMOVE.text(lang=current_user.lang),
                    callback_data=cb.EDIT_MEETING_KICK_OUT_ACTION.with_ids(meetup.db_id, participant.db_id),
                    style="danger",
                )
            )
            in html
        )
    assert str(cb.EDIT_MEETING_KICK_OUT_ACTION.with_ids(meetup.db_id, current_user.db_id)) not in html

    # The menu carries only the back row: the kick controls live in the body.
    assert view.menu == [
        [
            ButtonConfig(
                text=ButtonMessages.MEETING.back(lang=current_user.lang),
                callback_data=cb.EDIT_MEETING.with_id(meetup.db_id),
            )
        ]
    ]


def prepare_meeting(mock_session: MockDbSession, user_with_settings: User):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    for i in range(12):
        # Use a shift id to avoid overlapping with the current user with id 1
        shifted_id = 10 + i
        user = create_user(
            id=shifted_id,
            username=f"joined_user_{shifted_id}",
            first_name=f"Joined User {shifted_id}",
            tg_user_id=123 * shifted_id,
        )
        create_joined_link(user=user, meetup=meeting)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_KICK_OUT_PARTICIPANTS.with_ids(1, 1))], indirect=["update"]
)
async def test_edit_meeting_kickout_participants_sends_list_of_participants(
    user_with_settings: User,
    handler_context: HandlerContext,
    mock_session: MockDbSession,
    update: Update,
):
    prepare_meeting(mock_session, user_with_settings)

    context, _ = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_CALLBACK, handler_context=handler_context
    )

    context.api.assert_edit_message_called(
        update,
        kick_out_users_view(meeting=user_with_settings.meetups[0], current_user=user_with_settings),
    )

    actual_view: MitupView = context.api.call_args_list("edit_message")[0].kwargs["view"]
    html = actual_view.message.html
    # Every joined user is on the list with their own kick chip; the acting owner is not.
    for shifted_id in range(10, 22):
        assert f"kickout;user:{shifted_id}:1" in html
    assert "kickout;user:1:1" not in html

    back_button = actual_view.menu[-1][0]
    assert back_button.callback_data == cb.EDIT_MEETING.with_id(user_with_settings.meetups[0].db_id)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_KICK_OUT_PARTICIPANTS.with_ids(1, 1))], indirect=["update"]
)
async def test_edit_meeting_kickout_participants_ignores_waiting_list(
    user_with_settings: User,
    handler_context: HandlerContext,
    mock_session: MockDbSession,
    update: Update,
):
    prepare_meeting(mock_session, user_with_settings)
    user_with_settings.meetups[0].waiting_list = True

    ## Add a couple of users to the waiting list
    user_with_settings.meetups[0].joined_links[3].is_waiting_list = True
    user_with_settings.meetups[0].joined_links[4].is_waiting_list = True

    context, _ = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_CALLBACK, handler_context=handler_context
    )

    args_list = context.api.call_args_list("edit_message")
    assert len(args_list) == 1
    actual_view = args_list[0].kwargs["view"]
    html = actual_view.message.html
    assert "kickout;user:13:1" not in html
    assert "kickout;user:14:1" not in html


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_KICK_OUT_PARTICIPANTS.with_ids(1, 1))], indirect=["update"]
)
async def test_edit_meeting_kickout_participants_with_no_participants(
    user_with_settings: User,
    handler_context: HandlerContext,
    mock_session: MockDbSession,
    update: Update,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    context, _ = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_CALLBACK, handler_context=handler_context
    )

    context.api.assert_edit_message_called(update, meeting_views.owner_view(user_with_settings.meetups[0]))


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.EDIT_MEETING_KICK_OUT_ACTION.with_ids(1, 15))],
    indirect=["update"],
)
async def test_edit_meeting_kickout_given_participant(
    user_with_settings: User,
    handler_context: HandlerContext,
    mock_session: MockDbSession,
    update: Update,
):
    prepare_meeting(mock_session, user_with_settings)
    context, _ = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_ACTION_CALLBACK, handler_context=handler_context
    )
    context.api.assert_edit_message_called(
        update,
        confirmation_view(
            RenderContext(lang=user_with_settings.lang),
            message=MeetingEditParticipantsMessages.KICK_OUT_CONFIRMATION.rich(
                lang=user_with_settings.lang, participant="joined_user_15", meeting_title="Test Meeting 1"
            ),
            confirm_callback_data=cb.CONFIRM_KICK_OUT.with_ids(meeting_id=1, id=15),
            decline_callback_data=cb.EDIT_MEETING_KICK_OUT_PARTICIPANTS.with_ids(meeting_id=1, id=1),
        ),
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.EDIT_MEETING_KICK_OUT_ACTION.with_ids(1, 5))],
    indirect=["update"],
)
async def test_edit_meeting_kickout_participant_no_longer_in_meeting(
    user_with_settings: User,
    handler_context: HandlerContext,
    mock_session: MockDbSession,
    update: Update,
):
    prepare_meeting(mock_session, user_with_settings)
    context, _ = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_ACTION_CALLBACK, handler_context=handler_context
    )
    context.api.assert_edit_message_called(
        update,
        meeting_views.owner_view(user_with_settings.meetups[0]).with_context(
            MeetingEditParticipantsMessages.KICK_OUT_NOT_IN_MEETING.rich(lang=user_with_settings.lang)
        ),
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.CONFIRM_KICK_OUT.with_ids(1, 10))],
    indirect=["update"],
)
async def test_edit_meeting_kickout_participant_confirm(
    user_with_settings: User,
    handler_context: HandlerContext,
    mock_session: MockDbSession,
    update: Update,
):
    prepare_meeting(mock_session, user_with_settings)
    meeting = user_with_settings.meetups[0]
    participant_to_delete = meeting.participant(10)
    assert participant_to_delete is not None

    context, _ = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_ACTION_CONFIRM_CALLBACK, handler_context=handler_context
    )

    # Test the message has been edited successfully
    expected_view = kick_out_users_view(meeting=meeting, current_user=user_with_settings)

    context.api.assert_edit_message_called(
        update,
        expected_view,
    )

    # The kicked-out participant lost their line and their chip
    assert "kickout;user:10:1" not in expected_view.message.html

    # The list's back button returns to the editor card, where the kick-out chip lives.
    back_button = expected_view.menu[-1][0]
    assert back_button.callback_data == cb.EDIT_MEETING.with_id(meeting.db_id)

    # Kickout should trigger meeting messages update
    context.api.assert_update_meeting_messages_called(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.CONFIRM_KICK_OUT.with_ids(1, 10))],
    indirect=["update"],
)
async def test_kickout_confirm_loads_meeting_with_row_lock(
    user_with_settings: User,
    handler_context: HandlerContext,
    mock_session: MockDbSession,
):
    """Wiring guard for the per-meeting mutex (#187): kick-out confirm must load the meeting with
    for_update=True (removal can promote from the waiting list). The actual serialization behavior
    is covered on real Postgres in tests/models/db_behavior/test_meeting_row_locks.py; this only
    pins the call site so a refactor cannot silently drop the lock."""
    prepare_meeting(mock_session, user_with_settings)

    await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_ACTION_CONFIRM_CALLBACK, handler_context=handler_context
    )

    assert_locked_meetup_select(mock_session)


@pytest.mark.parametrize(
    "update, is_invited",
    [
        [UpdateRequest(callback_query=cb.CONFIRM_KICK_OUT.with_ids(1, 10)), False],
        [UpdateRequest(callback_query=cb.CONFIRM_KICK_OUT.with_ids(1, 10)), True],
    ],
    indirect=["update"],
    ids=["normal_participant", "invited_participant"],
)
async def test_edit_meeting_kickout_participant_confirm_promotes_waiting_list(
    handler_context: HandlerContext,
    user_with_settings: User,
    mock_session: MockDbSession,
    is_invited: bool,
):
    prepare_meeting(mock_session, user_with_settings)
    meeting = user_with_settings.meetups[0]
    if is_invited:
        participant_in_waiting_list = create_joined_link(
            user=create_user(id=200, username="joined_user_10", first_name="Joined User 10", tg_user_id=-1),
            invited_by=user_with_settings,
            meetup=meeting,
        )
    else:
        participant_in_waiting_list = meeting.joined_links[6]
    participant_in_waiting_list.is_waiting_list = True

    context, _ = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_ACTION_CONFIRM_CALLBACK, handler_context=handler_context
    )

    # User 10 is no longer in the meeting and 6 is no longer in the waiting list
    assert not participant_in_waiting_list.is_waiting_list
    assert not meeting.has_participant(10)

    # Teh proper view has been sent to the user
    expected_view = kick_out_users_view(meeting=user_with_settings.meetups[0], current_user=user_with_settings)

    context.api.assert_edit_message_called(handler_context.update, expected_view)

    # The promoted user has been notified only if they were not invited
    times_message_sent = 0 if is_invited else 1

    context.api.assert_send_message_to_user_called(
        user=participant_in_waiting_list.user,
        view=MeetingJoinMessages.PROMOTED_FROM_WAITING_LIST.rich(
            lang=participant_in_waiting_list.user.lang, meeting_title=meeting.title
        ),
        times=times_message_sent,
    )

    # Assert that the user that was kicked out was not removed
    mock_session.assert_not_deleted()


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.CONFIRM_KICK_OUT.with_ids(1, 5))],
    indirect=["update"],
)
async def test_edit_meeting_kickout_participant_confirm_no_more_participants(
    user_with_settings: User,
    handler_context: HandlerContext,
    mock_session: MockDbSession,
    update: Update,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")
    # This is the only participant in the meeting, user 6 is in waiting list
    create_joined_link(user=create_user(id=5, username="user_5", first_name="User 5"), meetup=meeting)

    context, _ = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_ACTION_CONFIRM_CALLBACK, handler_context=handler_context
    )

    context.api.assert_edit_message_called(update, meeting_views.owner_view(meeting))


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.CONFIRM_KICK_OUT.with_ids(1, 5))],
    indirect=["update"],
)
async def test_edit_meeting_kickout_participant_confirm_no_longer_in_meeting(
    user_with_settings: User,
    handler_context: HandlerContext,
    mock_session: MockDbSession,
    update: Update,
):
    prepare_meeting(mock_session, user_with_settings)
    context, _ = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_ACTION_CONFIRM_CALLBACK, handler_context=handler_context
    )

    context.api.assert_edit_message_called(
        update,
        meeting_views.owner_view(user_with_settings.meetups[0]).with_context(
            MeetingEditParticipantsMessages.KICK_OUT_NOT_IN_MEETING.rich(lang=user_with_settings.lang)
        ),
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.CONFIRM_KICK_OUT.with_ids(1, 200))],
    indirect=["update"],
)
async def test_kick_out_invited_participant(
    user_with_settings: User,
    handler_context: HandlerContext,
    mock_session: MockDbSession,
):
    prepare_meeting(mock_session, user_with_settings)
    meeting = user_with_settings.meetups[0]

    invited_user = create_user(id=200, username="invited", first_name="Invited")
    mock_session.add_object(invited_user)
    meeting.add_participant(invited_user, invited_by=user_with_settings)

    context, _ = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_ACTION_CONFIRM_CALLBACK,
        handler_context=handler_context,
    )

    # Test the message has been edited successfully
    expected_view = kick_out_users_view(meeting=meeting, current_user=user_with_settings)

    # The kicked-out invited participant lost their line and their chip
    assert "kickout;user:200:1" not in expected_view.message.html

    # The user has been deleted
    mock_session.assert_deleted(invited_user)
