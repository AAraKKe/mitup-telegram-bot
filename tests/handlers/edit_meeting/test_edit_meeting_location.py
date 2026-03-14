import logging

import pytest
from telegram import Location, Update
from telegram.ext import ConversationHandler

from mitup_bot.custom_context import ContextId
from mitup_bot.handlers.edit_meeting.enums import EditMeetingHandlerId
from mitup_bot.views import factory
from tests.helpers import MockDbSession, StubMitupApp, UpdateRequest, call_handler, owner_with_meeting

# ---------------------------------------------------------------------------
# LOCATION_NAME_MESSAGE — ContextPropertyNotSetError path (line 178-180)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="My Location")],
    indirect=True,
)
async def test_edit_location_name_message_edits_to_main_menu_when_context_missing(
    caplog: pytest.LogCaptureFixture,
    mock_session: MockDbSession,
    update: Update,
    app: StubMitupApp,
):
    """When no meeting_id is stored in context, edit_meeting_location_name catches
    ContextPropertyNotSetError, edits the message to the main menu view, and ends the conversation."""
    user, meeting = owner_with_meeting(meeting_id=1)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    # Do NOT pass with_meeting_id so ContextPropertyNotSetError is raised.
    with caplog.at_level(logging.ERROR):
        context, state = await call_handler(
            EditMeetingHandlerId.LOCATION_NAME_MESSAGE,
            update=update,
            app=app,
        )
        assert any(r.levelno == logging.ERROR for r in caplog.records)
        assert "meeting_id" in caplog.text

    assert state == ConversationHandler.END
    context.api.assert_edit_message_called(update, factory.main_menu_view(lang=user.lang))


# ---------------------------------------------------------------------------
# LOCATION_COORDINATES_WRONG_MESSAGE — ContextPropertyNotSetError path (line 209-213)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="Message instead of coordinates")],
    indirect=True,
)
async def test_edit_location_coordinates_wrong_message_edits_to_main_menu_when_context_missing(
    caplog: pytest.LogCaptureFixture,
    mock_session: MockDbSession,
    update: Update,
    app: StubMitupApp,
):
    """When no meeting_id is stored in context, edit_coordinates_without_location catches
    ContextPropertyNotSetError, edits the message to the main menu view, and ends the conversation."""
    user, meeting = owner_with_meeting(meeting_id=1)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    # Do NOT pass with_meeting_id so ContextPropertyNotSetError is raised.
    with caplog.at_level(logging.ERROR):
        context, state = await call_handler(
            EditMeetingHandlerId.LOCATION_COORDINATES_WRONG_MESSAGE,
            update=update,
            app=app,
        )
        assert any(r.levelno == logging.ERROR for r in caplog.records)
        assert "meeting_id" in caplog.text

    assert state == ConversationHandler.END
    context.api.assert_edit_message_called(update, factory.main_menu_view(lang=user.lang))


# ---------------------------------------------------------------------------
# LOCATION_COORDINATES_MESSAGE — ContextPropertyNotSetError path (line 209-213)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(location=Location(longitude=1.0, latitude=2.0))],
    indirect=True,
)
async def test_edit_location_coordinates_message_edits_to_main_menu_when_context_missing(
    caplog: pytest.LogCaptureFixture,
    mock_session: MockDbSession,
    update: Update,
    app: StubMitupApp,
):
    """When no meeting_id is stored in context, edit_meeting_location_coordinates catches
    ContextPropertyNotSetError, edits the message to the main menu view, and ends the conversation."""
    user, meeting = owner_with_meeting(meeting_id=1)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    # Do NOT pass with_meeting_id so ContextPropertyNotSetError is raised.
    with caplog.at_level(logging.ERROR):
        context, state = await call_handler(
            EditMeetingHandlerId.LOCATION_COORDINATES_MESSAGE,
            update=update,
            app=app,
        )
        assert any(r.levelno == logging.ERROR for r in caplog.records)
        assert "meeting_id" in caplog.text

    assert state == ConversationHandler.END
    context.api.assert_edit_message_called(update, factory.main_menu_view(lang=user.lang))


# ---------------------------------------------------------------------------
# LOCATION_COORDINATES_MESSAGE — meeting is None path (line 208)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(location=Location(longitude=1.0, latitude=2.0))],
    indirect=True,
)
async def test_edit_location_coordinates_message_ends_when_user_does_not_own_meeting(
    mock_session: MockDbSession,
    update: Update,
    app: StubMitupApp,
):
    """When meeting_id is set but user doesn't own that meeting, user_owns_meeting returns None,
    the handler redirects to main_menu_view and returns END."""
    user, meeting = owner_with_meeting(meeting_id=1)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    # Pass with_meeting_id=999 — user only owns meeting 1, so guard returns None.
    context, state = await call_handler(
        EditMeetingHandlerId.LOCATION_COORDINATES_MESSAGE,
        update=update,
        app=app,
        with_meeting_id={ContextId.EDIT_MEETING_LOCATION_COORDINATES: 999},
    )

    assert state == ConversationHandler.END
    context.api.assert_method_just_called("edit_message", times=1)
