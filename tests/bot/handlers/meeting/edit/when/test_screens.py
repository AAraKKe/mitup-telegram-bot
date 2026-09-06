"""The When screens draw a meeting datetime as text a client can read on its own.

A `date_time` entity is only acted on by clients that understand it; every other client draws the
entity's text as it stands. These tests pin that text to the moment itself, in the language the
screen is rendered in and the timezone the meeting is scheduled in.
"""

import datetime as dt

from mitup_bot.handlers.meeting.edit.when import screens
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages
from mitup_bot.views.datetime_format import localized_datetime
from tests.helpers import create_meetup, create_settings, create_user

# 22:45 in Madrid, the evening before in UTC.
STARTS_AT = dt.datetime(2027, 3, 17, 21, 45, tzinfo=dt.UTC)
ENDS_AT = dt.datetime(2027, 3, 18, 0, 30, tzinfo=dt.UTC)

# Pinned to the year the meeting falls in, so the expectations below stay on the short form: a
# screen spells the year out only when the meeting outlives the year it was created in.
CREATED_AT = dt.datetime(2027, 1, 5, 9, 0, tzinfo=dt.UTC)

STARTS_AT_IN_SPANISH = "mié, 17 mar, 22:45"
# Spanish drops the leading zero on the hour; the expectations follow CLDR rather than the stored value.
ENDS_AT_IN_SPANISH = "jue, 18 mar, 1:30"


def meeting_in_madrid(*, ends_at: dt.datetime | None = None) -> Meetup:
    """A meeting starting at STARTS_AT, owned by someone whose timezone is Madrid's."""
    meeting = create_meetup(1, datetime=STARTS_AT, created_time=CREATED_AT)
    meeting.end_datetime = ends_at
    create_user(
        id=1,
        tg_user_id=123,
        owned_meetings=[meeting],
        settings=create_settings(timezone="Europe/Madrid"),
    )
    return meeting


def test_the_end_card_writes_the_start_under_the_meetings_own_time_format():
    """The moment the end is measured from reads exactly as it does on the card, so a meeting on a
    12-hour clock naming its timezone sees the same here."""
    meeting = meeting_in_madrid(ends_at=ENDS_AT)
    meeting.clock_24h = False
    meeting.show_timezone = True

    view = screens.end_datetime_card(meeting, "es_ES", month=ENDS_AT.date())

    expected = localized_datetime(
        STARTS_AT,
        lang="es_ES",
        tz=meeting.timezone,
        created=meeting.created_time,
        time_format=meeting.time_format,
    )
    assert expected in view.message.text
    assert STARTS_AT_IN_SPANISH not in view.message.text


def test_end_datetime_card_names_the_start_of_the_span():
    meeting = meeting_in_madrid(ends_at=ENDS_AT)
    view = screens.end_datetime_card(meeting, "es_ES", month=ENDS_AT.date())

    assert STARTS_AT_IN_SPANISH in view.message.text


def test_end_datetime_card_shows_the_end_time_row_and_remove():
    meeting = meeting_in_madrid(ends_at=ENDS_AT)
    view = screens.end_datetime_card(meeting, "en", month=ENDS_AT.date())
    html = view.message.html

    # The end is 00:30 UTC == 1:30 Madrid; its day carries the success accent.
    assert "01:30" in html
    assert str(cb.OPEN_END_TIME_PROMPT.with_id(1)) in html
    assert str(cb.PICK_END_DATE.with_id(1).with_date(dt.date(2027, 3, 18))) in html
    assert view.menu[0] == [
        ButtonConfig(
            text=ButtonMessages.REMOVE_END_DATETIME.text(lang="en"),
            callback_data=cb.DELETE_MEETING_END_TIME.with_id(1),
            style="danger",
        )
    ]


def test_end_datetime_card_without_an_end_disables_the_time_row():
    meeting = meeting_in_madrid()
    view = screens.end_datetime_card(meeting, "en", month=STARTS_AT.date())
    html = view.message.html

    assert '<tg-button type="disabled">' in html
    assert ButtonMessages.SELECT_DATE_FIRST.text(lang="en") in html
    assert len(view.menu) == 1
    assert view.menu[0][0].callback_data == cb.CANCEL_END_EDIT.with_id(1)


# --- The start datetime card ---


def test_start_datetime_card_with_a_start_shows_calendar_time_row_and_remove():
    meeting = meeting_in_madrid()
    view = screens.start_datetime_card(meeting, "en", month=STARTS_AT.date())
    html = view.message.html

    # The calendar table with the meeting's day highlighted (22:45 Madrid on the 17th).
    assert "<table compact>" in html
    assert 'style="success"' in html
    assert str(cb.PICK_START_DATE.with_id(1).with_date(dt.date(2027, 3, 17))) in html
    # The month arrows, and below them the year arrows.
    assert str(cb.NAVIGATE_START_CALENDAR.with_id(1).with_date(dt.date(2027, 4, 1))) in html
    assert str(cb.NAVIGATE_START_CALENDAR.with_id(1).with_date(dt.date(2028, 3, 1))) in html
    # The time row shows the owner-local wall clock with its edit chip.
    assert "22:45" in html
    assert str(cb.OPEN_START_TIME_PROMPT.with_id(1)) in html
    # The remove control lives in the menu, wired to the clear-times confirmation.
    assert view.menu[0] == [
        ButtonConfig(
            text=ButtonMessages.REMOVE_DATETIME.text(lang="en"),
            callback_data=cb.DELETE_MEETING_TIMES.with_id(1),
            style="danger",
        )
    ]


def test_start_datetime_card_without_a_start_disables_the_time_row():
    meeting = meeting_in_madrid()
    meeting.datetime = None
    view = screens.start_datetime_card(meeting, "en", month=dt.date(2027, 3, 1))
    html = view.message.html

    # The time row is an inert chip until a day exists, and nothing offers to remove a schedule
    # that is not there: the menu holds only the back row.
    assert '<tg-button type="disabled">' in html
    assert ButtonMessages.SELECT_DATE_FIRST.text(lang="en") in html
    assert str(cb.OPEN_START_TIME_PROMPT.with_id(1)) not in html
    assert len(view.menu) == 1
    assert view.menu[0][0].callback_data == cb.CANCEL_START_EDIT.with_id(1)


def test_datetime_cards_name_the_meeting_card_they_return_to():
    """Both halves share one card builder, so the back label is asserted once: cancelling redraws
    the meeting card, and the label names it."""
    meeting = meeting_in_madrid(ends_at=ENDS_AT)

    start = screens.start_datetime_card(meeting, "en", month=STARTS_AT.date())
    end = screens.end_datetime_card(meeting, "en", month=ENDS_AT.date())

    assert start.menu[-1] == [
        ButtonConfig(text=ButtonMessages.MEETING.back(lang="en"), callback_data=cb.CANCEL_START_EDIT.with_id(1))
    ]
    assert end.menu[-1] == [
        ButtonConfig(text=ButtonMessages.MEETING.back(lang="en"), callback_data=cb.CANCEL_END_EDIT.with_id(1))
    ]
