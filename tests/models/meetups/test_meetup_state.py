from datetime import UTC, datetime

import pytest

from mitup_bot.models import User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.emojis import Emojis
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages
from mitup_bot.views import ButtonConfig, MitupView
from tests.helpers import create_meetup, create_user


def test_external_view():
    owner = create_user(id=1, first_name="Owner")
    meeting = create_meetup(id=1, owner=owner, invitation=True)

    expected_view = MitupView(
        meeting.inline_message,
        [
            [
                ButtonConfig(
                    text=ButtonMessages.JOIN.get(lang=meeting.user_language),
                    callback_data=cb.JOIN.with_id(meeting.db_id),
                ),
                ButtonConfig(
                    text=ButtonMessages.INVITE.get(lang=meeting.user_language),
                    callback_data=cb.INVITE.with_id(meeting.db_id),
                ),
                ButtonConfig(
                    text=ButtonMessages.LEAVE.get(lang=meeting.user_language),
                    callback_data=cb.LEAVE.with_id(meeting.db_id),
                ),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.MAIN_MENU.back(lang=meeting.user_language),
                    callback_data=cb.MAIN_MENU,
                ),
            ],
        ],
    )

    assert expected_view == meeting.external_view


def test_edit_view(user_with_settings: User):
    meeting = user_with_settings.meetups[0]

    # Row 2 is [Date & Time → EDIT_MEETING_DATE_TIME] [Duration → EDIT_MEETING_DURATION];
    # there is no separate Date/Time row and no standalone Duration row.
    expected_view = MitupView(
        meeting.message,
        [
            [
                ButtonConfig(
                    text=ButtonMessages.TITLE.get(lang=meeting.user_language),
                    callback_data=cb.EDIT_MEETING_TITLE.with_id(meeting.db_id),
                ),
                ButtonConfig(
                    text=ButtonMessages.DESCRIPTION.get(lang=meeting.user_language),
                    callback_data=cb.EDIT_MEETING_DESCRIPTION.with_id(meeting.db_id),
                ),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.DATE_TIME.get(lang=meeting.user_language),
                    callback_data=cb.EDIT_MEETING_DATE_TIME.with_id(meeting.db_id),
                ),
                ButtonConfig(
                    text=ButtonMessages.DURATION.get(lang=meeting.user_language),
                    callback_data=cb.EDIT_MEETING_DURATION.with_id(meeting.db_id),
                ),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.PARTICIPANTS.get(lang=meeting.user_language),
                    callback_data=cb.EDIT_MEETING_PARTICIPANTS.with_id(meeting.db_id),
                ),
                ButtonConfig(
                    text=ButtonMessages.LOCATION.get(lang=meeting.user_language),
                    callback_data=cb.EDIT_MEETING_LOCATION.with_id(meeting.db_id),
                ),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.LANGUAGE.get(lang=meeting.user_language),
                    callback_data=cb.EDIT_MEETING_LANGUAGE.with_id(meeting.db_id),
                ),
                ButtonConfig(
                    text=ButtonMessages.SETTINGS.get(lang=meeting.user_language),
                    callback_data=cb.EDIT_MEETING_SETTINGS.with_id(meeting.db_id),
                ),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.DONE.get(lang=meeting.user_language),
                    callback_data=cb.SHOW_MEETING.with_id(meeting.db_id),
                ),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.MAIN_MENU.back(lang=meeting.user_language),
                    callback_data=cb.MAIN_MENU,
                ),
            ],
        ],
    )

    assert expected_view == meeting.edit_view


# ---------------------------------------------------------------------------
# _datetime_section: single clock line vs start/stop lines
# ---------------------------------------------------------------------------


def test_datetime_section_no_datetime_shows_single_clock_line(user_with_settings: User):
    from mitup_bot.utils import render

    meeting = create_meetup(id=1, owner=user_with_settings)
    meeting.datetime = None
    meeting.duration_minutes = None

    text = render(meeting._datetime_section).text

    # A single "--- CLOCK <not set>" line followed by \n
    assert text.startswith(f"--- {Emojis.CLOCK.value} ")
    assert MeetingMessages.DATE_NOT_SET.get_text(lang=meeting.lang) in text
    assert Emojis.START.value not in text
    assert Emojis.STOP.value not in text


def test_datetime_section_with_datetime_no_duration_shows_single_clock_line(user_with_settings: User):
    from mitup_bot.utils import render

    meeting = create_meetup(id=1, owner=user_with_settings)
    meeting.datetime = datetime(2024, 6, 15, 10, 0, tzinfo=UTC)
    meeting.duration_minutes = None

    text = render(meeting._datetime_section).text

    # Single clock line: "--- CLOCK Meeting time\n"
    assert text.startswith(f"--- {Emojis.CLOCK.value} ")
    assert text.endswith("\n")
    assert Emojis.START.value not in text
    assert Emojis.STOP.value not in text


def test_datetime_section_with_datetime_and_duration_shows_start_stop_lines(user_with_settings: User):
    from mitup_bot.utils import render

    meeting = create_meetup(id=1, owner=user_with_settings)
    meeting.datetime = datetime(2024, 6, 15, 10, 0, tzinfo=UTC)
    meeting.duration_minutes = 60  # 60 minutes

    text = render(meeting._datetime_section).text

    # Two lines: start line and stop line; no plain clock
    assert Emojis.CLOCK.value not in text
    assert Emojis.START.value in text
    assert Emojis.STOP.value in text
    # Both lines are present
    start_label = MeetingMessages.MEETING_START_TIME.get_text(lang=meeting.lang)
    stop_label = MeetingMessages.MEETING_STOP_TIME.get_text(lang=meeting.lang)
    assert start_label in text
    assert stop_label in text


# --- duration_view: lock toggle always visible ---


@pytest.mark.parametrize(
    "duration_minutes",
    [None, 30, 90],
    ids=["no_duration", "duration_30", "duration_90"],
)
@pytest.mark.parametrize("lock_on_start", [True, False], ids=["lock_on_start_true", "lock_on_start_false"])
def test_duration_view_lock_toggle_always_visible(
    user_with_settings: User,
    duration_minutes: int | None,
    lock_on_start: bool,
):
    meeting = create_meetup(id=1, owner=user_with_settings)
    meeting.duration_minutes = duration_minutes
    meeting.lock_on_start = lock_on_start

    view = meeting.duration_view

    lock_cb = cb.SET_MEETING_LOCK_ON_START.with_id(meeting.db_id)
    lock_buttons = [btn for row in view.keyboard for btn in row if btn.callback_data == lock_cb]

    # Lock toggle must be present regardless of whether duration_minutes is set
    assert len(lock_buttons) == 1  # exactly one lock toggle button


def test_duration_view_keyboard_row0_has_set_and_lock_buttons_when_no_duration(user_with_settings: User):
    meeting = create_meetup(id=1, owner=user_with_settings)
    meeting.duration_minutes = None

    view = meeting.duration_view

    row0 = view.keyboard[0]
    assert len(row0) == 2  # "Set duration" + LOCK_ON_START toggle
    assert row0[0].callback_data == cb.SET_MEETING_DURATION.with_id(meeting.db_id)
    assert row0[1].callback_data == cb.SET_MEETING_LOCK_ON_START.with_id(meeting.db_id)
    # No delete row when duration is not set
    delete_cb = cb.CLEAR_MEETING_DURATION.with_id(meeting.db_id)
    all_buttons = [btn for row in view.keyboard for btn in row]
    assert not any(btn.callback_data == delete_cb for btn in all_buttons)


def test_duration_view_keyboard_row0_has_set_and_lock_and_row1_has_delete_when_duration_set(user_with_settings: User):
    meeting = create_meetup(id=1, owner=user_with_settings)
    meeting.duration_minutes = 60

    view = meeting.duration_view

    row0 = view.keyboard[0]
    assert len(row0) == 2  # "Set duration" + LOCK_ON_START toggle
    assert row0[0].callback_data == cb.SET_MEETING_DURATION.with_id(meeting.db_id)
    assert row0[1].callback_data == cb.SET_MEETING_LOCK_ON_START.with_id(meeting.db_id)
    # Delete button is in row 1
    row1 = view.keyboard[1]
    assert len(row1) == 1  # only "Delete duration"


# --- _plain_datetime fallback branch ---


def test_plain_datetime_fallback_when_no_datetime_set(user_with_settings: User):
    """_plain_datetime must return the DATE_NOT_SET message when meeting.datetime is None."""
    meeting = create_meetup(id=1, owner=user_with_settings)
    meeting.datetime = None

    result = meeting._plain_datetime

    # Line 251: the else-branch returning the localised "date not set" string
    expected = MeetingMessages.DATE_NOT_SET.get_text(lang=meeting.lang)
    assert result == expected  # plain str, not FormattedText


def test_plain_datetime_formatted_when_datetime_set(user_with_settings: User):
    """_plain_datetime must return a UTC-formatted string when meeting.datetime is set."""
    meeting = create_meetup(id=1, owner=user_with_settings)
    meeting.datetime = datetime(2024, 1, 12, 12, 30, tzinfo=UTC)

    result = meeting._plain_datetime

    assert result == "2024-01-12 12:30"  # f"{self.datetime:%Y-%m-%d %H:%M}"


# --- main_view and external_view: join/leave row hidden when locked and in-progress ---


def test_main_view_hides_join_leave_row_when_locked_and_in_progress(user_with_settings: User):
    """main_view must omit the join/leave row when lock_on_start=True and the meeting is in progress."""
    import datetime as dt
    from datetime import timedelta

    now = dt.datetime.now(dt.UTC)
    meeting = create_meetup(id=1, owner=user_with_settings)
    # Set datetime to 5 minutes ago and duration to 60 min so is_in_progress is True
    meeting.datetime = now - timedelta(minutes=5)
    meeting.duration_minutes = 60
    meeting.lock_on_start = True

    assert meeting.is_in_progress  # guard: the branch condition must be True

    view = meeting.main_view

    join_cb = cb.JOIN.with_id(meeting.db_id)
    join_buttons = [btn for row in view.keyboard for btn in row if btn.callback_data == join_cb]
    # Lines 427→429: the join/leave row is skipped when locked and in progress
    assert len(join_buttons) == 0


def test_external_view_hides_join_leave_row_when_locked_and_in_progress(user_with_settings: User):
    """external_view must omit the join/leave row when lock_on_start=True and the meeting is in progress."""
    import datetime as dt
    from datetime import timedelta

    now = dt.datetime.now(dt.UTC)
    meeting = create_meetup(id=1, owner=user_with_settings)
    meeting.datetime = now - timedelta(minutes=5)
    meeting.duration_minutes = 60
    meeting.lock_on_start = True

    assert meeting.is_in_progress  # guard: the branch condition must be True

    view = meeting.external_view

    join_cb = cb.JOIN.with_id(meeting.db_id)
    join_buttons = [btn for row in view.keyboard for btn in row if btn.callback_data == join_cb]
    # Lines 461→463: the join/leave row is skipped when locked and in progress
    assert len(join_buttons) == 0


def test_build_inline_keyboard_hides_join_leave_row_when_locked_and_in_progress(user_with_settings: User):
    """build_inline_keyboard must omit the join/leave row when is_locked_and_in_progress=True."""
    meeting = create_meetup(id=1, owner=user_with_settings)

    keyboard = meeting.build_inline_keyboard(is_locked_and_in_progress=True)

    join_cb = cb.JOIN.with_id(meeting.db_id)
    join_buttons = [btn for row in keyboard for btn in row if btn.callback_data == join_cb]
    # Lines 633→636: the join/leave row is skipped
    assert len(join_buttons) == 0
