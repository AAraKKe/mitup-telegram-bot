import datetime as dt

import pytest

from mitup_bot.callback_data import CallbackData, MeetingListSource
from mitup_bot.emojis import Emojis
from mitup_bot.models import Meetup, MeetupLocation
from mitup_bot.translations import SUPPORTED_LANGUAGES
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingDisplayMessages
from mitup_bot.utils.rich_message import RichContent
from mitup_bot.views.datetime_format import RANGE_SEPARATOR
from mitup_bot.views.meeting.list_card import list_heading, meeting_list_section
from mitup_bot.views.meeting.sections import participants_count_line
from mitup_bot.views.meeting.shared_card import when_lines
from tests.telegram.views.meeting.helpers import in_progress_meeting, owned_meeting, with_images

PLACE_NAME = "The usual bar"
PLACE = MeetupLocation(name=PLACE_NAME, coordinates=(2.34, 48.85))

STARTS_AT = dt.datetime(2026, 9, 1, 18, 0, tzinfo=dt.UTC)
ENDS_AT = dt.datetime(2026, 9, 1, 21, 0, tzinfo=dt.UTC)


def open_callback(meeting: Meetup) -> CallbackData:
    return cb.SHOW_MEETING.with_page(meeting.db_id, 2, MeetingListSource.ACTIVE)


def section_lines(meeting: Meetup, lang: str) -> list[str]:
    """The text lines of a section, without the button row that closes it."""
    html = meeting_list_section(meeting, open_callback(meeting), lang).html
    return RichContent.from_markup(html.split("<tg-button-row>")[0]).text.splitlines()


def scheduled_meeting(lang: str, *, ends: bool = False) -> Meetup:
    meeting = owned_meeting(lang=lang)
    meeting.datetime = STARTS_AT
    meeting.end_datetime = ENDS_AT if ends else None
    return meeting


def test_the_title_opens_the_section_and_a_button_row_closes_it(lang: str):
    meeting = owned_meeting(lang=lang)

    section = meeting_list_section(meeting, open_callback(meeting), lang)

    label = ButtonMessages.OPEN.text(lang=lang)
    assert section.html.startswith(f"<b>{meeting.plain_title}</b>")
    assert section.html.endswith(
        f'<tg-button-row><tg-button type="callback_data" data="{open_callback(meeting)}" style="primary">'
        f"{label}</tg-button></tg-button-row>"
    )


def test_a_list_of_owned_meetings_offers_delete_next_to_open(lang: str):
    meeting = owned_meeting(lang=lang)
    delete = cb.DELETE_MEETING.with_id(meeting.db_id)

    section = meeting_list_section(meeting, open_callback(meeting), lang, delete_callback=delete)

    label = ButtonMessages.DELETE.text(lang=lang)
    assert section.html.endswith(
        f'<tg-button type="callback_data" data="{delete}" style="danger">{label}</tg-button></tg-button-row>'
    )


def test_the_title_keeps_the_formatting_its_owner_gave_it(lang: str):
    meeting = owned_meeting(lang=lang)
    meeting.set_title("Board <b>game</b> night")

    assert "Board <b>game</b> night" in meeting_list_section(meeting, open_callback(meeting), lang).html


@pytest.mark.parametrize("title", ["", "   ", "<b>   </b>"], ids=["empty", "spaces", "tags_around_spaces"])
def test_a_meeting_whose_title_has_no_visible_text_is_named_untitled(lang: str, title: str):
    meeting = owned_meeting(lang=lang)
    meeting.set_title(title)

    lines = section_lines(meeting, lang)

    assert lines[0].startswith(MeetingDisplayMessages.UNTITLED.rich(lang=lang).text)


def test_the_schedule_reads_exactly_as_the_card_draws_it(lang: str):
    meeting = scheduled_meeting(lang, ends=True)

    section = meeting_list_section(meeting, open_callback(meeting), lang)

    assert when_lines(meeting, meeting.datetime, with_status=False).html in section.html
    assert RANGE_SEPARATOR in section.text


def test_a_meeting_with_no_schedule_draws_no_time_at_all(lang: str):
    meeting = owned_meeting(lang=lang)

    assert section_lines(meeting, lang)[1:] == [f"{Emojis.JOINED} {participants_count_line(meeting).text}"]


def test_a_running_meeting_reports_no_status(lang: str):
    meeting = in_progress_meeting(locked=False, lang=lang)

    section = meeting_list_section(meeting, open_callback(meeting), lang)

    assert MeetingDisplayMessages.IN_PROGRESS_STATUS.rich(lang=lang).text not in section.text


def test_the_place_is_named_without_the_map_the_card_shows(lang: str):
    meeting = owned_meeting(lang=lang, location=PLACE)

    section = meeting_list_section(meeting, open_callback(meeting), lang)

    assert PLACE_NAME in section.text
    assert "<tg-map" not in section.html


def test_a_meeting_with_no_place_names_none(lang: str):
    meeting = scheduled_meeting(lang)
    meeting.location = MeetupLocation()

    assert section_lines(meeting, lang)[2:] == [f"{Emojis.JOINED} {participants_count_line(meeting).text}"]


def test_the_count_of_who_is_coming_closes_the_section(lang: str):
    meeting = scheduled_meeting(lang, ends=True)
    meeting.location = PLACE

    assert section_lines(meeting, lang)[-1] == f"{Emojis.JOINED} {participants_count_line(meeting).text}"


def test_the_heading_titles_the_screen_with_the_list_it_shows(lang: str):
    heading = list_heading(ButtonMessages.PAST_MEETINGS, lang)

    assert heading.html == f"<h2>{ButtonMessages.PAST_MEETINGS.rich(lang=lang).html}</h2>"


def test_the_chip_reads_in_the_language_it_is_given(lang: str):
    other = next(code for code in SUPPORTED_LANGUAGES if code != lang)
    meeting = owned_meeting(lang=lang)

    section = meeting_list_section(meeting, open_callback(meeting), other)

    assert ButtonMessages.OPEN.text(lang=other) in section.text


def test_a_list_row_draws_no_photos():
    """A list row is a one-glance summary, and banners would crowd the other rows out."""
    meeting = with_images(owned_meeting(), 3)

    assert "<img" not in meeting_list_section(meeting, cb.SHOW_MEETING.with_id(7), "en").html
