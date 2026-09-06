import datetime as dt
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from mitup_bot.datetimes import DateFormat, TimeFormat
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup, User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingDisplayMessages
from mitup_bot.utils.rich_message import RichContent
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.meeting_text import plain_datetime
from mitup_bot.views.mitup_view import MitupView
from tests.helpers import create_meetup, create_user


def test_external_view():
    owner = create_user(id=1, first_name="Owner")
    meeting = create_meetup(id=1, owner=owner, invitation=True)

    view = meeting_views.external_view(meeting)

    assert view.menu == [
        [
            ButtonConfig(
                text=ButtonMessages.JOIN.text(lang=meeting.user_language),
                callback_data=cb.JOIN.with_id(meeting.db_id),
                style="success",
            ),
            ButtonConfig(
                text=ButtonMessages.INVITE.text(lang=meeting.user_language),
                callback_data=cb.INVITE.with_id(meeting.db_id),
            ),
            ButtonConfig(
                text=ButtonMessages.LEAVE.text(lang=meeting.user_language),
                callback_data=cb.LEAVE.with_id(meeting.db_id),
                style="danger",
            ),
        ],
        [
            ButtonConfig(
                text=ButtonMessages.REFRESH.text(lang=meeting.user_language),
                callback_data=cb.REFRESH_MEETING.with_id(meeting.db_id),
            ),
        ],
        [
            ButtonConfig(
                text=ButtonMessages.MAIN_MENU.back(lang=meeting.user_language),
                callback_data=cb.MAIN_MENU,
            ),
        ],
    ]


# --- plain_datetime fallback branch ---


def test_plain_datetime_fallback_when_no_datetime_set(user_with_settings: User):
    """plain_datetime must return the DATE_NOT_SET message when meeting.datetime is None."""
    meeting = create_meetup(id=1, owner=user_with_settings)
    meeting.datetime = None

    result = plain_datetime(meeting)

    # Line 251: the else-branch returning the localised "date not set" string
    expected = MeetingDisplayMessages.DATE_NOT_SET.text(lang=meeting.lang)
    assert result == expected  # plain str, not FormattedText


def test_plain_datetime_formatted_when_datetime_set(user_with_settings: User):
    """plain_datetime must write the stored UTC moment out in the meeting's language and timezone.

    The owner's timezone is Madrid's, an hour ahead of the stored 12:30, and the meeting was
    created in the year it takes place in, which keeps the year out of the text.
    """
    meeting = create_meetup(
        id=1,
        owner=user_with_settings,
        language="en",
        created_time=datetime(2024, 1, 2, 9, 0, tzinfo=UTC),
    )
    meeting.datetime = datetime(2024, 1, 12, 12, 30, tzinfo=UTC)

    result = plain_datetime(meeting)

    assert result == "Fri, Jan 12, 13:30"


# --- external_view: join/leave row hidden when locked and in-progress ---


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

    view = meeting_views.external_view(meeting)

    join_cb = cb.JOIN.with_id(meeting.db_id)
    join_buttons = [btn for row in view.menu for btn in row if btn.callback_data == join_cb]
    # Lines 461→463: the join/leave row is skipped when locked and in progress
    assert len(join_buttons) == 0


def test_build_inline_keyboard_hides_join_leave_row_when_locked_and_in_progress(user_with_settings: User):
    """build_inline_keyboard must omit the join/leave row when is_locked_and_in_progress=True."""
    meeting = create_meetup(id=1, owner=user_with_settings)

    keyboard = meeting_views.build_inline_keyboard(meeting, is_locked_and_in_progress=True)

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


# Each card closes its schedule with the status line in its own language: the owner's card in the
# owner's, and a card nobody reading it owns in the meeting's own.
IN_PROGRESS_LABELS = [
    (
        meeting_views.owner_view,
        lambda meeting: MeetingDisplayMessages.IN_PROGRESS_STATUS.rich(lang=meeting.user_language),
    ),
    (
        meeting_views.external_view,
        lambda meeting: MeetingDisplayMessages.IN_PROGRESS_STATUS.rich(lang=meeting.lang),
    ),
]


@pytest.mark.parametrize("get_view, get_label", IN_PROGRESS_LABELS, ids=["owner_view", "external_view"])
def test_view_includes_in_progress_label_when_in_progress(
    user_with_settings: User,
    get_view: Callable[[Meetup], MitupView],
    get_label: Callable[[Meetup], RichContent],
):
    meeting = make_in_progress_meeting(user_with_settings)

    assert meeting.is_in_progress  # guard: precondition for the branch under test

    view = get_view(meeting)
    assert get_label(meeting).text in view.message.text


def test_inline_view_includes_in_progress_label_when_meeting_has_language(user_with_settings: User):
    """inline_view uses meeting.lang; when the meeting has its own language set, that language is used."""
    meeting = make_in_progress_meeting(user_with_settings)
    # make_in_progress_meeting uses the default language="en" from create_meetup
    assert meeting.language == "en"

    assert meeting.is_in_progress  # guard: precondition for the branch under test

    view = meeting_views.inline_view(meeting)
    # meeting.lang resolves to meeting.language ("en") since it is explicitly set
    expected_text = MeetingDisplayMessages.IN_PROGRESS_STATUS.rich(lang=meeting.lang).text
    assert expected_text in view.message.text


def test_inline_view_includes_in_progress_label_when_meeting_has_no_language(user_with_settings: User):
    """inline_view uses meeting.lang; when language=None, it falls through to user_language."""
    now = dt.datetime.now(dt.UTC)
    meeting = create_meetup(id=2, owner=user_with_settings, language=None)
    meeting.datetime = now - timedelta(minutes=5)
    meeting.end_datetime = now + timedelta(minutes=115)  # still in progress
    assert meeting.language is None

    assert meeting.is_in_progress  # guard: precondition for the branch under test

    view = meeting_views.inline_view(meeting)
    # meeting.lang falls through to meeting.user_language when language is None
    expected_text = MeetingDisplayMessages.IN_PROGRESS_STATUS.rich(lang=meeting.lang).text
    assert expected_text in view.message.text


@pytest.mark.parametrize("get_view, get_label", IN_PROGRESS_LABELS, ids=["owner_view", "external_view"])
def test_view_excludes_in_progress_label_when_not_in_progress(
    user_with_settings: User,
    get_view: Callable[[Meetup], MitupView],
    get_label: Callable[[Meetup], RichContent],
):
    meeting = make_not_in_progress_meeting(user_with_settings)

    assert not meeting.is_in_progress  # guard: precondition

    view = get_view(meeting)
    assert get_label(meeting).text not in view.message.text


def test_inline_view_excludes_in_progress_label_when_not_in_progress(user_with_settings: User):
    meeting = make_not_in_progress_meeting(user_with_settings)

    assert not meeting.is_in_progress  # guard: precondition

    view = meeting_views.inline_view(meeting)
    # inline_view uses meeting.lang (meeting.language or meeting.user_language)
    expected_text = MeetingDisplayMessages.IN_PROGRESS_STATUS.rich(lang=meeting.lang).text
    assert expected_text not in view.message.text


# ---------------------------------------------------------------------------
# is_in_progress: naive datetime compatibility (DB round-trip simulation)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# enforce_datetime_ordering — returns False when start < end (line 654)
# ---------------------------------------------------------------------------


def test_enforce_datetime_ordering_returns_false_when_start_before_end(user_with_settings: User):
    """enforce_datetime_ordering returns False (no clearing) when start < end."""
    meeting = create_meetup(id=1, owner=user_with_settings)
    meeting.datetime = datetime(2024, 6, 15, 10, 0, tzinfo=UTC)
    meeting.end_datetime = datetime(2024, 6, 15, 12, 0, tzinfo=UTC)  # 2 hours later

    result = meeting.enforce_datetime_ordering()

    assert result is False
    # end_datetime is preserved
    assert meeting.end_datetime == datetime(2024, 6, 15, 12, 0, tzinfo=UTC)


def test_enforce_datetime_ordering_returns_false_when_end_is_none(user_with_settings: User):
    """enforce_datetime_ordering returns False when end_datetime is None (nothing to clear)."""
    meeting = create_meetup(id=1, owner=user_with_settings)
    meeting.datetime = datetime(2024, 6, 15, 10, 0, tzinfo=UTC)
    meeting.end_datetime = None

    result = meeting.enforce_datetime_ordering()

    assert result is False


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


# ---------------------------------------------------------------------------
# is_in_progress: window semantics (open-ended vs bounded vs no start)
# ---------------------------------------------------------------------------


def test_is_in_progress_false_without_start_time(user_with_settings: User):
    """No start time at all → never in progress, even with lock intent."""
    meeting = create_meetup(id=1, owner=user_with_settings)
    meeting.datetime = None
    meeting.end_datetime = None

    assert meeting.is_in_progress is False


def test_is_in_progress_open_ended_false_before_start(user_with_settings: User):
    """Start time set, no end time → not in progress while now < start."""
    meeting = create_meetup(id=1, owner=user_with_settings)
    meeting.datetime = dt.datetime.now(dt.UTC) + timedelta(minutes=30)  # starts in the future
    meeting.end_datetime = None

    assert meeting.is_in_progress is False


def test_is_in_progress_open_ended_true_after_start(user_with_settings: User):
    """Start time in the past, no end time → in progress indefinitely (open-ended window)."""
    meeting = create_meetup(id=1, owner=user_with_settings)
    meeting.datetime = dt.datetime.now(dt.UTC) - timedelta(minutes=5)  # started 5 min ago
    meeting.end_datetime = None

    assert meeting.is_in_progress is True


def test_is_in_progress_open_ended_true_long_after_start(user_with_settings: User):
    """Open-ended window has no upper bound: still in progress long after start."""
    meeting = create_meetup(id=1, owner=user_with_settings)
    meeting.datetime = dt.datetime.now(dt.UTC) - timedelta(days=365)  # started a year ago
    meeting.end_datetime = None

    assert meeting.is_in_progress is True


def test_is_in_progress_false_when_meeting_inactive(user_with_settings: User):
    """A deactivated meeting is never in progress — deactivation ends the open-ended window."""
    meeting = create_meetup(id=1, owner=user_with_settings)
    meeting.datetime = dt.datetime.now(dt.UTC) - timedelta(days=365)
    meeting.end_datetime = None
    meeting.active = False

    assert meeting.is_in_progress is False


def test_is_in_progress_bounded_true_within_window(user_with_settings: User):
    """Both times set → in progress within [start, end)."""
    now = dt.datetime.now(dt.UTC)
    meeting = create_meetup(id=1, owner=user_with_settings)
    meeting.datetime = now - timedelta(minutes=5)
    meeting.end_datetime = now + timedelta(minutes=55)

    assert meeting.is_in_progress is True


def test_is_in_progress_bounded_false_after_end(user_with_settings: User):
    """Both times set → not in progress once now >= end (bounded window closes)."""
    now = dt.datetime.now(dt.UTC)
    meeting = create_meetup(id=1, owner=user_with_settings)
    meeting.datetime = now - timedelta(minutes=120)
    meeting.end_datetime = now - timedelta(minutes=5)  # ended 5 min ago

    assert meeting.is_in_progress is False


def test_is_in_progress_bounded_false_before_start(user_with_settings: User):
    """Both times set → not in progress before the start."""
    now = dt.datetime.now(dt.UTC)
    meeting = create_meetup(id=1, owner=user_with_settings)
    meeting.datetime = now + timedelta(minutes=30)
    meeting.end_datetime = now + timedelta(minutes=90)

    assert meeting.is_in_progress is False


# ---------------------------------------------------------------------------
# attendance_is_open: the lock only bites while the meeting runs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lock_on_start,running,expected",
    [
        (True, True, False),
        (True, False, True),
        (False, True, True),
        (False, False, True),
    ],
    ids=["locked_running", "locked_not_running", "unlocked_running", "unlocked_not_running"],
)
def test_attendance_is_open(user_with_settings: User, lock_on_start: bool, running: bool, expected: bool):
    now = dt.datetime.now(dt.UTC)
    meeting = create_meetup(id=1, owner=user_with_settings)
    meeting.lock_on_start = lock_on_start
    start = now - timedelta(minutes=5) if running else now + timedelta(minutes=30)
    meeting.datetime = start
    meeting.end_datetime = start + timedelta(minutes=60)

    assert meeting.is_in_progress is running  # guard: the clock half of the predicate
    assert meeting.attendance_is_open is expected


def test_a_new_meeting_names_the_time_format_it_writes_its_moments_with():
    meeting = create_meetup(id=1)

    assert meeting.time_format == TimeFormat(show_timezone=False, clock_24h=True, date_format=DateFormat.DEFAULT)


def test_the_time_format_reads_the_three_stored_settings():
    meeting = create_meetup(id=1)
    meeting.show_timezone = False
    meeting.clock_24h = False
    meeting.date_format = DateFormat.LONG

    assert meeting.time_format == TimeFormat(show_timezone=False, clock_24h=False, date_format=DateFormat.LONG)
