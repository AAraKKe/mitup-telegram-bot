import datetime as dt
from datetime import UTC, datetime, timedelta

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

    # Row 2 is [When → EDIT_MEETING_WHEN] — a single button replaces "Date & Time" + "Duration".
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
                    text=ButtonMessages.WHEN.get(lang=meeting.user_language),
                    callback_data=cb.EDIT_MEETING_WHEN.with_id(meeting.db_id),
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
    meeting.end_datetime = None

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
    meeting.end_datetime = None

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
    meeting.end_datetime = datetime(2024, 6, 15, 11, 0, tzinfo=UTC)  # 60 minutes later

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
    # Set datetime to 5 minutes ago and end_datetime to 55 min from now so is_in_progress is True
    meeting.datetime = now - timedelta(minutes=5)
    meeting.end_datetime = now + timedelta(minutes=55)  # total 60 min, still in progress
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
    meeting.end_datetime = now + timedelta(minutes=55)  # total 60 min, still in progress
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


# ---------------------------------------------------------------------------
# In-progress label in views
# ---------------------------------------------------------------------------


def make_in_progress_meeting(owner: User):
    """Create a meeting that is currently in progress (started 5 min ago, ends 120 min from start)."""
    now = dt.datetime.now(dt.UTC)
    meeting = create_meetup(id=2, owner=owner)
    meeting.datetime = now - timedelta(minutes=5)
    meeting.end_datetime = now + timedelta(minutes=115)  # 120 min total from start, still in progress
    return meeting


def make_not_in_progress_meeting(owner: User):
    """Create a meeting that has not started yet (datetime in the future)."""
    now = dt.datetime.now(dt.UTC)
    meeting = create_meetup(id=3, owner=owner)
    meeting.datetime = now + timedelta(hours=2)
    meeting.end_datetime = now + timedelta(hours=3)  # 60 min duration, both in future
    return meeting


@pytest.mark.parametrize(
    "get_view",
    [
        lambda m: m.main_view,
        lambda m: m.external_view,
    ],
    ids=["main_view", "external_view"],
)
def test_view_includes_in_progress_label_when_in_progress(user_with_settings: User, get_view):
    meeting = make_in_progress_meeting(user_with_settings)

    assert meeting.is_in_progress  # guard: precondition for the branch under test

    view = get_view(meeting)
    # main_view and external_view always use the owner's settings language
    expected_text = MeetingMessages.MEETING_IN_PROGRESS.get_text(lang=meeting.user_language)
    assert expected_text in view.description.text


def test_inline_view_includes_in_progress_label_when_meeting_has_language(user_with_settings: User):
    """inline_view uses meeting.lang; when the meeting has its own language set, that language is used."""
    meeting = make_in_progress_meeting(user_with_settings)
    # make_in_progress_meeting uses the default language="en" from create_meetup
    assert meeting.language == "en"

    assert meeting.is_in_progress  # guard: precondition for the branch under test

    view = meeting.inline_view()
    # meeting.lang resolves to meeting.language ("en") since it is explicitly set
    expected_text = MeetingMessages.MEETING_IN_PROGRESS.get_text(lang=meeting.lang)
    assert expected_text in view.description.text


def test_inline_view_includes_in_progress_label_when_meeting_has_no_language(user_with_settings: User):
    """inline_view uses meeting.lang; when language=None, it falls through to user_language."""
    now = dt.datetime.now(dt.UTC)
    meeting = create_meetup(id=2, owner=user_with_settings, language=None)
    meeting.datetime = now - timedelta(minutes=5)
    meeting.end_datetime = now + timedelta(minutes=115)  # still in progress
    assert meeting.language is None

    assert meeting.is_in_progress  # guard: precondition for the branch under test

    view = meeting.inline_view()
    # meeting.lang falls through to meeting.user_language when language is None
    expected_text = MeetingMessages.MEETING_IN_PROGRESS.get_text(lang=meeting.lang)
    assert expected_text in view.description.text


@pytest.mark.parametrize(
    "get_view",
    [
        lambda m: m.main_view,
        lambda m: m.external_view,
    ],
    ids=["main_view", "external_view"],
)
def test_view_excludes_in_progress_label_when_not_in_progress(user_with_settings: User, get_view):
    meeting = make_not_in_progress_meeting(user_with_settings)

    assert not meeting.is_in_progress  # guard: precondition

    view = get_view(meeting)
    # main_view and external_view always use the owner's settings language
    expected_text = MeetingMessages.MEETING_IN_PROGRESS.get_text(lang=meeting.user_language)
    assert expected_text not in view.description.text


def test_inline_view_excludes_in_progress_label_when_not_in_progress(user_with_settings: User):
    meeting = make_not_in_progress_meeting(user_with_settings)

    assert not meeting.is_in_progress  # guard: precondition

    view = meeting.inline_view()
    # inline_view uses meeting.lang (meeting.language or meeting.user_language)
    expected_text = MeetingMessages.MEETING_IN_PROGRESS.get_text(lang=meeting.lang)
    assert expected_text not in view.description.text


# ---------------------------------------------------------------------------
# is_in_progress: naive datetime compatibility (DB round-trip simulation)
# ---------------------------------------------------------------------------


def test_is_in_progress_with_naive_datetimes(user_with_settings: User):
    """is_in_progress must return True when datetimes are naive (no tzinfo), as returned by the DB.

    In production the DateTime column has no timezone, so SQLAlchemy returns naive datetimes.
    Tests normally pass because fixtures set tzinfo=UTC in memory and never round-trip through
    the DB. This test simulates the actual DB behaviour.
    """
    meeting = create_meetup(id=1, owner=user_with_settings)
    # Naive datetimes: started in the past, ends far in the future
    meeting.datetime = dt.datetime(2024, 1, 1, 10, 0)  # naive, no tzinfo — simulates DB return
    meeting.end_datetime = dt.datetime(2099, 12, 31, 23, 59)  # naive, far future — meeting still running

    # Before the fix, comparing naive end_datetime with aware now() raised TypeError.
    # After the fix, naive datetimes are normalised to UTC before comparison.
    assert meeting.is_in_progress is True
