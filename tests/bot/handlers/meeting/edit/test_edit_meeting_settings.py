from collections.abc import Callable

import pytest
from telegram import Update

from mitup_bot.datetimes import DateFormat
from mitup_bot.handlers.meeting.edit.enums import EditMeetingHandlerId
from mitup_bot.models import Meetup, User
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import MitupView
from mitup_bot.views import meeting as meeting_views
from tests.helpers import HandlerContext, MockDbSession, UpdateRequest, call_handler


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.EDIT_MEETING_SETTINGS.with_id(1))], indirect=True)
async def test_edit_default_options_view(
    mock_session: MockDbSession, user_with_settings: User, update: Update, handler_context: HandlerContext
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(meeting, query_field="id")

    expected_view = meeting_views.settings_view(meeting)

    context, _ = await call_handler(EditMeetingHandlerId.MEETING_SETTINGS_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_called(update, expected_view)


@pytest.mark.parametrize(
    "update,handler_id,sub_card",
    [
        (
            UpdateRequest(callback_query=cb.OPEN_MEETING_BEHAVIOR.with_id(1)),
            EditMeetingHandlerId.OPEN_MEETING_BEHAVIOR,
            meeting_views.behavior_view,
        ),
        (
            UpdateRequest(callback_query=cb.OPEN_MEETING_TIME_FORMAT.with_id(1)),
            EditMeetingHandlerId.OPEN_MEETING_TIME_FORMAT,
            meeting_views.time_format_view,
        ),
    ],
    ids=["behavior", "time_format"],
    indirect=["update"],
)
async def test_opening_a_sub_card_draws_it_over_the_settings_card(
    mock_session: MockDbSession,
    user_with_settings: User,
    update: Update,
    handler_id: EditMeetingHandlerId,
    sub_card: Callable[[Meetup], MitupView],
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(meeting, query_field="id")

    context, _ = await call_handler(handler_id, handler_context=handler_context)

    context.api.assert_edit_message_called(update, sub_card(meeting))
    context.api.assert_update_meeting_messages_not_called()


def assert_default_options_value(
    meeting: Meetup,
    handler_id: EditMeetingHandlerId,
    waiting_list: bool,
    public: bool,
    invitation: bool,
    incognito: bool,
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

    assert meeting.waiting_list == expected_waiting_list
    assert meeting.public == expected_public
    assert meeting.allow_invitation == expected_invitation
    assert meeting.incognito == expected_incognito


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
    ],
    ids=["waiting_list", "public", "invitation", "incognito"],
    indirect=["update"],
)
@pytest.mark.parametrize("waiting_list", [True, False], ids=["waiting_list_true", "waiting_list_false"])
@pytest.mark.parametrize("public", [True, False], ids=["public_true", "public_false"])
@pytest.mark.parametrize("invitation", [True, False], ids=["invitation_true", "invitation_false"])
@pytest.mark.parametrize("incognito", [True, False], ids=["incognito_true", "incognito_false"])
async def test_callbacks_to_set_meeting_setting(
    mock_session: MockDbSession,
    user_with_settings: User,
    update: Update,
    handler_id: EditMeetingHandlerId,
    waiting_list: bool,
    public: bool,
    invitation: bool,
    incognito: bool,
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    meeting.waiting_list = waiting_list
    meeting.public = public
    meeting.allow_invitation = invitation
    meeting.incognito = incognito

    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(meeting, query_field="id")

    context, _ = await call_handler(handler_id, handler_context=handler_context)

    expected_view = meeting_views.behavior_view(meeting)

    context.api.assert_edit_message_called(update, expected_view)
    assert_default_options_value(meeting, handler_id, waiting_list, public, invitation, incognito)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SET_MEETING_LOCK_ON_START.with_id(1))],
    indirect=True,
)
@pytest.mark.parametrize("has_start", [True, False], ids=["with_start", "no_start"])
@pytest.mark.parametrize("initial_lock", [True, False], ids=["lock_true", "lock_false"])
async def test_lock_on_start_toggle(
    mock_session: MockDbSession,
    user_with_settings: User,
    update: Update,
    handler_context: HandlerContext,
    has_start: bool,
    initial_lock: bool,
):
    """The lock toggles from the behavior card like every other setting, whether or not the
    meeting has a start time yet: without one the setting stays dormant but editable."""
    meeting = user_with_settings.meetups[0]
    if not has_start:
        meeting.datetime = None
    meeting.lock_on_start = initial_lock

    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(meeting, query_field="id")

    context, _ = await call_handler(EditMeetingHandlerId.LOCK_ON_START_CALLBACK, handler_context=handler_context)

    assert meeting.lock_on_start == (not initial_lock)

    context.api.assert_edit_message_called(update, meeting_views.behavior_view(meeting))
    context.api.assert_update_meeting_messages_called(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )


@pytest.mark.parametrize(
    "update,handler_id,attribute",
    [
        (
            UpdateRequest(callback_query=cb.SET_MEETING_SHOW_TIMEZONE.with_id(1)),
            EditMeetingHandlerId.SET_MEETING_SHOW_TIMEZONE_CALLBACK,
            "show_timezone",
        ),
        (
            UpdateRequest(callback_query=cb.SET_MEETING_CLOCK_24H.with_id(1)),
            EditMeetingHandlerId.SET_MEETING_CLOCK_24H_CALLBACK,
            "clock_24h",
        ),
    ],
    ids=["show_timezone", "clock_24h"],
    indirect=["update"],
)
@pytest.mark.parametrize("initial_value", [True, False], ids=["initially_true", "initially_false"])
async def test_time_format_toggle_flips_the_setting_and_rewrites_every_card(
    mock_session: MockDbSession,
    user_with_settings: User,
    update: Update,
    handler_id: EditMeetingHandlerId,
    attribute: str,
    initial_value: bool,
    handler_context: HandlerContext,
):
    """A time format change rewrites how every stored card reads, so the fan-out is the point."""
    meeting = user_with_settings.meetups[0]
    setattr(meeting, attribute, initial_value)

    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(meeting, query_field="id")

    context, _ = await call_handler(handler_id, handler_context=handler_context)

    assert getattr(meeting, attribute) == (not initial_value)

    context.api.assert_edit_message_called(update, meeting_views.time_format_view(meeting))
    context.api.assert_update_meeting_messages_called(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )


@pytest.mark.parametrize(
    "update,picked",
    [
        (UpdateRequest(callback_query=cb.SET_MEETING_DATE_FORMAT.with_ids(1, index)), date_format)
        for index, date_format in enumerate(DateFormat)
    ],
    ids=[date_format.value for date_format in DateFormat],
    indirect=["update"],
)
async def test_setting_the_date_format_stores_the_picked_one_and_rewrites_every_card(
    mock_session: MockDbSession,
    user_with_settings: User,
    update: Update,
    picked: DateFormat,
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    meeting.date_format = DateFormat.DEFAULT

    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(meeting, query_field="id")

    context, _ = await call_handler(
        EditMeetingHandlerId.SET_MEETING_DATE_FORMAT_CALLBACK, handler_context=handler_context
    )

    assert meeting.date_format is picked

    context.api.assert_edit_message_called(update, meeting_views.time_format_view(meeting))
    context.api.assert_update_meeting_messages_called(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )
