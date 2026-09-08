import datetime as dt
from collections.abc import Callable

import pytest

from mitup_bot import bot_links
from mitup_bot.emojis import Emojis
from mitup_bot.images import ImageLayout
from mitup_bot.keyboards import ButtonConfig, Keyboard
from mitup_bot.models import Meetup, MeetupLocation
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import (
    ButtonMessages,
    MeetingAttachMessages,
    MeetingCardSectionMessages,
    MeetingDisplayMessages,
    MeetingJoinMessages,
)
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.datetime_format import RANGE_SEPARATOR
from mitup_bot.views.meeting import shared_card
from mitup_bot.views.meeting.fitting import MEETING_CARD_BUDGET
from mitup_bot.views.meeting.sections import participants_count_line
from mitup_bot.views.mitup_view import MitupView
from tests.helpers import create_meetup, create_settings, create_user
from tests.telegram.views.meeting.helpers import (
    BAR,
    OWNER_NAME,
    in_progress_meeting,
    owned_meeting,
    participants_header_line,
    populated_meeting,
    section_header_line,
    url_buttons,
    with_images,
)

CHAT_INSTANCE = "someinstance"

# Enough guests that their names alone carry the card past its budget: each is about ten
# characters of text, and the budget is the wire ceiling less the room the closing lines and the
# keyboard take.
OVER_BUDGET_GUESTS = 4000


def share_buttons(keyboard: Keyboard) -> list[ButtonConfig]:
    return [button for row in keyboard for button in row if button.switch_inline_query is not None]


def callback_datas(keyboard: Keyboard) -> list[str]:
    return [str(button.callback_data) for row in keyboard for button in row if button.callback_data is not None]


def body_html(meeting: Meetup) -> str:
    return shared_card.shared_body(meeting).html


def closing_footer_html(view: MitupView) -> str:
    """The muted block the card ends on, which is the last of the two it carries: the byline
    closing the heading is the other."""
    return view.message.html.rpartition("<footer>")[2].partition("</footer>")[0]


def waiting_list_line(meeting: Meetup, waiting: int) -> str:
    return f"{Emojis.WAITING} {ButtonMessages.WAITING_LIST.rich(lang=meeting.lang).text} · {waiting}"


def full_meeting(*, waiting_list: bool = False, lang: str = "en") -> Meetup:
    meeting = owned_meeting(guests=2, lang=lang)
    meeting.max_members = 2
    meeting.waiting_list = waiting_list
    return meeting


# --- The heading a card opens on ---


def test_the_title_is_the_cards_heading():
    assert body_html(owned_meeting()).startswith("<h2>Board game night</h2>")


def test_the_title_carries_no_chip_because_nobody_reading_this_card_owns_it():
    meeting = owned_meeting()

    assert str(cb.EDIT_MEETING_TITLE.with_id(meeting.db_id)) not in body_html(meeting)


def test_the_byline_is_the_quiet_half_of_the_heading(lang: str):
    """The muted block sets it below the title, and the rule and section that follow absorb the
    margin it draws."""
    meeting = owned_meeting(lang=lang)
    created_by = MeetingDisplayMessages.CREATED_BY.rich(lang=lang, owner=OWNER_NAME).html

    assert f"</h2><footer>{created_by}</footer>" in body_html(meeting)


def test_a_public_meeting_says_so_with_an_inert_chip_in_its_byline(lang: str):
    """The chip reports what the meeting is rather than offering to change it, so it answers no tap
    and keeps a status colour instead of a control's."""
    meeting = owned_meeting(lang=lang, public=True)
    label = ButtonMessages.PUBLIC.text(lang=lang)

    assert f'<tg-button type="disabled" style="success">{label}</tg-button>' in body_html(meeting)


def test_a_private_meeting_carries_no_status_chip(lang: str):
    assert "<tg-button" not in body_html(owned_meeting(lang=lang))


def test_the_description_is_a_titled_section_keeping_its_own_formatting(lang: str):
    """It reads in the card's section vocabulary: a glyph and a bold title over the block below."""
    meeting = owned_meeting(lang=lang)
    meeting.set_description("<b>Bring</b> snacks")
    title = MeetingCardSectionMessages.DESCRIPTION.rich(lang=lang).html

    assert f"{Emojis.DESCRIPTION} <b>{title}</b><br/><b>Bring</b> snacks" in body_html(meeting)


def test_a_meeting_with_no_description_leaves_the_section_out(lang: str):
    """A reader of somebody else's card cannot fill an empty field, so being told it is empty only
    costs them a section."""
    meeting = owned_meeting(lang=lang)
    meeting.description = None

    text = shared_card.shared_body(meeting).text

    assert section_header_line(MeetingCardSectionMessages.DESCRIPTION, Emojis.DESCRIPTION, meeting) not in text
    assert MeetingDisplayMessages.DESCRIPTION_EMPTY.rich(lang=meeting.lang).text not in text


# --- Sections, and the ones a bare meeting leaves out ---


def test_a_populated_meeting_titles_every_section(lang: str):
    meeting = populated_meeting(lang=lang)
    text = shared_card.shared_body(meeting).text

    for title, glyph in (
        (MeetingCardSectionMessages.DESCRIPTION, Emojis.DESCRIPTION),
        (MeetingCardSectionMessages.WHEN, Emojis.CLOCK),
        (MeetingCardSectionMessages.WHERE, Emojis.MAP),
        (MeetingCardSectionMessages.PARTICIPANTS, Emojis.JOINED),
    ):
        assert section_header_line(title, glyph, meeting) in text


@pytest.mark.parametrize(
    "title, glyph",
    [(MeetingCardSectionMessages.WHEN, Emojis.CLOCK), (MeetingCardSectionMessages.WHERE, Emojis.MAP)],
    ids=["when", "where"],
)
def test_a_bare_meeting_leaves_the_sections_holding_nothing_out(
    title: MeetingCardSectionMessages, glyph: Emojis, lang: str
):
    meeting = owned_meeting(lang=lang)

    assert section_header_line(title, glyph, meeting) not in shared_card.shared_body(meeting).text


def test_the_participants_section_is_titled_even_on_a_meeting_nobody_has_joined(lang: str):
    """A meeting nobody has joined yet is a fact about it rather than a gap in it."""
    meeting = owned_meeting(lang=lang)
    header = section_header_line(MeetingCardSectionMessages.PARTICIPANTS, Emojis.JOINED, meeting)

    assert header in shared_card.shared_body(meeting).text


def test_a_card_with_nothing_to_say_draws_one_rule_and_not_two():
    """The rule separates sections, so a card that has none to separate must not stack two of them
    against each other."""
    meeting = owned_meeting()
    meeting.description = None

    assert body_html(meeting).count("<hr/>") == 1


def test_a_card_rules_off_only_the_sections_it_renders():
    """A bare meeting still describes itself, so its card runs heading, description, participants."""
    assert body_html(owned_meeting()).count("<hr/>") == 2


def test_a_populated_card_rules_off_every_block():
    assert body_html(populated_meeting()).count("<hr/>") == 3


# --- When ---

# 22:45 in Madrid, the evening before in UTC, as a meeting datetime column holds it.
STARTS_AT = dt.datetime(2027, 3, 17, 21, 45, tzinfo=dt.UTC)
ENDS_AT = dt.datetime(2027, 3, 18, 0, 30, tzinfo=dt.UTC)
# Pinned to the year the meeting falls in, which is what keeps the year out of the expectations
# below: a card spells the year out only when the meeting outlives the year it was created in.
CREATED_AT = dt.datetime(2027, 1, 5, 9, 0, tzinfo=dt.UTC)
STARTS_AT_IN_SPANISH = "mié, 17 mar, 22:45"
ENDS_AT_IN_SPANISH = "jue, 18 mar, 1:30"


def meeting_in_madrid(*, ends_at: dt.datetime | None = None) -> Meetup:
    """A meeting starting at STARTS_AT, owned by someone whose timezone is Madrid's."""
    meeting = create_meetup(1, datetime=STARTS_AT, language="es_ES", created_time=CREATED_AT)
    meeting.end_datetime = ends_at
    create_user(
        id=1,
        tg_user_id=123,
        owned_meetings=[meeting],
        settings=create_settings(timezone="Europe/Madrid", language="es_ES"),
    )
    return meeting


def test_the_start_is_written_out_in_the_meetings_own_language_and_timezone():
    assert STARTS_AT_IN_SPANISH in shared_card.shared_body(meeting_in_madrid()).text


def test_the_start_is_a_time_chip_whose_visible_text_is_the_whole_moment():
    """Clients that resolve the chip swap in the reader's own time on a tap; the rest draw the text
    as it stands, so it has to read as a datetime on its own."""
    html = body_html(meeting_in_madrid())

    unix = int(STARTS_AT.timestamp())
    date_chip = f'<tg-time unix="{unix}" format="D">17 mar</tg-time>'
    time_chip = f'<tg-time unix="{unix}" format="T">22:45</tg-time>'
    assert f"mié, {date_chip}, {time_chip}" in html


def test_the_start_spells_the_year_out_for_a_meeting_outliving_its_creation_year():
    meeting = meeting_in_madrid()
    meeting.created_time = dt.datetime(2026, 11, 5, 9, 0, tzinfo=dt.UTC)

    assert "mié, 17 mar 2027, 22:45" in shared_card.shared_body(meeting).text


def test_an_end_on_another_day_folds_into_a_span_of_two_full_moments():
    text = shared_card.shared_body(meeting_in_madrid(ends_at=ENDS_AT)).text

    assert f"{STARTS_AT_IN_SPANISH}{RANGE_SEPARATOR}{ENDS_AT_IN_SPANISH}" in text


def test_an_end_on_the_same_day_names_the_date_once_and_both_times():
    text = shared_card.shared_body(meeting_in_madrid(ends_at=STARTS_AT + dt.timedelta(minutes=30))).text

    assert f"{STARTS_AT_IN_SPANISH}{RANGE_SEPARATOR}23:15" in text


def test_a_meeting_with_no_end_carries_only_the_start():
    meeting = meeting_in_madrid()

    assert RANGE_SEPARATOR not in shared_card.when_lines(meeting, meeting.datetime).text


def test_a_meeting_under_way_says_so_on_the_line_below_its_schedule(lang: str):
    """The status is what the times above currently amount to, so it closes their block rather than
    standing over the card's title."""
    meeting = in_progress_meeting(locked=False, lang=lang)
    status = MeetingDisplayMessages.IN_PROGRESS_STATUS.rich(lang=meeting.lang).text

    lines = shared_card.shared_body(meeting).text.splitlines()

    assert status in lines
    assert RANGE_SEPARATOR in lines[lines.index(status) - 1]


def test_the_status_line_carries_the_emphasis_that_sets_it_apart_from_a_time_row(lang: str):
    meeting = in_progress_meeting(locked=False, lang=lang)

    assert MeetingDisplayMessages.IN_PROGRESS_STATUS.rich(lang=meeting.lang).html in body_html(meeting)


def test_a_meeting_still_to_come_carries_no_status_line(lang: str):
    meeting = owned_meeting(lang=lang)
    meeting.datetime = dt.datetime.now(dt.UTC) + dt.timedelta(days=1)
    assert not meeting.is_in_progress  # guard: precondition for the branch under test

    status = MeetingDisplayMessages.IN_PROGRESS_STATUS.rich(lang=meeting.lang).text

    assert status not in shared_card.shared_body(meeting).text


def test_a_finished_meeting_says_so_on_the_line_below_its_schedule(lang: str):
    """The state a finished card reports reads where every other state does, so the card keeps the
    one shape a reader knows it by."""
    meeting = populated_meeting(lang=lang)
    status = MeetingDisplayMessages.FINISHED_STATUS.rich(lang=meeting.lang).text

    lines = shared_card.shared_body(meeting, finished=True).text.splitlines()

    assert status in lines
    assert RANGE_SEPARATOR in lines[lines.index(status) - 1]


def test_a_finished_card_opens_on_the_meeting_title(lang: str):
    """No banner stands over the title: the status line under the schedule is the whole of what the
    card says about the state."""
    meeting = populated_meeting(lang=lang)

    html = shared_card.shared_body(meeting, finished=True).html

    assert html.startswith(f"<h2>{meeting.plain_title}</h2>")


def test_a_finished_meeting_the_clock_still_calls_live_says_it_has_finished(lang: str):
    """A meeting can be finished with its end time still ahead, so the flag settles the state and
    the clock does not."""
    meeting = in_progress_meeting(locked=False, lang=lang)

    text = shared_card.shared_body(meeting, finished=True).text

    assert MeetingDisplayMessages.FINISHED_STATUS.rich(lang=meeting.lang).text in text
    assert MeetingDisplayMessages.IN_PROGRESS_STATUS.rich(lang=meeting.lang).text not in text


def test_a_finished_meeting_that_was_never_scheduled_still_says_so(lang: str):
    """A dateless meeting is deactivated once its window runs out, and it carries no schedule for
    the status line to close, so the section carries the status alone."""
    meeting = owned_meeting(lang=lang)
    assert meeting.datetime is None  # guard: precondition for the branch under test

    lines = shared_card.shared_body(meeting, finished=True).text.splitlines()
    status = MeetingDisplayMessages.FINISHED_STATUS.rich(lang=meeting.lang).text

    assert status in lines
    assert lines[lines.index(status) - 1] == section_header_line(MeetingCardSectionMessages.WHEN, Emojis.CLOCK, meeting)


def test_a_card_nobody_asked_the_state_of_names_no_finished_status(lang: str):
    meeting = populated_meeting(lang=lang)

    text = shared_card.shared_body(meeting).text

    assert MeetingDisplayMessages.FINISHED_STATUS.rich(lang=meeting.lang).text not in text


# --- Where ---


def test_the_location_name_stands_on_its_own_row(lang: str):
    meeting = owned_meeting(lang=lang, location=MeetupLocation(name="my cousin's place"))

    assert "my cousin's place" in body_html(meeting)


def test_coordinates_become_a_map_embedded_in_the_card(lang: str):
    meeting = owned_meeting(lang=lang, location=BAR)

    assert '<tg-map lat="48.85" long="2.34" zoom="15"/>' in body_html(meeting)


def test_a_name_only_location_embeds_no_map(lang: str):
    meeting = owned_meeting(lang=lang, location=MeetupLocation(name="my cousin's place"))

    assert "<tg-map" not in body_html(meeting)


@pytest.mark.parametrize(
    "location", [BAR, MeetupLocation(name="my cousin's place"), MeetupLocation()], ids=["pinned", "named", "unset"]
)
def test_no_card_carries_a_link_out_to_a_map(location: MeetupLocation, lang: str):
    """The embedded map is tappable where it sits, and a location with no coordinates has nothing
    to point a link at, so neither shared surface offers a maps button."""
    meeting = owned_meeting(lang=lang, location=location)

    assert url_buttons(meeting_views.external_view(meeting).menu) == []
    assert url_buttons(meeting_views.build_inline_keyboard(meeting)) == []


# --- Participants ---


def test_the_count_is_bare_numbers_on_the_title_line(lang: str):
    """The title it rides already names what is being counted, so it carries no noun of its own."""
    meeting = owned_meeting(lang=lang, guests=3)
    meeting.max_members = 8

    assert MeetingCardSectionMessages.COUNT_OF_MAX.text(lang=lang, count=3, max=8) in (
        shared_card.shared_body(meeting).text
    )


def test_the_count_covers_the_waiting_list(lang: str):
    meeting = owned_meeting(lang=lang, guests=2, waiting=3)
    meeting.max_members = 2

    assert MeetingCardSectionMessages.COUNT_OF_MAX.text(lang=lang, count=5, max=2) in (
        shared_card.shared_body(meeting).text
    )


@pytest.mark.parametrize(
    "incognito, guests", [(True, 3), (False, 0), (False, 2)], ids=["incognito", "nobody_joined", "named"]
)
def test_the_count_rides_the_section_title(incognito: bool, guests: int, lang: str):
    """It says how full the meeting is, which is what the title is asking, so it reads as part of
    that title rather than as the first row under it."""
    meeting = owned_meeting(lang=lang, incognito=incognito, guests=guests)

    assert participants_header_line(meeting) in shared_card.shared_body(meeting).text


def test_the_names_open_directly_under_the_counted_title():
    """The list block draws its own break, so no break is authored against it."""
    meeting = owned_meeting(guests=1)
    count = participants_count_line(meeting).html

    assert f"{count}<ul>" in body_html(meeting)


def test_the_names_are_listed_one_per_line_under_the_counted_title():
    meeting = owned_meeting(guests=2, owner_joins=True)
    count = participants_count_line(meeting).text

    assert f"{count}\n{OWNER_NAME}\nGuest 0\nGuest 1" in shared_card.shared_body(meeting).text


def test_a_name_is_plain_text_rather_than_a_mention():
    """A mention pings the person it names on every edit a live card makes, and a busy meeting
    makes a great many."""
    meeting = owned_meeting(guests=1)

    assert "tg://user?id=" not in body_html(meeting)


def test_an_incognito_meeting_names_nobody(lang: str):
    meeting = owned_meeting(lang=lang, incognito=True, guests=3)

    assert "Guest 0" not in body_html(meeting)
    assert str(Emojis.GLASSES) in shared_card.shared_body(meeting).text


def test_the_waiting_list_closes_the_section_naming_its_members(lang: str):
    meeting = owned_meeting(lang=lang, guests=1, waiting=2)

    text = shared_card.shared_body(meeting).text
    assert text.endswith(f"{waiting_list_line(meeting, 2)}\nGuest 1\nGuest 2\n")


def test_an_incognito_waiting_list_stays_a_counted_line(lang: str):
    """Incognito hides both lists' names from everyone but the owner; the counts stand alone."""
    meeting = owned_meeting(lang=lang, incognito=True, guests=1, waiting=2)

    assert shared_card.shared_body(meeting).text.endswith(waiting_list_line(meeting, 2))


# --- The quiet block a shared card closes on ---


def test_the_bot_chat_card_closes_on_a_button_back_into_the_bot():
    """A bare link inside the muted footer does not read as tappable on every client, so the way
    back into the bot is a url chip."""
    view = meeting_views.external_view(owned_meeting())

    assert (
        f'<tg-button type="url" url="https://t.me/mitupbot?start={shared_card.BOT_CHAT_SOURCE}">{bot_links.MITUP}</tg-button>'
        in (view.message.html)
    )


def test_the_shared_card_attributes_itself_to_the_chat_it_was_shared_into():
    """The two surfaces are different acquisition stories, so the payload counts them apart."""
    view = meeting_views.inline_view(owned_meeting())

    assert f"?start={shared_card.SHARED_CHAT_SOURCE}" in view.message.html


def test_the_closing_footer_is_the_last_thing_on_a_shared_card():
    view = meeting_views.inline_view(owned_meeting(), chat_instance=CHAT_INSTANCE)

    assert view.message.html.endswith("</footer>")


@pytest.mark.parametrize(
    "build_view",
    [meeting_views.external_view, lambda meeting: meeting_views.inline_view(meeting, chat_instance=CHAT_INSTANCE)],
    ids=["bot_chat", "shared"],
)
def test_the_footer_follows_the_body_with_no_blank_line_of_its_own(
    build_view: Callable[[Meetup], MitupView], lang: str
):
    """The footer draws its own top margin, so an authored blank line would stack a second gap under
    the card."""
    view = build_view(owned_meeting(lang=lang, guests=2))

    assert "<br/><footer>" not in view.message.html
    assert "<footer>" in view.message.html


def test_the_body_alone_carries_no_link_back_into_the_bot():
    """The finished and past-meeting renders build from the body directly, and neither is a place
    to advertise the bot to somebody already using it."""
    assert "?start=" not in body_html(owned_meeting())


# --- The card everyone but the owner reads ---


def test_external_view_offers_the_way_in_above_the_back_button(lang: str):
    meeting = owned_meeting(lang=lang)

    view = meeting_views.external_view(meeting)

    assert view.menu == [
        [
            ButtonConfig(
                text=ButtonMessages.JOIN.text(lang=lang),
                callback_data=cb.JOIN.with_id(meeting.db_id),
                style="success",
            ),
            ButtonConfig(
                text=ButtonMessages.LEAVE.text(lang=lang),
                callback_data=cb.LEAVE.with_id(meeting.db_id),
                style="danger",
            ),
        ],
        [
            ButtonConfig(
                text=ButtonMessages.REFRESH.text(lang=lang),
                callback_data=cb.REFRESH_MEETING.with_id(meeting.db_id),
            )
        ],
        [ButtonConfig(text=ButtonMessages.MAIN_MENU.back(lang=lang), callback_data=cb.MAIN_MENU)],
    ]


def test_external_view_back_button_points_where_the_caller_asks():
    meeting = owned_meeting()
    back = ButtonConfig(text="≪ Joined meetings", callback_data=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(2))

    assert meeting_views.external_view(meeting, back).menu[-1] == [back]


def test_external_view_drops_the_attendance_row_while_a_locked_meeting_runs(lang: str):
    """A locked meeting freezes its list for as long as it runs, so there is nothing to offer."""
    meeting = in_progress_meeting(locked=True, lang=lang)

    view = meeting_views.external_view(meeting)

    assert view.menu == [
        [
            ButtonConfig(
                text=ButtonMessages.REFRESH.text(lang=lang),
                callback_data=cb.REFRESH_MEETING.with_id(meeting.db_id),
            )
        ],
        [ButtonConfig(text=ButtonMessages.MAIN_MENU.back(lang=lang), callback_data=cb.MAIN_MENU)],
    ]


def test_external_view_of_a_meeting_under_way_still_opens_on_its_own_title(lang: str):
    """Nothing is stacked over the heading: the meeting's name is what a reader came for, and its
    schedule already reports that it is running."""
    meeting = in_progress_meeting(locked=False, lang=lang)

    view = meeting_views.external_view(meeting)

    assert view.message.html.startswith("<h2>")
    assert MeetingDisplayMessages.IN_PROGRESS_STATUS.rich(lang=meeting.lang).text in view.message.text


def test_external_view_reports_a_full_meeting_where_the_way_in_would_be(lang: str):
    view = meeting_views.external_view(full_meeting(lang=lang))

    assert view.menu[0][0] == ButtonConfig(text=ButtonMessages.FULL.text(lang=lang), disabled=True)


def test_external_view_keeps_the_way_in_while_a_waiting_list_takes_people(lang: str):
    meeting = full_meeting(waiting_list=True)

    view = meeting_views.external_view(meeting)

    assert view.menu[0][0].callback_data == cb.JOIN.with_id(meeting.db_id)


# --- The screen shown after the user taps Leave in the private chat with the bot ---


def test_the_leave_confirmation_screen_shows_the_meeting_title_as_a_heading_and_the_confirmation(lang: str):
    meeting = owned_meeting(lang=lang)

    view = meeting_views.left_view(meeting, lang)

    assert view.message.html.startswith("<h2>")
    assert meeting.plain_title in view.message.text
    assert MeetingJoinMessages.LEFT_CONFIRMATION.rich(lang=lang).text in view.message.text


def test_the_leave_confirmation_screen_has_only_a_back_button_and_no_join_button(lang: str):
    """On a private meeting Join only works for current participants, so a Join button here would
    be refused."""
    meeting = owned_meeting(lang=lang)

    view = meeting_views.left_view(meeting, lang)

    assert view.menu == [[ButtonConfig(text=ButtonMessages.MAIN_MENU.back(lang=lang), callback_data=cb.MAIN_MENU)]]
    assert str(cb.JOIN.with_id(meeting.db_id)) not in callback_datas(view.menu)


def test_the_leave_confirmation_screen_uses_the_language_of_the_user_who_left_not_the_meetings():
    """The back button label is translated in every language, so a missing translation cannot make
    this pass by falling back to English."""
    meeting = owned_meeting(lang="es_ES")

    view = meeting_views.left_view(meeting, "de_DE")

    assert view.menu[0][0].text == ButtonMessages.MAIN_MENU.back(lang="de_DE")
    assert view.menu[0][0].text != ButtonMessages.MAIN_MENU.back(lang=meeting.lang)


# --- The keyboard the shared card carries ---


def test_inline_keyboard_keeps_a_tappable_join_on_a_full_meeting(lang: str):
    """The buttons of an inline-addressed card travel as a classic keyboard, which refuses a
    disabled button outright, so the reader keeps a Join answering with the full-meeting alert."""
    meeting = full_meeting()

    keyboard = meeting_views.build_inline_keyboard(meeting)

    assert str(cb.JOIN.with_id(meeting.db_id)) in callback_datas(keyboard)
    assert ButtonMessages.FULL.text(lang=lang) not in [button.text for row in keyboard for button in row]


@pytest.mark.parametrize("public", [True, False], ids=["public", "private"])
def test_inline_keyboard_offers_to_share_on_a_public_meeting_only(public: bool, lang: str):
    """Sharing hands the card to a chat the reader picks, which a private meeting does not invite."""
    meeting = owned_meeting(lang=lang, public=public)

    keyboard = meeting_views.build_inline_keyboard(meeting)

    shared = [str(meeting.db_id)] if public else []
    assert [button.switch_inline_query for button in share_buttons(keyboard)] == shared


@pytest.mark.parametrize("is_searchable", [False, True], ids=["not_attached", "attached"])
def test_inline_keyboard_offers_to_make_the_meeting_searchable_until_it_already_is(is_searchable: bool, lang: str):
    meeting = owned_meeting(lang=lang)

    keyboard = meeting_views.build_inline_keyboard(meeting, is_searchable=is_searchable)

    attach = str(cb.ATTACH_TO_CHAT.with_id(meeting.db_id))
    assert (attach in callback_datas(keyboard)) is not is_searchable


def test_a_shared_card_has_no_refresh_button(lang: str):
    """A shared card is redrawn on every change, so there is nothing to refresh by hand."""
    meeting = owned_meeting(lang=lang)

    keyboard = meeting_views.build_inline_keyboard(meeting)

    assert str(cb.REFRESH_MEETING.with_id(meeting.db_id)) not in callback_datas(keyboard)


def test_inline_keyboard_drops_the_attendance_row_while_a_locked_meeting_runs(lang: str):
    meeting = owned_meeting(lang=lang, invitation=True)

    keyboard = meeting_views.build_inline_keyboard(meeting, is_locked_and_in_progress=True)

    attendance = {str(cb.JOIN.with_id(meeting.db_id)), str(cb.INVITE.with_id(meeting.db_id))}
    assert attendance.isdisjoint(callback_datas(keyboard))


# --- The result an inline query is answered with ---


def test_inline_view_lists_the_meeting_by_its_title_under_the_meetings_own_id():
    meeting = owned_meeting()

    view = meeting_views.inline_view(meeting)

    assert view.id == "7"
    assert view.title == "Board game night"


@pytest.mark.parametrize("attached", [False, True], ids=["not_attached", "attached"])
def test_inline_view_reports_where_the_card_stands_as_a_muted_closing_line(attached: bool, lang: str):
    """A card is redrawn on every join, so this reads as a state rather than as news, and it sits in
    the quiet block at the bottom rather than in the body it would otherwise rank beside."""
    meeting = owned_meeting(lang=lang)
    state = MeetingAttachMessages.STATE_SEARCHABLE if attached else MeetingAttachMessages.STATE_NOT_SEARCHABLE

    view = meeting_views.inline_view(meeting, chat_instance=CHAT_INSTANCE if attached else None)

    assert closing_footer_html(view).startswith(state.rich(lang=meeting.lang).html)


def test_the_state_line_and_the_link_back_into_the_bot_share_one_quiet_footer(lang: str):
    """Two closing footers stacked read as two paragraphs, so both lines sit in one, the bot's own
    last. The only other footer on the card is the byline closing its heading."""
    meeting = owned_meeting(lang=lang)
    view = meeting_views.inline_view(meeting, chat_instance=CHAT_INSTANCE)
    state = MeetingAttachMessages.STATE_SEARCHABLE.rich(lang=meeting.lang).html

    footer = closing_footer_html(view)

    assert view.message.html.count("<footer>") == 2
    assert footer.startswith(f"{state}<br/>")
    assert f"?start={shared_card.SHARED_CHAT_SOURCE}" in footer


def test_the_bot_chat_card_closes_on_the_bot_line_alone(lang: str):
    """Searchability is about the chat a card was shared into, which the owner's own chat is not."""
    meeting = owned_meeting(lang=lang)

    view = meeting_views.external_view(meeting)

    for state in (MeetingAttachMessages.STATE_SEARCHABLE, MeetingAttachMessages.STATE_NOT_SEARCHABLE):
        assert state.rich(lang=meeting.lang).text not in view.message.text


def test_inline_view_of_an_attached_card_stops_offering_to_attach_it_again(lang: str):
    meeting = owned_meeting(lang=lang)

    view = meeting_views.inline_view(meeting, chat_instance=CHAT_INSTANCE)

    assert str(cb.ATTACH_TO_CHAT.with_id(meeting.db_id)) not in callback_datas(view.menu)


def test_inline_view_freezes_the_attendance_row_of_a_locked_meeting_under_way(lang: str):
    meeting = in_progress_meeting(locked=True, lang=lang, invitation=True)

    view = meeting_views.inline_view(meeting)

    assert str(cb.JOIN.with_id(meeting.db_id)) not in callback_datas(view.menu)


def test_inline_view_of_a_meeting_under_way_still_opens_on_its_own_title(lang: str):
    meeting = in_progress_meeting(locked=False, lang=lang)

    view = meeting_views.inline_view(meeting)

    assert view.message.html.startswith("<h2>")
    assert MeetingDisplayMessages.IN_PROGRESS_STATUS.rich(lang=meeting.lang).text in view.message.text


# --- Fitting the card to its budget ---


def test_a_card_that_fits_names_everyone():
    meeting = owned_meeting(guests=20)

    body = shared_card.fitted_shared_body(meeting)

    assert body.text_length <= MEETING_CARD_BUDGET
    assert body.text.count("Guest ") == 20


def test_an_over_budget_card_names_as_many_as_fit_and_counts_the_rest():
    meeting = owned_meeting(guests=OVER_BUDGET_GUESTS)
    assert shared_card.shared_body(meeting).text_length > MEETING_CARD_BUDGET

    body = shared_card.fitted_shared_body(meeting)
    hidden = OVER_BUDGET_GUESTS - body.text.count("Guest ")

    assert body.text_length <= MEETING_CARD_BUDGET
    assert hidden > 0
    assert MeetingDisplayMessages.PARTICIPANTS_TRUNCATED.text(lang=meeting.lang, count=hidden) in body.text


def test_a_fitted_card_gives_up_names_before_anything_else():
    """Everything else on the card is the meeting itself, which is what a reader came for."""
    meeting = populated_meeting(guests=OVER_BUDGET_GUESTS)

    body = shared_card.fitted_shared_body(meeting)

    assert "Board game night" in body.text
    assert "Bring snacks" in body.text
    assert "<tg-map" in body.html


# --- The photos the card opens with ---


def shared_media_ids(view: MitupView) -> list[str]:
    return [entry["id"] for entry in view.rich_message().to_api_dict().get("media", [])]


def test_the_banner_sits_under_the_heading_and_above_the_first_section():
    """The banner comes after the title and byline and before the first horizontal rule."""
    meeting = with_images(populated_meeting(), 3)

    html = shared_card.shared_body(meeting).html

    assert html.index("</footer>") < html.index("<tg-collage>")
    assert html.index("</tg-collage>") < html.index("<hr/>")


def test_the_banner_is_drawn_in_the_arrangement_the_meeting_holds():
    collage = shared_card.shared_body(with_images(owned_meeting(), 3, ImageLayout.COLLAGE)).html
    slideshow = shared_card.shared_body(with_images(owned_meeting(), 3, ImageLayout.SLIDESHOW)).html

    assert "<tg-collage>" in collage
    assert "<tg-slideshow>" in slideshow


def test_a_meeting_with_no_photos_draws_no_banner():
    assert "<img" not in shared_card.shared_body(with_images(owned_meeting(), 0)).html


def test_a_finished_meeting_keeps_its_photos():
    """The card stops taking joins, but it is still the meeting people came to read."""
    meeting = with_images(populated_meeting(), 2)

    assert "<tg-collage>" in shared_card.shared_body(meeting, finished=True).html


def test_the_bot_chat_card_carries_every_photo_it_draws():
    meeting = with_images(owned_meeting(), 3)

    assert shared_media_ids(shared_card.external_view(meeting)) == ["uniq-0", "uniq-1", "uniq-2"]


def test_the_shared_card_carries_every_photo_it_draws():
    meeting = with_images(owned_meeting(), 3)

    assert shared_media_ids(shared_card.inline_view(meeting)) == ["uniq-0", "uniq-1", "uniq-2"]


def test_a_picked_inline_result_sends_the_photos_with_the_card():
    """The result carries the card as its own message, so the media travels with the content."""
    meeting = with_images(owned_meeting(), 2)

    result = shared_card.inline_view(meeting).inline_result()

    assert [entry["id"] for entry in result["input_message_content"]["rich_message"]["media"]] == [
        "uniq-0",
        "uniq-1",
    ]
