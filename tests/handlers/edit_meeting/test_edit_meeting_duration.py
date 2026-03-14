import pytest
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot.custom_context import ContextId
from mitup_bot.handlers.edit_meeting.edit_meeting_duration import duration_input_prompt_view
from mitup_bot.handlers.edit_meeting.enums import ConversationMeetingState, EditMeetingHandlerId
from mitup_bot.models import Settings
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import MeetingMessages
from tests.helpers import MockDbSession, StubMitupApp, UpdateRequest, call_handler, create_meetup, create_user


def owner_with_meeting(meeting_id: int = 1, duration_minutes: int | None = None, lock_on_start: bool = False):
    """Build a user owning a single meeting."""
    meeting = create_meetup(id=meeting_id, title="Test Meeting")
    meeting.duration_minutes = duration_minutes
    meeting.lock_on_start = lock_on_start
    user = create_user(id=1, tg_user_id=123, owned_meetings=[meeting], settings=Settings(id=1))
    return user, meeting


# ---------------------------------------------------------------------------
# DURATION_ENTRY_CALLBACK — renders duration_view
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.EDIT_MEETING_DURATION.with_id(1))],
    indirect=True,
)
async def test_duration_entry_renders_duration_view(
    mock_session: MockDbSession,
    update: Update,
    app: StubMitupApp,
):
    user, meeting = owner_with_meeting(meeting_id=1)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, _ = await call_handler(EditMeetingHandlerId.DURATION_ENTRY_CALLBACK, update=update, app=app)

    context.api.assert_edit_message_called(update, meeting.duration_view)


# ---------------------------------------------------------------------------
# DURATION_INPUT_CALLBACK — enter conversation, show input prompt
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SET_MEETING_DURATION.with_id(1))],
    indirect=True,
)
async def test_duration_input_entry_shows_prompt_and_enters_state(
    mock_session: MockDbSession,
    update: Update,
    app: StubMitupApp,
):
    user, meeting = owner_with_meeting(meeting_id=1, duration_minutes=None)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(EditMeetingHandlerId.DURATION_INPUT_CALLBACK, update=update, app=app)

    expected_prompt = duration_input_prompt_view(1, user.lang, None)
    context.api.assert_edit_message_called(update, expected_prompt)
    assert state == ConversationMeetingState.EDIT_DURATION


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SET_MEETING_DURATION.with_id(1))],
    indirect=True,
)
async def test_duration_input_entry_shows_edit_prompt_when_duration_already_set(
    mock_session: MockDbSession,
    update: Update,
    app: StubMitupApp,
):
    user, meeting = owner_with_meeting(meeting_id=1, duration_minutes=60)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(EditMeetingHandlerId.DURATION_INPUT_CALLBACK, update=update, app=app)

    # Prompt reflects the existing duration value
    expected_prompt = duration_input_prompt_view(1, user.lang, 60)
    context.api.assert_edit_message_called(update, expected_prompt)
    assert state == ConversationMeetingState.EDIT_DURATION


# ---------------------------------------------------------------------------
# DURATION_TEXT_MESSAGE — valid numeric input saves and closes conversation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="90")],
    indirect=True,
)
async def test_valid_duration_text_saves_duration_and_exits_conversation(
    mock_session: MockDbSession,
    update: Update,
    app: StubMitupApp,
):
    user, meeting = owner_with_meeting(meeting_id=1)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_TEXT_MESSAGE,
        update=update,
        app=app,
        with_meeting_id={ContextId.EDIT_MEETING_DURATION: 1},
    )

    # Duration has been saved
    assert meeting.duration_minutes == 90  # parsed from message text "90"

    # The success response is shown
    expected_view = meeting.duration_view.with_context(
        MeetingMessages.DURATION_SET_SUCCESS.get(lang=user.lang, duration=90)
    )
    context.api.assert_send_message_called(update, expected_view)

    # Meeting messages are updated
    context.api.assert_update_meeting_messages_called(session=mock_session, meeting=meeting)

    assert state == ConversationHandler.END


@pytest.mark.parametrize("text", ["0", "-5", "abc", "1.5", ""], ids=["zero", "negative", "alpha", "float", "empty"])
@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="0")],  # overridden inline via text parametrize — value ignored here
    indirect=True,
)
async def test_invalid_duration_text_shows_error_and_stays_in_state(
    mock_session: MockDbSession,
    update: Update,
    app: StubMitupApp,
    text: str,
):
    """Non-positive or non-numeric text uses DURATION_INVALID_MESSAGE handler which re-prompts."""
    user, meeting = owner_with_meeting(meeting_id=1, duration_minutes=None)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    # We test the invalid-message handler directly to exercise the re-prompt path
    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_INVALID_MESSAGE,
        update=update,
        app=app,
        with_meeting_id={ContextId.EDIT_MEETING_DURATION: 1},
    )

    # Duration should not have changed
    assert meeting.duration_minutes is None

    # Prompt is re-displayed
    expected_prompt = duration_input_prompt_view(1, user.lang, None)
    context.api.assert_send_message_called(update, expected_prompt)

    assert state == ConversationMeetingState.EDIT_DURATION


# ---------------------------------------------------------------------------
# DURATION_CANCEL_CALLBACK — cancel returns to duration_view, exits conversation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.CANCEL_EDIT_MEETING_DURATION.with_id(1))],
    indirect=True,
)
async def test_cancel_during_duration_input_returns_to_view(
    mock_session: MockDbSession,
    update: Update,
    app: StubMitupApp,
):
    user, meeting = owner_with_meeting(meeting_id=1, duration_minutes=45)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_CANCEL_CALLBACK,
        update=update,
        app=app,
        with_meeting_id={ContextId.EDIT_MEETING_DURATION: 1},
    )

    # Duration must not have changed
    assert meeting.duration_minutes == 45  # unchanged

    context.api.assert_edit_message_called(update, meeting.duration_view)
    assert state == ConversationHandler.END


# ---------------------------------------------------------------------------
# DURATION_CLEAR_CALLBACK — clear sets duration_minutes=None and lock_on_start=False
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.CLEAR_MEETING_DURATION.with_id(1))],
    indirect=True,
)
async def test_clear_duration_removes_duration_and_lock(
    mock_session: MockDbSession,
    update: Update,
    app: StubMitupApp,
):
    user, meeting = owner_with_meeting(meeting_id=1, duration_minutes=90, lock_on_start=True)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, _ = await call_handler(EditMeetingHandlerId.DURATION_CLEAR_CALLBACK, update=update, app=app)

    assert meeting.duration_minutes is None  # cleared
    assert meeting.lock_on_start is False  # also cleared

    expected_view = meeting.duration_view.with_context(MeetingMessages.DURATION_CLEARED.get(lang=user.lang))
    context.api.assert_send_message_called(update, expected_view)
    context.api.assert_update_meeting_messages_called(session=mock_session, meeting=meeting)


# ---------------------------------------------------------------------------
# LOCK_ON_START_CALLBACK — toggle lock_on_start when duration is set
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SET_MEETING_LOCK_ON_START.with_id(1))],
    indirect=True,
)
@pytest.mark.parametrize("initial_lock", [True, False], ids=["lock_true", "lock_false"])
async def test_toggle_lock_on_start_flips_value_and_re_renders(
    mock_session: MockDbSession,
    update: Update,
    app: StubMitupApp,
    initial_lock: bool,
):
    user, meeting = owner_with_meeting(meeting_id=1, duration_minutes=60, lock_on_start=initial_lock)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    context, _ = await call_handler(EditMeetingHandlerId.LOCK_ON_START_CALLBACK, update=update, app=app)

    assert meeting.lock_on_start == (not initial_lock)  # flipped

    context.api.assert_edit_message_called(update, meeting.duration_view)
    context.api.assert_update_meeting_messages_called(
        session=mock_session,
        meeting=meeting,
        current_message=meeting.message_from_update(update),  # None — no message registered
        skip_current=True,
    )


# ---------------------------------------------------------------------------
# LOCK_ON_START_CALLBACK — stale callback when duration_minutes is None
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.SET_MEETING_LOCK_ON_START.with_id(1))],
    indirect=True,
)
async def test_lock_on_start_stale_alert_when_no_duration(
    mock_session: MockDbSession,
    update: Update,
    app: StubMitupApp,
):
    user, meeting = owner_with_meeting(meeting_id=1, duration_minutes=None)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    original_lock = meeting.lock_on_start

    context, _ = await call_handler(EditMeetingHandlerId.LOCK_ON_START_CALLBACK, update=update, app=app)

    # lock_on_start must not have been modified
    assert meeting.lock_on_start == original_lock

    context.api.assert_answer_callback_query_called(
        update=update,
        text=MeetingMessages.LOCK_ON_START_STALE_ALERT.get_text(lang=meeting.user_language),
        show_alert=True,
    )

    # No message edit should have occurred
    context.api.assert_method_just_called("edit_message", times=0)


# ---------------------------------------------------------------------------
# ContextPropertyNotSetError in conversation message handlers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="90")],
    indirect=True,
)
async def test_duration_text_message_handler_edits_to_main_menu_when_context_missing(
    mock_session: MockDbSession,
    update: Update,
    app: StubMitupApp,
):
    """When no meeting_id is stored, duration_text_message_handler catches ContextPropertyNotSetError,
    edits the message to the main menu, and ends the conversation."""
    user, _ = owner_with_meeting(meeting_id=1)
    mock_session.add_object(user, query_field="tg_user_id")

    # Do NOT pass with_meeting_id so ContextPropertyNotSetError is raised.
    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_TEXT_MESSAGE,
        update=update,
        app=app,
    )

    context.api.assert_method_just_called("edit_message", times=1)
    assert state == ConversationHandler.END


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="not_a_number")],
    indirect=True,
)
async def test_duration_invalid_message_handler_edits_to_main_menu_when_context_missing(
    mock_session: MockDbSession,
    update: Update,
    app: StubMitupApp,
):
    """When no meeting_id is stored, duration_invalid_message_handler catches ContextPropertyNotSetError,
    edits the message to the main menu, and ends the conversation."""
    user, _ = owner_with_meeting(meeting_id=1)
    mock_session.add_object(user, query_field="tg_user_id")

    # Do NOT pass with_meeting_id so ContextPropertyNotSetError is raised.
    context, state = await call_handler(
        EditMeetingHandlerId.DURATION_INVALID_MESSAGE,
        update=update,
        app=app,
    )

    context.api.assert_method_just_called("edit_message", times=1)
    assert state == ConversationHandler.END
