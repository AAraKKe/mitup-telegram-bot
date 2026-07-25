import logging
import re

import pytest
from telegram import Update

from mitup_bot.callback_data import MeetingListSource
from mitup_bot.exceptions import MalformedCallbackData, MeetingNotOwnedError
from mitup_bot.handlers.meeting.enums import MeetingHandlerId
from mitup_bot.handlers.meeting.show_meeting import callback_query_show_meeting
from mitup_bot.handlers.meeting.utils import meeting_detail_back_button
from mitup_bot.models import User
from mitup_bot.monitoring import MetricKey
from mitup_bot.utils import ButtonMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import meeting as meeting_views
from tests.helpers import (
    HandlerContext,
    MockDbSession,
    StubMitupContext,
    UpdateRequest,
    call_handler,
    create_joined_link,
    create_meetup,
    create_settings,
    create_user,
)
from tests.helpers.monitoring import MetricAssertions


@pytest.mark.parametrize("update", ([UpdateRequest(callback_query=cb.SHOW_MEETING.with_id(1))]), indirect=True)
async def test_show_meeting_calls_to_meeting_view_when_meeting_is_set(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    # Ensure meetups[0] is ID 1 and owned by user_with_settings for the test's UpdateRequest
    # The user_with_settings fixture should ideally set this up.
    # If not, we need to adjust it here or the fixture.
    # Assuming user_with_settings.meetups is populated by the fixture and meetups[0] has id=1.
    if not user_with_settings.meetups or user_with_settings.meetups[0].db_id != 1:
        # If fixture doesn't provide meetups[0] as id=1, create/modify it
        # This is a fallback, ideally fixture is canonical
        meeting1 = create_meetup(id=1, title="Test Meeting 1 for Show", owner=user_with_settings)
        if user_with_settings.meetups:
            # Try to replace or ensure it's the first for consistency if test relies on meetups[0]
            if user_with_settings.meetups[0].db_id == 1:
                user_with_settings.meetups[0] = meeting1  # replace if id matches but owner might be wrong
            else:
                user_with_settings.meetups.insert(0, meeting1)  # add at beginning
        else:
            user_with_settings.meetups = [meeting1]
    elif user_with_settings.meetups[0].owner != user_with_settings:
        user_with_settings.meetups[0].owner = user_with_settings

    target_meeting = user_with_settings.meetups[0]
    assert target_meeting.db_id == 1, "Target meeting for test should have ID 1"
    assert target_meeting.owner == user_with_settings, "Target meeting not owned by user_with_settings"

    mock_session.add_object(target_meeting)

    context, _ = await call_handler(MeetingHandlerId.SHOW_MEETING_CALLBACK, handler_context=handler_context)

    expected_view = meeting_views.main_view(target_meeting)
    context.api.assert_edit_message_called(update, expected_view)


async def test_show_meeting_stops_for_meeting_not_owned(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
):
    match = re.match(cb.SHOW_MEETING.pattern, "show;meeting:4")  # Target meeting ID 4
    assert match is not None

    context.matches = [match]
    mock_session.add_object(user_with_settings, "tg_user_id")

    # Create another user to be the owner of meeting 4
    # Ensure this other_user has a different DB id from user_with_settings
    other_user_db_id = (user_with_settings.id + 1) if user_with_settings.id is not None else 999
    other_user_tg_id = (user_with_settings.tg_user_id + 1) if user_with_settings.tg_user_id is not None else 9990
    other_user = User(id=other_user_db_id, tg_user_id=other_user_tg_id, first_name="Other Owner")
    mock_session.add_object(other_user, "tg_user_id")

    # Create meeting 4, owned by other_user
    other_meeting = create_meetup(id=4, owner=other_user, title="Other's Meeting")
    mock_session.add_object(other_meeting)

    with pytest.raises(MeetingNotOwnedError) as raised:
        await callback_query_show_meeting(update, context)

    # Nothing about the meeting reaches the user: the rejection carries only the main-menu redirect,
    # which the error handler renders.
    context.api.assert_edit_message_not_called()
    context.api.assert_send_message_not_called()
    assert str(raised.value) == (
        "User tried 'Show meeting' with a meeting that does not belong to them. "
        f"Meeting id: 4, user id: {user_with_settings.id}"
    )


async def test_show_meeting_renders_external_view_for_joined_but_not_owned_meeting(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    metrics: MetricAssertions,
    caplog: pytest.LogCaptureFixture,
):
    """Tapping a joined (non-owned) meeting shows the non-owner view instead of bouncing to the main menu."""
    match = re.match(cb.SHOW_MEETING.pattern, "show;meeting:7")  # Meeting joined but not owned
    assert match is not None
    context.matches = [match]

    owner = create_user(id=999, tg_user_id=9990, first_name="Owner", settings=create_settings(id=2))
    joined_meeting = create_meetup(id=7, owner=owner, title="Owner's Meeting")
    user_with_settings.joined_links = [create_joined_link(user=user_with_settings, meetup=joined_meeting)]

    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(joined_meeting)

    with caplog.at_level(logging.WARNING):
        await callback_query_show_meeting(update, context)
        await context.flush_metrics()

        # The ownership bounce is fully gone: no warning log and no MeetingNotOwned error emitted.
        assert caplog.text == ""
        metrics.assert_not_emitted(name=MetricKey.ERROR.with_prefix("MeetingNotOwned"), value=1)

    # The handler builds the back button in the viewer's language (no origin in the callback, so it
    # targets the main menu), unlike external_view's owner-language default back button.
    expected_view = meeting_views.external_view(
        joined_meeting, back_button=meeting_detail_back_button(None, 1, user_with_settings.lang)
    )
    context.api.assert_edit_message_called(update, expected_view)
    context.api.assert_send_message_not_called()


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SHOW_MEETING.with_page(7, 2, MeetingListSource.JOINED))],
    indirect=True,
)
async def test_show_meeting_external_view_back_button_targets_originating_list_page(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    """A joined (non-owned) meeting opened from the joined list must render the non-owner view with
    a back button returning to the originating joined-list page."""
    owner = create_user(id=999, tg_user_id=9990, first_name="Owner", settings=create_settings(id=2))
    joined_meeting = create_meetup(id=7, owner=owner, title="Owner's Meeting")
    user_with_settings.joined_links = [create_joined_link(user=user_with_settings, meetup=joined_meeting)]
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(joined_meeting)

    context, _ = await call_handler(MeetingHandlerId.SHOW_MEETING_CALLBACK, handler_context=handler_context)

    expected_view = meeting_views.external_view(
        joined_meeting, back_button=meeting_detail_back_button(MeetingListSource.JOINED, 2, user_with_settings.lang)
    )
    context.api.assert_edit_message_called(update, expected_view)


async def test_show_meeting_fails_without_callback_query_data(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
):
    match = re.match(cb.SHOW_MEETING.pattern, "show;meeting:")
    assert match is not None

    context.matches = [match]
    with pytest.raises(MalformedCallbackData):
        await callback_query_show_meeting(update, context)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.SHOW_MEETING.with_page(99, 3))], indirect=True)
async def test_show_meeting_deleted_fallback_returns_to_originating_page(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    """When a meeting opened from active-list page 3 no longer exists, the fallback back button
    must return to page 3 rather than page 1."""
    mock_session.add_object(user_with_settings, "tg_user_id")
    # Meeting 99 is never registered, so the guard takes the "deleted" path
    # and renders the custom back keyboard.

    context, _ = await call_handler(MeetingHandlerId.SHOW_MEETING_CALLBACK, handler_context=handler_context)

    view = context.api.call_args("edit_message").kwargs["view"]
    back_button = view.keyboard[-1][-1]
    assert back_button.callback_data == cb.SHOW_ACTIVE_MEETING_PAGE.with_id(3)


@pytest.mark.parametrize(
    "update, expected_text, expected_callback",
    [
        (
            UpdateRequest(callback_query=cb.SHOW_MEETING.with_page(1, 2, MeetingListSource.ACTIVE)),
            ButtonMessages.ACTIVE_MEETINGS,
            cb.SHOW_ACTIVE_MEETING_PAGE.with_id(2),
        ),
        (
            UpdateRequest(callback_query=cb.SHOW_MEETING.with_page(1, 2, MeetingListSource.JOINED)),
            ButtonMessages.JOINED_MEETINGS,
            cb.SHOW_JOINED_MEETINGS_PAGE.with_id(2),
        ),
        (
            UpdateRequest(callback_query=cb.SHOW_MEETING.with_id(1)),
            ButtonMessages.MAIN_MENU,
            cb.MAIN_MENU,
        ),
    ],
    ids=["from_active_list", "from_joined_list", "no_origin"],
    indirect=["update"],
)
async def test_show_meeting_back_button_targets_originating_list_page(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    expected_text,
    expected_callback,
):
    """The detail back button must point at the list page encoded in the callback data, falling
    back to the main menu when the callback carries no origin."""
    meeting = create_meetup(id=1, title="Meeting 1", owner=user_with_settings)
    user_with_settings.meetups = [meeting]
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(meeting)

    context, _ = await call_handler(MeetingHandlerId.SHOW_MEETING_CALLBACK, handler_context=handler_context)

    view = context.api.call_args("edit_message").kwargs["view"]
    back_button = view.keyboard[-1][-1]
    assert back_button.text == expected_text.back(lang=user_with_settings.lang)
    assert back_button.callback_data == expected_callback


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SHOW_MEETING.with_page(99, 3, MeetingListSource.JOINED))],
    indirect=True,
)
async def test_show_meeting_deleted_fallback_targets_joined_list(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    """When an inaccessible meeting was opened from the joined list, the fallback button must
    offer the joined list at the originating page rather than the active list."""
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(MeetingHandlerId.SHOW_MEETING_CALLBACK, handler_context=handler_context)

    view = context.api.call_args("edit_message").kwargs["view"]
    back_button = view.keyboard[-1][-1]
    assert back_button.callback_data == cb.SHOW_JOINED_MEETINGS_PAGE.with_id(3)
