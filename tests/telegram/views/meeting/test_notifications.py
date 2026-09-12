import datetime as dt
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from typing import Protocol
from unittest.mock import patch

import pytest

from mitup_bot import lifecycle
from mitup_bot.emojis import Emojis
from mitup_bot.keyboards import ButtonConfig, Keyboard
from mitup_bot.models import JoinedUsers, Meetup, MeetupLocation
from mitup_bot.supporter import SupporterLevel
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
from tests.helpers import create_joined_link, create_meetup, create_settings, create_user
from tests.telegram.views.meeting.helpers import BAR, owned_meeting, section_header_line, with_images

STARTS_AT = dt.datetime(2026, 9, 1, 18, 0, tzinfo=dt.UTC)
ENDS_AT = dt.datetime(2026, 9, 1, 21, 0, tzinfo=dt.UTC)
# The clock every card that does not care about the countdown is rendered against, 25 minutes
# before the meeting starts.
NOW = STARTS_AT - dt.timedelta(minutes=25)


class NotificationView(Protocol):
    def __call__(self, link: JoinedUsers, *, now: dt.datetime) -> MitupView: ...


class DigestView(Protocol):
    def __call__(self, meetups: Sequence[Meetup]) -> MitupView: ...


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


# --- The deletion digests ---


CREATED_AT = dt.datetime(2025, 7, 12, 14, 30, tzinfo=dt.UTC)

DIGESTS: list[tuple[DigestView, MessageBase]] = [
    (meeting_views.deletion_warning_view, NotificationMessages.DELETION_WARNING_HEADING),
    (meeting_views.deletion_notice_view, NotificationMessages.DELETION_NOTICE_HEADING),
]
DIGEST_VIEWS = [view for view, _ in DIGESTS]
DIGEST_IDS = ["warning", "notice"]


def expiring_meetings(
    count: int,
    *,
    lang: str = "en",
    timezone: str = "UTC",
    supporter_level: SupporterLevel = SupporterLevel.NONE,
) -> list[Meetup]:
    """*count* meetings of one owner, created a day apart, the shape a cleanup digest names."""
    owner = create_user(
        id=1,
        tg_user_id=1,
        first_name="Owner",
        settings=create_settings(id=1, language=lang, timezone=timezone),
        supporter_level=supporter_level,
    )
    return [
        create_meetup(
            id=index + 1,
            title=f"Meeting {index}",
            owner=owner,
            created_time=CREATED_AT + dt.timedelta(days=index),
        )
        for index in range(count)
    ]


@pytest.mark.parametrize("digest, heading", DIGESTS, ids=DIGEST_IDS)
def test_the_digest_opens_on_what_is_happening_to_the_meetings(digest: DigestView, heading: MessageBase, lang: str):
    meetings = expiring_meetings(1, lang=lang)

    assert digest(meetings).message.html.startswith(f"<h2>{heading.rich(lang=lang).html}</h2>")


@pytest.mark.parametrize("digest", DIGEST_VIEWS, ids=DIGEST_IDS)
@pytest.mark.parametrize("count", [1, 5], ids=["one", "five"])
def test_a_short_digest_names_every_meeting_it_covers(digest: DigestView, count: int, lang: str):
    meetings = expiring_meetings(count, lang=lang)
    body = digest(meetings).message

    assert body.html.count("<li>") == count
    assert NotificationMessages.DELETION_MORE_MEETINGS.rich(lang=lang, count=1).text not in body.text


@pytest.mark.parametrize("digest", DIGEST_VIEWS, ids=DIGEST_IDS)
def test_a_long_digest_names_the_first_five_and_counts_the_rest(digest: DigestView, lang: str):
    """Eighty-three lines would be unreadable, so the digest names as many as a reader can take in
    and says how many it left out."""
    meetings = expiring_meetings(83, lang=lang)
    body = digest(meetings).message

    assert body.html.count("<li>") == 5
    assert meetings[4].title in body.text
    assert meetings[5].title not in body.text
    assert NotificationMessages.DELETION_MORE_MEETINGS.rich(lang=lang, count=78).html in body.html


@pytest.mark.parametrize("digest", DIGEST_VIEWS, ids=DIGEST_IDS)
def test_each_meeting_is_listed_with_when_it_was_created(digest: DigestView, lang: str):
    meetings = expiring_meetings(1, lang=lang)
    html = digest(meetings).message.html

    assert f"<li><b>{meetings[0].title}</b>" in html
    assert f'<tg-time unix="{int(CREATED_AT.timestamp())}"' in html


@pytest.mark.parametrize("digest", DIGEST_VIEWS, ids=DIGEST_IDS)
def test_a_meeting_with_no_title_is_listed_as_untitled(digest: DigestView, lang: str):
    meetings = expiring_meetings(1, lang=lang)
    meetings[0].title = " "

    assert MeetingDisplayMessages.UNTITLED.rich(lang=lang).text in digest(meetings).message.text


@pytest.mark.parametrize("digest", DIGEST_VIEWS, ids=DIGEST_IDS)
def test_the_creation_moment_is_written_in_the_owners_own_timezone(digest: DigestView):
    """The owner reads the list against the clock they set, not the one the rows are stored in."""
    tokyo = digest(expiring_meetings(1, timezone="Asia/Tokyo")).message.text
    utc = digest(expiring_meetings(1)).message.text

    assert "23:30" in tokyo
    assert "14:30" in utc


@pytest.mark.parametrize("digest", DIGEST_VIEWS, ids=DIGEST_IDS)
def test_the_digest_is_written_in_the_language_its_owner_picked(digest: DigestView, lang: str):
    meetings = expiring_meetings(1, lang=lang)
    other = expiring_meetings(1, lang=other_language(lang))

    assert digest(meetings).message.html != digest(other).message.html


def test_the_warning_promises_the_owners_own_tier_deadline(monkeypatch: pytest.MonkeyPatch):
    """The message promises a deadline, so a Host owner must be told their own tier's lead.

    Both tiers carry the same lead today, which is exactly why this test moves the Host one: with
    the shipped values the assertion would hold whichever policy the view read.
    """
    host_lead = dt.timedelta(days=14)
    monkeypatch.setattr(lifecycle, "PATRON_POLICY", replace(lifecycle.PATRON_POLICY, deletion_warning_lead=host_lead))
    meetings = expiring_meetings(1, supporter_level=SupporterLevel.HOST_3)

    html = meeting_views.deletion_warning_view(meetings).message.html

    assert NotificationMessages.DELETION_WARNING_DEADLINE.rich(lang="en", days_until_deletion=14).html in html


def test_the_warning_points_at_the_list_the_meetings_can_be_reactivated_from(lang: str):
    meetings = expiring_meetings(1, lang=lang)
    past_meetings = ButtonConfig(text=ButtonMessages.PAST_MEETINGS.text(lang=lang), callback_data=cb.PAST_MEETINGS)
    hint = NotificationMessages.DELETION_WARNING_REACTIVATE.rich(lang=lang, button_past_meetings=past_meetings)

    assert hint.html in meeting_views.deletion_warning_view(meetings).message.html


def test_the_warning_closes_on_the_main_menu_however_many_meetings_it_left_unnamed(lang: str):
    """The reactivate hint is the only route into the meetings, so the menu stays navigation only."""
    short = meeting_views.deletion_warning_view(expiring_meetings(5, lang=lang))
    long = meeting_views.deletion_warning_view(expiring_meetings(6, lang=lang))

    assert callback_datas(short.menu) == [str(cb.MAIN_MENU)]
    assert callback_datas(long.menu) == [str(cb.MAIN_MENU)]


def test_the_notice_offers_no_way_into_meetings_that_no_longer_exist(lang: str):
    """The rows are gone by the time the notice is sent, so every button on it would open nothing."""
    notice = meeting_views.deletion_notice_view(expiring_meetings(83, lang=lang))

    assert callback_datas(notice.menu) == [str(cb.MAIN_MENU)]
    assert "<tg-button" not in notice.message.html


@pytest.mark.parametrize("digest", DIGEST_VIEWS, ids=DIGEST_IDS)
def test_every_digest_closes_on_the_way_back_into_the_bot(digest: DigestView, lang: str):
    """A digest arrives unprompted, so it carries the navigation its reader has no other way to."""
    menu = digest(expiring_meetings(1, lang=lang)).menu

    assert menu[-1][0].text == ButtonMessages.MAIN_MENU.back(lang=lang)
    assert menu[-1][0].callback_data == cb.MAIN_MENU
