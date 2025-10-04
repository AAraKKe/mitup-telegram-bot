import pytest
from telegram import Update

from mitup_bot.handlers.edit_meeting.enums import EditMeetingHandlerId
from mitup_bot.handlers.edit_meeting.views import edit_participants_view, kick_out_users_view
from mitup_bot.models.users import User
from mitup_bot.utils import MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages
from mitup_bot.views.factory import confirmation_view
from mitup_bot.views.mitup_view import MitupView
from tests.helpers import (
    MockApi,
    StubMitupApp,
    UpdateRequest,
    call_handler,
    create_joined_link,
    create_meetup,
    create_user,
)
from tests.helpers.stub_db import MockDbSession


@pytest.fixture
def api():
    with MockApi.start("mitup_bot.handlers.edit_meeting.edit_meeting_location") as api:
        yield api


def participants_list(num_participants: int):
    return [create_user(id=i, username=f"user_{i}", first_name=f"User {i}") for i in range(1, num_participants + 1)]


@pytest.mark.parametrize(
    "num_participants, expected_pages",
    [(1, 1), (5, 1), (10, 1), (11, 2), (20, 2), (21, 3)],
    ids=[
        "one_participant",
        "five_participants",
        "ten_participants",
        "eleven_participants",
        "twenty_participants",
        "twenty_one_participants",
    ],
)
def test_kickout_participants_view_pagination(num_participants: int, expected_pages: int):
    meetup = create_meetup(id=1, title="Test Meetup")
    participants = participants_list(num_participants)
    for participant in participants:
        create_joined_link(user=participant, meetup=meetup)
    current_user = create_user(id=99, username="current_user", first_name="Current User")

    view = kick_out_users_view(
        page_number=1,
        meeting=meetup,
        current_user=current_user,
    )

    assert view.total_pages == expected_pages
    # Test back buttons
    back_button_row = view.keyboard[-1]
    assert len(back_button_row) == 1
    back_button = back_button_row[0]
    assert back_button.text == f"{ButtonMessages.EDIT.back(lang=current_user.lang)}"


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
    "update, page_number, expected_user_id",
    [
        [UpdateRequest(callback_query=cb.EDIT_MEETING_KICK_OUT_PARTICIPANTS.with_ids(1, 1)), 1, 10],
        [UpdateRequest(callback_query=cb.EDIT_MEETING_KICK_OUT_PARTICIPANTS.with_ids(1, 2)), 2, 21],
    ],
    indirect=["update"],
    ids=["first_page", "second_page"],
)
async def test_edit_meeting_kickout_participants_sends_list_of_participants(
    user_with_settings: User,
    app: StubMitupApp,
    api: MockApi,
    mock_session: MockDbSession,
    update: Update,
    page_number: int,
    expected_user_id: int,
):
    prepare_meeting(mock_session, user_with_settings)

    context, _ = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_CALLBACK)

    api.assert_edit_message_called(
        context,
        update,
        kick_out_users_view(
            meeting=user_with_settings.meetups[0],
            current_user=user_with_settings,
            page_number=page_number,
        ),
    )

    actual_view: MitupView = api.edit_message_mock.call_args_list[0].kwargs["view"]
    all_buttons_text = {button.text for row in actual_view.keyboard for button in row}
    # Current user is not present in the button list
    assert "joined_user_1" not in all_buttons_text
    # Check a given user button is present
    assert f"joined_user_{expected_user_id}" in all_buttons_text
    # Validate that the button contains the correct callback data
    all_buttons_callback_data = {str(button.callback_data) for row in actual_view.keyboard for button in row}
    assert f"kickout;user:{expected_user_id}:1" in all_buttons_callback_data


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_KICK_OUT_PARTICIPANTS.with_ids(1, 1))], indirect=["update"]
)
async def test_edit_meeting_kickout_participants_ignores_waiting_list(
    user_with_settings: User,
    app: StubMitupApp,
    api: MockApi,
    mock_session: MockDbSession,
    update: Update,
):
    prepare_meeting(mock_session, user_with_settings)
    user_with_settings.meetups[0].waiting_list = True

    ## Add a couple of users to the waiting list
    user_with_settings.meetups[0].joined_links[3].is_waiting_list = True
    user_with_settings.meetups[0].joined_links[4].is_waiting_list = True

    context, _ = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_CALLBACK)

    actual_view = api.edit_message_mock.call_args_list[0].kwargs["view"]
    all_buttons_text = {button.text for row in actual_view.keyboard for button in row}
    assert "joined_user_13" not in all_buttons_text
    assert "joined_user_14" not in all_buttons_text


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_KICK_OUT_PARTICIPANTS.with_ids(1, 1))], indirect=["update"]
)
async def test_edit_meeting_kickout_participants_with_no_participants(
    user_with_settings: User,
    app: StubMitupApp,
    api: MockApi,
    mock_session: MockDbSession,
    update: Update,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    context, _ = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_CALLBACK)

    api.assert_edit_message_called(context, update, edit_participants_view(user_with_settings.meetups[0]))


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.EDIT_MEETING_KICK_OUT_ACTION.with_ids(1, 15))],
    indirect=["update"],
)
async def test_edit_meeting_kickout_given_participant(
    user_with_settings: User,
    app: StubMitupApp,
    api: MockApi,
    mock_session: MockDbSession,
    update: Update,
):
    prepare_meeting(mock_session, user_with_settings)
    context, _ = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_ACTION_CALLBACK)
    api.assert_edit_message_called(
        context,
        update,
        confirmation_view(
            lang=user_with_settings.lang,
            message=MeetingMessages.KICK_OUT_PARTICIPANT_CONFIRMATION_MESSAGE.get(
                lang=user_with_settings.lang, participant="joined_user_15", meeting_title="Test Meeting 1"
            ),
            confirm_callback_data=cb.EDIT_MEETING_KICK_OUT_ACTION_CONFIRM.with_ids(meeting_id=1, id=15),
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
    app: StubMitupApp,
    api: MockApi,
    mock_session: MockDbSession,
    update: Update,
):
    prepare_meeting(mock_session, user_with_settings)
    context, _ = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_ACTION_CALLBACK)
    api.assert_edit_message_called(
        context,
        update,
        edit_participants_view(user_with_settings.meetups[0]).with_context(
            MeetingMessages.PARTICIPANT_NO_LONGER_IN_MEETING.get(lang=user_with_settings.lang)
        ),
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.EDIT_MEETING_KICK_OUT_ACTION_CONFIRM.with_ids(1, 10))],
    indirect=["update"],
)
async def test_edit_meeting_kickout_participant_confirm(
    user_with_settings: User,
    app: StubMitupApp,
    api: MockApi,
    mock_session: MockDbSession,
    update: Update,
):
    prepare_meeting(mock_session, user_with_settings)
    participant_to_delete = user_with_settings.meetups[0].participant(10)
    assert participant_to_delete is not None

    context, _ = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_ACTION_CONFIRM_CALLBACK)

    # Test the message has been edited successfully
    expected_view = kick_out_users_view(
        meeting=user_with_settings.meetups[0],
        current_user=user_with_settings,
        page_number=1,
    ).with_context(
        MeetingMessages.PARTICIPANT_KICKED_OUT_SUCCESS.get(lang=user_with_settings.lang, participant="joined_user_10")
    )

    api.assert_edit_message_called(
        context,
        update,
        expected_view,
    )

    # Ensure the list of buttons does not include the participant that has been kicked out
    all_buttons_text = {button.text for row in expected_view.keyboard for button in row}
    assert "joined_user_10" not in all_buttons_text


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.EDIT_MEETING_KICK_OUT_ACTION_CONFIRM.with_ids(1, 10))],
    indirect=["update"],
)
async def test_edit_meeting_kickout_participant_confirm_promotes_waiting_list(
    user_with_settings: User,
    app: StubMitupApp,
    api: MockApi,
    mock_session: MockDbSession,
    update: Update,
):
    prepare_meeting(mock_session, user_with_settings)
    meeting = user_with_settings.meetups[0]
    participant_in_waiting_list = meeting.joined_links[6]
    participant_in_waiting_list.is_waiting_list = True

    context, _ = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_ACTION_CONFIRM_CALLBACK)

    # User 10 is no longer in the meeting and 6 is no longer in the waiting list
    assert not participant_in_waiting_list.is_waiting_list
    assert not meeting.has_participant(10)

    # Teh proper view has been sent to the user
    expected_view = kick_out_users_view(
        meeting=user_with_settings.meetups[0],
        current_user=user_with_settings,
        page_number=1,
    ).with_context(
        MeetingMessages.PARTICIPANT_KICKED_OUT_SUCCESS.get(lang=user_with_settings.lang, participant="joined_user_10")
    )

    api.assert_edit_message_called(context, update, expected_view)

    # The promoted user has been notified
    api.assert_send_to_user_called(
        context=context,
        user=participant_in_waiting_list.user,
        view=MeetingMessages.PROMOTED_FROM_THE_WAITING_LIST.get(
            lang=participant_in_waiting_list.user.lang, meeting_title=meeting.title
        ),
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.EDIT_MEETING_KICK_OUT_ACTION_CONFIRM.with_ids(1, 5))],
    indirect=["update"],
)
async def test_edit_meeting_kickout_participant_confirm_no_more_participants(
    user_with_settings: User,
    app: StubMitupApp,
    api: MockApi,
    mock_session: MockDbSession,
    update: Update,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")
    # This is the only participant in the meeting, user 6 is in waiting list
    create_joined_link(user=create_user(id=5, username="user_5", first_name="User 5"), meetup=meeting)

    context, _ = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_ACTION_CONFIRM_CALLBACK)

    api.assert_edit_message_called(
        context,
        update,
        edit_participants_view(meeting).with_context(
            MeetingMessages.PARTICIPANT_KICKED_OUT_SUCCESS_NO_MORE_PARTICIPANTS.get(
                lang=user_with_settings.lang, participant="user_5"
            )
        ),
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.EDIT_MEETING_KICK_OUT_ACTION_CONFIRM.with_ids(1, 5))],
    indirect=["update"],
)
async def test_edit_meeting_kickout_participant_confirm_no_longer_in_meeting(
    user_with_settings: User,
    app: StubMitupApp,
    api: MockApi,
    mock_session: MockDbSession,
    update: Update,
):
    prepare_meeting(mock_session, user_with_settings)
    context, _ = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_ACTION_CONFIRM_CALLBACK)

    api.assert_edit_message_called(
        context,
        update,
        edit_participants_view(user_with_settings.meetups[0]).with_context(
            MeetingMessages.PARTICIPANT_NO_LONGER_IN_MEETING.get(lang=user_with_settings.lang)
        ),
    )
