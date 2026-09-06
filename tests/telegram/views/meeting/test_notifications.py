import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol
from unittest.mock import patch

import pytest

from mitup_bot.emojis import Emojis
from mitup_bot.keyboards import ButtonConfig, Keyboard
from mitup_bot.models import JoinedUsers, Meetup, MeetupLocation
from mitup_bot.translations import SUPPORTED_LANGUAGES
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import (
    ButtonMessages,
    MeetingCardSectionMessages,
    MeetingDisplayMessages,
    MessageBase,
    NotificationMessages,
)
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.datetime_format import relative_time_content
from mitup_bot.views.mitup_view import MitupView
from tests.helpers import create_joined_link, create_settings, create_user
from tests.telegram.views.meeting.helpers import BAR, owned_meeting, section_header_line, with_images

STARTS_AT = dt.datetime(2026, 9, 1, 18, 0, tzinfo=dt.UTC)
ENDS_AT = dt.datetime(2026, 9, 1, 21, 0, tzinfo=dt.UTC)
# The clock every card that does not care about the countdown is rendered against, 25 minutes
# before the meeting starts.
NOW = STARTS_AT - dt.timedelta(minutes=25)


class NotificationView(Protocol):
    def __call__(self, link: JoinedUsers, *, now: dt.datetime) -> MitupView: ...


# The two cards a meeting's start produces, each with the heading it opens on. Everything under the
# heading is the same card, so most of this module runs over both.
NOTIFICATION_CARDS: list[tuple[NotificationView, MessageBase]] = [
    (meeting_views.starting_soon_view, NotificationMessages.STARTING_SOON_HEADING),
    (meeting_views.started_view, NotificationMessages.STARTED_HEADING),
]
NOTIFICATION_VIEWS = [view for view, _ in NOTIFICATION_CARDS]
CARD_IDS = ["starting_soon", "started"]


def scheduled_meeting(lang: str, *, location: MeetupLocation | None = None, ends: bool = False) -> Meetup:
    meeting = owned_meeting(lang=lang, location=location)
    meeting.created_time = dt.datetime(2026, 8, 1, 9, 0, tzinfo=dt.UTC)
    meeting.datetime = STARTS_AT
    meeting.end_datetime = ENDS_AT if ends else None
    return meeting


def reader_link(meeting: Meetup, reader_lang: str) -> JoinedUsers:
    """The joined link a notification is addressed through, held by someone who is not the host."""
    reader = create_user(
        id=50, tg_user_id=50, first_name="Reader", settings=create_settings(id=50, language=reader_lang)
    )
    return create_joined_link(reader, meeting, id=50)


def other_language(lang: str) -> str:
    """A supported language that is not *lang*, so a card rendering both cannot render one twice."""
    return next(other for other in SUPPORTED_LANGUAGES if other != lang)


def callback_datas(keyboard: Keyboard) -> list[str]:
    return [str(button.callback_data) for row in keyboard for button in row if button.callback_data is not None]


def leave_chip(meeting: Meetup, lang: str) -> ButtonConfig:
    return ButtonConfig(
        text=ButtonMessages.LEAVE.text(lang=lang), callback_data=cb.LEAVE.with_id(meeting.db_id), style="danger"
    )


@contextmanager
def participant_list_unloaded() -> Iterator[None]:
    """Make the roster raise the way an unloaded relationship does under the async engine.

    The starting-soon nomination loads the meeting and its owner but not `meetup.joined_links`, so a
    card reaching for the roster would raise MissingGreenlet in the events runner and nowhere else.
    """

    def raise_unloaded(meeting: Meetup):
        raise AssertionError("The notification card read the participant list, which is not loaded")

    with patch.object(Meetup, "joined_links", property(raise_unloaded)):
        yield


# --- The heading each card opens on ---


@pytest.mark.parametrize("view, heading", NOTIFICATION_CARDS, ids=CARD_IDS)
def test_the_card_opens_on_what_happened_over_the_meeting_it_happened_to(
    view: NotificationView, heading: MessageBase, lang: str
):
    meeting = scheduled_meeting(lang)
    title = heading.rich(lang=lang).html

    assert view(reader_link(meeting, lang), now=NOW).message.html.startswith(f"<h2>{title}</h2>Board game night")


@pytest.mark.parametrize("view, heading", NOTIFICATION_CARDS, ids=CARD_IDS)
def test_the_reader_is_addressed_in_their_own_language_while_the_meeting_keeps_its_own(
    view: NotificationView, heading: MessageBase, lang: str
):
    """A participant reads the notice in the language they picked, and the meeting's schedule and
    place read as its own card writes them, in the language it was created in."""
    meeting = scheduled_meeting(other_language(lang))
    body = view(reader_link(meeting, lang), now=NOW).message

    assert heading.rich(lang=lang).html in body.html
    assert section_header_line(MeetingCardSectionMessages.WHEN, Emojis.CLOCK, meeting) in body.text


# --- When ---


@pytest.mark.parametrize("view", NOTIFICATION_VIEWS, ids=CARD_IDS)
def test_the_schedule_is_titled_and_carries_the_start_as_a_time_chip(view: NotificationView, lang: str):
    meeting = scheduled_meeting(lang)
    body = view(reader_link(meeting, lang), now=NOW).message

    assert section_header_line(MeetingCardSectionMessages.WHEN, Emojis.CLOCK, meeting) in body.text
    assert f'<tg-time unix="{int(STARTS_AT.timestamp())}"' in body.html


@pytest.mark.parametrize("view", NOTIFICATION_VIEWS, ids=CARD_IDS)
def test_the_end_shares_the_start_line_as_the_far_end_of_a_span(view: NotificationView, lang: str):
    meeting = scheduled_meeting(lang, ends=True)

    html = view(reader_link(meeting, lang), now=NOW).message.html

    start_chip = f'<tg-time unix="{int(STARTS_AT.timestamp())}" format="T"'
    start_line = next(line for line in html.split("<br/>") if start_chip in line)
    assert f'<tg-time unix="{int(ENDS_AT.timestamp())}" format="T"' in start_line


@pytest.mark.parametrize("view", NOTIFICATION_VIEWS, ids=CARD_IDS)
def test_a_meeting_with_no_end_time_carries_only_its_start(view: NotificationView, lang: str):
    meeting = scheduled_meeting(lang)
    html = view(reader_link(meeting, lang), now=NOW).message.html

    assert "<tg-time" in html
    assert f'<tg-time unix="{int(ENDS_AT.timestamp())}"' not in html


@pytest.mark.parametrize("view", NOTIFICATION_VIEWS, ids=CARD_IDS)
def test_a_meeting_with_no_start_time_leaves_the_schedule_out(view: NotificationView, lang: str):
    meeting = scheduled_meeting(lang)
    meeting.datetime = None

    text = view(reader_link(meeting, lang), now=NOW).message.text

    assert section_header_line(MeetingCardSectionMessages.WHEN, Emojis.CLOCK, meeting) not in text


def test_a_running_meeting_is_not_told_twice_that_it_started(lang: str):
    """The heading is the card's state line, so the status line the meeting card carries while it
    runs would only say the same thing again."""
    meeting = scheduled_meeting(lang)
    started = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)
    meeting.datetime = started
    assert meeting.is_in_progress

    text = meeting_views.started_view(reader_link(meeting, lang), now=started + dt.timedelta(minutes=5)).message.text

    assert MeetingDisplayMessages.IN_PROGRESS_STATUS.rich(lang=meeting.lang).text not in text


# --- How far the start is ---


def test_the_reminder_counts_down_to_the_start(lang: str):
    meeting = scheduled_meeting(lang)
    when = relative_time_content(STARTS_AT, now=NOW, lang=lang)
    countdown = NotificationMessages.STARTS_IN.rich(lang=lang, when=when)

    html = meeting_views.starting_soon_view(reader_link(meeting, lang), now=NOW).message.html

    assert countdown.html in html


def test_the_started_card_counts_up_from_the_start(lang: str):
    meeting = scheduled_meeting(lang)
    now = STARTS_AT + dt.timedelta(minutes=5)
    when = relative_time_content(STARTS_AT, now=now, lang=lang)
    countdown = NotificationMessages.STARTED_AGO.rich(lang=lang, when=when)

    html = meeting_views.started_view(reader_link(meeting, lang), now=now).message.html

    assert countdown.html in html


@pytest.mark.parametrize("view", NOTIFICATION_VIEWS, ids=CARD_IDS)
def test_the_countdown_sits_between_the_title_and_the_schedule(view: NotificationView, lang: str):
    meeting = scheduled_meeting(lang)
    text = view(reader_link(meeting, lang), now=NOW).message.text

    schedule = section_header_line(MeetingCardSectionMessages.WHEN, Emojis.CLOCK, meeting)
    countdown = relative_time_content(STARTS_AT, now=NOW, lang=lang).text

    assert text.index(meeting.title) < text.index(countdown) < text.index(schedule)


def test_the_countdown_is_written_in_the_language_the_reader_picked(lang: str):
    meeting = scheduled_meeting(other_language(lang))
    reader_wording = relative_time_content(STARTS_AT, now=NOW, lang=lang).text
    meeting_wording = relative_time_content(STARTS_AT, now=NOW, lang=meeting.lang).text
    assert reader_wording != meeting_wording

    text = meeting_views.starting_soon_view(reader_link(meeting, lang), now=NOW).message.text

    assert reader_wording in text
    assert meeting_wording not in text


@pytest.mark.parametrize("seconds", [-30, 0, 30, 59], ids=["about_to", "on_time", "seconds_in", "under_a_minute"])
def test_a_meeting_starting_around_now_is_not_counted_in_minutes(seconds: int, lang: str):
    """A count in minutes rounds half a minute up to a whole one, which would tell a reader the
    meeting began before it did."""
    meeting = scheduled_meeting(lang)
    body = meeting_views.started_view(reader_link(meeting, lang), now=STARTS_AT + dt.timedelta(seconds=seconds)).message

    assert NotificationMessages.STARTED_JUST_NOW.rich(lang=lang).text in body.text


def test_a_meeting_a_full_minute_old_is_counted(lang: str):
    meeting = scheduled_meeting(lang)
    now = STARTS_AT + dt.timedelta(minutes=1)

    body = meeting_views.started_view(reader_link(meeting, lang), now=now).message

    assert NotificationMessages.STARTED_JUST_NOW.rich(lang=lang).text not in body.text
    assert relative_time_content(STARTS_AT, now=now, lang=lang).text in body.text


@pytest.mark.parametrize("view", NOTIFICATION_VIEWS, ids=CARD_IDS)
def test_a_meeting_with_no_start_time_is_counted_neither_way(view: NotificationView, lang: str):
    meeting = scheduled_meeting(lang)
    meeting.datetime = None

    body = view(reader_link(meeting, lang), now=NOW).message

    assert "<tg-time" not in body.html
    assert NotificationMessages.STARTED_JUST_NOW.rich(lang=lang).text not in body.text


# --- Where ---


@pytest.mark.parametrize("view", NOTIFICATION_VIEWS, ids=CARD_IDS)
def test_the_place_is_named_over_the_map_its_pin_draws(view: NotificationView, lang: str):
    """The map is tappable where it sits, which is why the card carries no directions link."""
    meeting = scheduled_meeting(lang, location=BAR)
    body = view(reader_link(meeting, lang), now=NOW).message

    assert section_header_line(MeetingCardSectionMessages.WHERE, Emojis.MAP, meeting) in body.text
    assert 'The usual bar<tg-map lat="48.85" long="2.34"' in body.html


@pytest.mark.parametrize("view", NOTIFICATION_VIEWS, ids=CARD_IDS)
def test_a_place_with_no_pin_is_named_without_a_map(view: NotificationView, lang: str):
    meeting = scheduled_meeting(lang, location=MeetupLocation(name="The usual bar"))
    body = view(reader_link(meeting, lang), now=NOW).message

    assert section_header_line(MeetingCardSectionMessages.WHERE, Emojis.MAP, meeting) in body.text
    assert "<tg-map" not in body.html


@pytest.mark.parametrize("view", NOTIFICATION_VIEWS, ids=CARD_IDS)
def test_a_pin_with_no_name_is_still_the_meetings_place(view: NotificationView, lang: str):
    meeting = scheduled_meeting(lang, location=MeetupLocation(coordinates=(2.34, 48.85)))
    body = view(reader_link(meeting, lang), now=NOW).message

    assert section_header_line(MeetingCardSectionMessages.WHERE, Emojis.MAP, meeting) in body.text
    assert "<tg-map" in body.html


@pytest.mark.parametrize("view", NOTIFICATION_VIEWS, ids=CARD_IDS)
def test_a_meeting_with_neither_a_name_nor_a_pin_leaves_the_place_out(view: NotificationView, lang: str):
    meeting = scheduled_meeting(lang)
    body = view(reader_link(meeting, lang), now=NOW).message

    assert section_header_line(MeetingCardSectionMessages.WHERE, Emojis.MAP, meeting) not in body.text
    assert "<tg-map" not in body.html


# --- The way out, and the way in ---


def test_only_the_reminder_offers_the_way_out_of_the_meeting(lang: str):
    """A reminder still arrives in time to free a place; a meeting that already began is past the
    point where leaving it is the offer to make."""
    meeting = scheduled_meeting(lang)
    link = reader_link(meeting, lang)
    leave = f'data="{cb.LEAVE.with_id(meeting.db_id)}"'

    assert leave in meeting_views.starting_soon_view(link, now=NOW).message.html
    assert leave not in meeting_views.started_view(link, now=NOW).message.html


def test_the_reminder_carries_the_leave_chip_inside_its_question(lang: str):
    """The chip answers the sentence it sits in rather than standing as a row of its own."""
    meeting = scheduled_meeting(lang)
    question = NotificationMessages.CANNOT_MAKE_IT.rich(lang=lang, button_leave=leave_chip(meeting, lang))

    assert question.html in meeting_views.starting_soon_view(reader_link(meeting, lang), now=NOW).message.html


@pytest.mark.parametrize("view", NOTIFICATION_VIEWS, ids=CARD_IDS)
def test_both_cards_close_on_the_meeting_and_the_main_menu(view: NotificationView, lang: str):
    meeting = scheduled_meeting(lang)
    menu = view(reader_link(meeting, lang), now=NOW).menu

    assert callback_datas(menu) == [str(cb.SHOW_MEETING.with_id(meeting.db_id)), str(cb.MAIN_MENU)]


@pytest.mark.parametrize("view", NOTIFICATION_VIEWS, ids=CARD_IDS)
def test_the_way_into_the_meeting_is_the_cards_one_call_to_action(view: NotificationView, lang: str):
    meeting = scheduled_meeting(lang)
    open_meeting, main_menu = (row[0] for row in view(reader_link(meeting, lang), now=NOW).menu)

    assert (open_meeting.text, open_meeting.style) == (ButtonMessages.OPEN_MEETING.text(lang=lang), "primary")
    assert main_menu.text == ButtonMessages.MAIN_MENU.back(lang=lang)


# --- What the card is allowed to read ---


@pytest.mark.parametrize("view", NOTIFICATION_VIEWS, ids=CARD_IDS)
def test_rendering_never_reaches_for_the_participant_list(view: NotificationView, lang: str):
    meeting = scheduled_meeting(lang, location=BAR, ends=True)
    link = reader_link(meeting, lang)

    with participant_list_unloaded():
        assert view(link, now=NOW).message.html


@pytest.mark.parametrize("card", NOTIFICATION_VIEWS, ids=CARD_IDS)
def test_a_notification_draws_no_photos(card: NotificationView):
    """The notification arrives unprompted, so it stays short."""
    meeting = with_images(scheduled_meeting("en"), 3)

    assert "<img" not in card(reader_link(meeting, "en"), now=NOW).message.html
