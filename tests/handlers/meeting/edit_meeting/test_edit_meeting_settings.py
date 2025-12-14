import pytest
from telegram import Update

from mitup_bot.handlers.edit_meeting.enums import EditMeetingHandlerId
from mitup_bot.models import Meetup, User
from mitup_bot.utils import callbacks as cb
from tests.helpers import MockDbSession, StubMitupApp, UpdateRequest, call_handler


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.EDIT_MEETING_SETTINGS.with_id(1))], indirect=True)
async def test_edit_default_options_view(
    mock_session: MockDbSession, user_with_settings: User, update: Update, app: StubMitupApp
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(meeting, query_field="id")

    expected_view = meeting.settings_view

    context, _ = await call_handler(update, app, EditMeetingHandlerId.MEETING_SETTINGS_CALLBACK)

    context.api.assert_edit_message_called(update, expected_view)


def assert_default_options_value(
    meeting: Meetup,
    handler_id: EditMeetingHandlerId,
    waiting_list: bool,
    public: bool,
    invitation: bool,
    incognito: bool,
    show_timezone: bool,
):
    expected_waiting_list = (
        not waiting_list if handler_id is EditMeetingHandlerId.SET_MEETING_WAITING_LIST_CALLBACK else waiting_list
    )
    expected_public = not public if handler_id is EditMeetingHandlerId.SET_MEETING_PUBLIC_CALLBACK else public
    expected_invitation = (
        not invitation if handler_id is EditMeetingHandlerId.SET_MEETING_ALLOW_INVITATIONS_CALLBACK else invitation
    )
    expected_incognito = (
        not incognito if handler_id is EditMeetingHandlerId.SET_MEETING_INCOGNITO_CALLBACK else incognito
    )
    expected_show_timezone = (
        not show_timezone if handler_id is EditMeetingHandlerId.SET_MEETING_SHOW_TIMEZONE_CALLBACK else show_timezone
    )

    assert meeting.waiting_list == expected_waiting_list
    assert meeting.public == expected_public
    assert meeting.allow_invitation == expected_invitation
    assert meeting.incognito == expected_incognito
    assert meeting.show_timezone == expected_show_timezone


@pytest.mark.parametrize(
    "update,handler_id",
    [
        (
            UpdateRequest(callback_query=cb.SET_MEETING_WAITING_LIST.with_id(1)),
            EditMeetingHandlerId.SET_MEETING_WAITING_LIST_CALLBACK,
        ),
        (
            UpdateRequest(callback_query=cb.SET_MEETING_PUBLIC.with_id(1)),
            EditMeetingHandlerId.SET_MEETING_PUBLIC_CALLBACK,
        ),
        (
            UpdateRequest(callback_query=cb.SET_MEETING_ALLOW_INVITATIONS.with_id(1)),
            EditMeetingHandlerId.SET_MEETING_ALLOW_INVITATIONS_CALLBACK,
        ),
        (
            UpdateRequest(callback_query=cb.SET_MEETING_INCOGNITO.with_id(1)),
            EditMeetingHandlerId.SET_MEETING_INCOGNITO_CALLBACK,
        ),
        (
            UpdateRequest(callback_query=cb.SET_MEETING_SHOW_TIMEZONE.with_id(1)),
            EditMeetingHandlerId.SET_MEETING_SHOW_TIMEZONE_CALLBACK,
        ),
    ],
    ids=["waiting_list", "public", "invitation", "incognito", "show_timezone"],
    indirect=["update"],
)
@pytest.mark.parametrize("waiting_list", [True, False], ids=["waiting_list_true", "waiting_list_false"])
@pytest.mark.parametrize("public", [True, False], ids=["public_true", "public_false"])
@pytest.mark.parametrize("invitation", [True, False], ids=["invitation_true", "invitation_false"])
@pytest.mark.parametrize("incognito", [True, False], ids=["incognito_true", "incognito_false"])
@pytest.mark.parametrize("show_timezone", [True, False], ids=["show_timezone_true", "show_timezone_false"])
async def test_callbacks_to_set_meeting_setting(
    mock_session: MockDbSession,
    user_with_settings: User,
    update: Update,
    handler_id: EditMeetingHandlerId,
    waiting_list: bool,
    public: bool,
    invitation: bool,
    incognito: bool,
    show_timezone: bool,
    app: StubMitupApp,
):
    meeting = user_with_settings.meetups[0]
    meeting.waiting_list = waiting_list
    meeting.public = public
    meeting.allow_invitation = invitation
    meeting.incognito = incognito
    meeting.show_timezone = show_timezone

    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(meeting, query_field="id")

    context, _ = await call_handler(update, app, handler_id)

    expected_view = meeting.settings_view

    context.api.assert_edit_message_called(update, expected_view)
    assert_default_options_value(meeting, handler_id, waiting_list, public, invitation, incognito, show_timezone)
