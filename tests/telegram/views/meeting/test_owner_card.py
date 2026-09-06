import datetime as dt

from mitup_bot.emojis import Emojis
from mitup_bot.images import ImageLayout
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup, MeetupLocation
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingCardSectionMessages, MeetingDisplayMessages
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.meeting.fitting import MEETING_CARD_BUDGET
from mitup_bot.views.meeting.schedule import schedule_content
from tests.telegram.views.meeting.helpers import (
    OWNER_NAME,
    attendee_lines,
    body_lines,
    in_progress_meeting,
    owned_meeting,
    participants_header_line,
    populated_meeting,
    section_header_line,
    url_buttons,
    with_images,
)


def follows_in_order(html: str, pieces: list[str]):
    """Assert each piece appears after the one before it, matching repeats one at a time."""
    cursor = 0
    for piece in pieces:
        found = html.find(piece, cursor)
        assert found >= 0, f"{piece!r} is missing from the card, or sits ahead of what should precede it"
        cursor = found + len(piece)


def section_header_markup(title: MeetingCardSectionMessages, meeting: Meetup) -> str:
    """One section title as the card renders it: bold, never a heading tag."""
    return f"<b>{title.rich(lang=meeting.user_language).text}</b>"


def section_header_html(title: MeetingCardSectionMessages, glyph: Emojis, meeting: Meetup) -> str:
    """The whole title line as it is rendered: the glyph, then the word in bold."""
    return f"{glyph} {section_header_markup(title, meeting)}"


# --- Body composition ---


def test_owner_view_body_runs_from_the_title_down_to_who_is_coming():
    """The title, a rule, then a titled section per field, and the attendees below the last rule.

    Each section title introduces its own block, so the ordering is checked against the first
    control inside each one rather than against the titles alone.
    """
    meeting = populated_meeting(guests=1)

    follows_in_order(
        meeting_views.owner_view(meeting).message.html,
        [
            "Board game night",
            "<hr/>",
            section_header_markup(MeetingCardSectionMessages.IMAGES, meeting),
            str(cb.EDIT_MEETING_IMAGES.with_id(meeting.db_id)),
            "<hr/>",
            section_header_markup(MeetingCardSectionMessages.DESCRIPTION, meeting),
            "Bring snacks",
            "<hr/>",
            section_header_markup(MeetingCardSectionMessages.WHEN, meeting),
            str(cb.OPEN_START_EDITOR.with_id(meeting.db_id)),
            section_header_markup(MeetingCardSectionMessages.WHERE, meeting),
            str(cb.EDIT_MEETING_LOCATION_NAME.with_id(meeting.db_id)),
            "<hr/>",
            section_header_markup(MeetingCardSectionMessages.PARTICIPANTS, meeting),
            str(cb.EDIT_MEETING_MAX_PARTICIPANTS.with_id(meeting.db_id)),
            "Guest 0",
        ],
    )


def test_section_titles_are_bold_rather_than_headings(lang: str):
    """A heading tag switches clients to a serif face with no font control, which reads as another
    document inside the card. The title line keeps the only heading on the card."""
    meeting = populated_meeting(lang=lang)

    html = meeting_views.owner_view(meeting).message.html

    titles = (
        MeetingCardSectionMessages.DESCRIPTION,
        MeetingCardSectionMessages.WHEN,
        MeetingCardSectionMessages.WHERE,
        MeetingCardSectionMessages.PARTICIPANTS,
    )
    for title in titles:
        rendered = title.rich(lang=meeting.user_language).text
        assert f"<b>{rendered}</b>" in html
        assert f"<h2>{rendered}</h2>" not in html
    assert html.count("<h2>") == 1


def test_each_section_title_leads_into_its_own_block(lang: str):
    """The blank line between the field sections falls above the second title, not below it."""
    meeting = populated_meeting(lang=lang)

    html = meeting_views.owner_view(meeting).message.html
    where_header = section_header_html(MeetingCardSectionMessages.WHERE, Emojis.MAP, meeting)

    assert html[: html.index(where_header)].endswith("<br/><br/>")
    assert html[html.index(where_header) + len(where_header) :].startswith("<br/>")


# --- Section titles: when they render, and what they take off the rows below ---


def test_a_titled_section_takes_its_glyph_off_the_row_below_it(lang: str):
    """The glyph belongs to the section, so a row directly under a title does not repeat it.

    Each title's own glyph appears once, on the title. The rows that distinguish themselves from
    the row above keep theirs, which is checked separately below.
    """
    meeting = populated_meeting(lang=lang)

    lines = body_lines(meeting)
    titles = [
        (section_header_line(MeetingCardSectionMessages.IMAGES, Emojis.IMAGES, meeting), Emojis.IMAGES),
        (section_header_line(MeetingCardSectionMessages.DESCRIPTION, Emojis.DESCRIPTION, meeting), Emojis.DESCRIPTION),
        (section_header_line(MeetingCardSectionMessages.WHEN, Emojis.CLOCK, meeting), Emojis.CLOCK),
        (section_header_line(MeetingCardSectionMessages.WHERE, Emojis.MAP, meeting), Emojis.MAP),
        (participants_header_line(meeting), Emojis.JOINED),
    ]
    for header, glyph in titles:
        index = lines.index(header)
        assert not lines[index + 1].startswith(str(glyph))


def test_the_start_and_end_rows_are_labelled_and_sit_a_blank_line_apart(lang: str):
    """Each row is its label and value over its own chips; the blank line keeps one row's chips
    from being taken for the other's on a phone."""
    meeting = populated_meeting(lang=lang)

    lines = body_lines(meeting)
    when = lines.index(section_header_line(MeetingCardSectionMessages.WHEN, Emojis.CLOCK, meeting))

    assert meeting.datetime is not None and meeting.end_datetime is not None
    start = schedule_content(meeting, meeting.datetime, None)
    end = schedule_content(meeting, meeting.end_datetime, None)
    assert lines[when + 1] == MeetingCardSectionMessages.START_TIME.rich(lang=lang, when=start).text
    assert ButtonMessages.REMOVE.text(lang=lang) in lines[when + 2]
    assert lines[when + 3] == ""
    assert lines[when + 4] == MeetingCardSectionMessages.END_TIME.rich(lang=lang, when=end).text
    assert ButtonMessages.REMOVE.text(lang=lang) in lines[when + 5]


def test_a_meeting_with_a_start_but_no_end_offers_the_end_row_a_blank_line_below(lang: str):
    meeting = owned_meeting(lang=lang)
    meeting.datetime = dt.datetime(2026, 9, 1, 18, 0, tzinfo=dt.UTC)

    lines = body_lines(meeting)
    when = lines.index(section_header_line(MeetingCardSectionMessages.WHEN, Emojis.CLOCK, meeting))

    assert lines[when + 3] == ""
    assert lines[when + 4] == ButtonMessages.SET_END_DATETIME.text(lang=lang)


def test_the_pin_row_sits_a_blank_line_under_the_name_row_and_keeps_its_glyph(lang: str):
    """The pin glyph distinguishes the row from the one above rather than repeating the title."""
    meeting = populated_meeting(lang=lang)

    lines = body_lines(meeting)
    where = lines.index(section_header_line(MeetingCardSectionMessages.WHERE, Emojis.MAP, meeting))

    assert ButtonMessages.REMOVE.text(lang=lang) in lines[where + 2]
    assert lines[where + 3] == ""
    assert lines[where + 4].startswith(str(Emojis.PIN))


def test_every_section_is_titled_on_a_meeting_that_holds_nothing(lang: str):
    """The owner reads the same card whatever the meeting holds: a title over every section, so an
    empty one is a row named by the words above it rather than a bare control."""
    meeting = owned_meeting(lang=lang)

    lines = body_lines(meeting)

    titles = [
        section_header_line(MeetingCardSectionMessages.IMAGES, Emojis.IMAGES, meeting),
        section_header_line(MeetingCardSectionMessages.DESCRIPTION, Emojis.DESCRIPTION, meeting),
        section_header_line(MeetingCardSectionMessages.WHEN, Emojis.CLOCK, meeting),
        section_header_line(MeetingCardSectionMessages.WHERE, Emojis.MAP, meeting),
        participants_header_line(meeting),
    ]
    assert all(title in lines for title in titles)


def test_a_meeting_with_no_schedule_offers_a_set_row_with_no_clock_of_its_own(lang: str):
    """The title carries the clock, so the row under it does not repeat it."""
    meeting = owned_meeting(lang=lang)

    lines = body_lines(meeting)
    when = lines.index(section_header_line(MeetingCardSectionMessages.WHEN, Emojis.CLOCK, meeting))

    assert ButtonMessages.SET_DATETIME.text(lang=lang) in lines[when + 1]
    assert not lines[when + 1].startswith(str(Emojis.CLOCK))


def test_a_meeting_with_no_location_drops_the_map_glyph_and_keeps_the_pin(lang: str):
    """The name row sits under the title that carries the map; the pin row keeps its own glyph,
    which is what tells the two rows apart."""
    meeting = owned_meeting(lang=lang)

    lines = body_lines(meeting)
    where = lines.index(section_header_line(MeetingCardSectionMessages.WHERE, Emojis.MAP, meeting))

    assert ButtonMessages.SET_LOCATION_NAME.text(lang=lang) in lines[where + 1]
    assert not lines[where + 1].startswith(str(Emojis.MAP))
    assert lines[where + 2] == ""
    assert ButtonMessages.SET_COORDINATES.text(lang=lang) in lines[where + 3]
    assert lines[where + 3].startswith(str(Emojis.PIN))


def test_a_location_with_only_a_pin_leaves_the_name_row_glyphless(lang: str):
    meeting = owned_meeting(lang=lang, location=MeetupLocation(coordinates=(2.34, 48.85)))

    lines = body_lines(meeting)
    where = lines.index(section_header_line(MeetingCardSectionMessages.WHERE, Emojis.MAP, meeting))

    assert ButtonMessages.SET_LOCATION_NAME.text(lang=lang) in lines[where + 1]
    assert not lines[where + 1].startswith(str(Emojis.MAP))


def test_description_chips_sit_on_their_own_line():
    """Trailing the description, the chips would wrap apart wherever its last line ends."""
    meeting = owned_meeting()

    html = meeting_views.owner_view(meeting).message.html
    assert "Bring snacks<br/><tg-button" in html
    description = html.index("Bring snacks<br/>")
    chips_line = html[description : html.index("<hr/>", description)]
    assert str(cb.EDIT_MEETING_DESCRIPTION.with_id(meeting.db_id)) in chips_line
    assert str(cb.DELETE_MEETING_DESCRIPTION.with_id(meeting.db_id)) in chips_line


def test_the_description_section_stands_even_when_the_meeting_has_none(lang: str):
    """The owner reads the same card whatever the meeting holds, so the empty section offers the
    chip that fills it rather than dropping out the way a shared card's does."""
    meeting = owned_meeting(lang=lang)
    meeting.description = None

    lines = body_lines(meeting)
    header = lines.index(section_header_line(MeetingCardSectionMessages.DESCRIPTION, Emojis.DESCRIPTION, meeting))

    assert lines[header + 1] == ButtonMessages.ADD_DESCRIPTION.text(lang=lang)
    assert str(cb.EDIT_MEETING_DESCRIPTION.with_id(meeting.db_id)) in meeting_views.owner_view(meeting).message.html


def test_owner_view_separates_the_when_and_where_blocks_with_a_blank_line():
    """The settled editor spacing: one blank line between the two field blocks."""
    meeting = owned_meeting()

    html = meeting_views.owner_view(meeting).message.html
    start_chip_end = html.index(str(cb.OPEN_START_EDITOR.with_id(meeting.db_id)))
    location_chip = html.index(str(cb.EDIT_MEETING_LOCATION_NAME.with_id(meeting.db_id)))

    assert "<br/><br/>" in html[start_chip_end:location_chip]


def test_owner_view_embeds_the_map_instead_of_linking_out_to_it():
    """The location's map rides in the body, so the card carries no url button at all."""
    meeting = owned_meeting(location=MeetupLocation(name="The usual bar", coordinates=(2.34, 48.85)))

    view = meeting_views.owner_view(meeting)

    assert "<tg-map " in view.message.html
    assert url_buttons(view.menu) == []


def test_owner_view_reports_a_running_meeting_under_its_schedule(lang: str):
    """The status closes the When block instead of outranking the title above the card."""
    meeting = in_progress_meeting(locked=False, lang=lang)

    view = meeting_views.owner_view(meeting)

    status = MeetingDisplayMessages.IN_PROGRESS_STATUS.rich(lang=meeting.user_language).text
    assert status in view.message.text
    assert view.message.html.startswith("<h2>")


def test_owner_view_of_a_meeting_not_under_way_carries_no_status_line(lang: str):
    meeting = owned_meeting(lang=lang)

    status = MeetingDisplayMessages.IN_PROGRESS_STATUS.rich(lang=meeting.user_language).text
    assert status not in meeting_views.owner_view(meeting).message.text


# --- Menu ---


def test_owner_view_menu_is_share_then_settings_and_delete_then_refresh_then_back(lang: str):
    meeting = owned_meeting(lang=lang)

    view = meeting_views.owner_view(meeting)

    assert view.menu == [
        [
            ButtonConfig(
                text=ButtonMessages.SHARE.text(lang=lang),
                switch_inline_query=str(meeting.db_id),
                style="primary",
            ),
        ],
        [
            ButtonConfig(
                text=ButtonMessages.SETTINGS.text(lang=lang),
                callback_data=cb.EDIT_MEETING_SETTINGS.with_id(meeting.db_id),
            ),
            ButtonConfig(
                text=ButtonMessages.DELETE.text(lang=lang),
                callback_data=cb.DELETE_MEETING.with_id(meeting.db_id),
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


def test_owner_view_back_button_points_where_the_caller_asks():
    meeting = owned_meeting()
    back = ButtonConfig(text="≪ Active meetings", callback_data=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(2))

    assert meeting_views.owner_view(meeting, back).menu[-1] == [back]


# --- Fitting the card to its budget ---


# Enough guests that their names alone carry the card past its budget: each is about ten
# characters of text, and the budget is the wire ceiling less the room a banner and a keyboard take.
OVER_BUDGET_GUESTS = 4000


def crowded_meeting(guests: int) -> Meetup:
    return owned_meeting(guests=guests, owner_joins=True)


def test_a_card_that_fits_names_everyone():
    meeting = crowded_meeting(20)

    view = meeting_views.owner_view(meeting)
    named = [line for line in view.message.text.splitlines() if line.startswith("Guest ")]

    assert view.message.text_length <= MEETING_CARD_BUDGET
    assert len(named) == 20


def test_an_over_budget_card_names_as_many_as_fit_and_counts_the_rest():
    meeting = crowded_meeting(OVER_BUDGET_GUESTS)
    assert meeting_views.owner_body(meeting).text_length > MEETING_CARD_BUDGET

    view = meeting_views.owner_view(meeting)
    named = [line for line in view.message.text.splitlines() if line.startswith("Guest ")]
    hidden = OVER_BUDGET_GUESTS - len(named)

    assert view.message.text_length <= MEETING_CARD_BUDGET
    assert hidden > 0
    assert MeetingDisplayMessages.PARTICIPANTS_TRUNCATED.text(lang=meeting.user_language, count=hidden) in (
        view.message.text
    )


def test_a_fitted_card_gives_up_names_before_anything_else():
    """Everything but the names is a control the owner acts on, so all of it survives the cut."""
    meeting = crowded_meeting(OVER_BUDGET_GUESTS)

    view = meeting_views.owner_view(meeting)
    html = view.message.html

    assert str(cb.EDIT_MEETING_TITLE.with_id(meeting.db_id)) in html
    assert str(cb.EDIT_MEETING_MAX_PARTICIPANTS.with_id(meeting.db_id)) in html
    assert "Bring snacks" in view.message.text


def test_a_fitted_card_never_cuts_the_owners_own_row():
    meeting = crowded_meeting(OVER_BUDGET_GUESTS)

    assert attendee_lines(meeting)[0].startswith(OWNER_NAME)


# --- The photos the card opens with ---


def owner_media_ids(meeting: Meetup) -> list[str]:
    return [entry["id"] for entry in meeting_views.owner_view(meeting).rich_message().to_api_dict().get("media", [])]


def test_a_meeting_with_no_photos_offers_the_chip_that_adds_the_first(lang: str):
    meeting = with_images(owned_meeting(lang=lang), 0)

    lines = body_lines(meeting)
    images = lines.index(section_header_line(MeetingCardSectionMessages.IMAGES, Emojis.IMAGES, meeting))

    assert lines[images + 1] == ButtonMessages.ADD_IMAGES.text(lang=lang)


def test_a_meeting_that_holds_photos_offers_the_chip_that_changes_them(lang: str):
    meeting = with_images(owned_meeting(lang=lang), 3)

    lines = body_lines(meeting)
    images = lines.index(section_header_line(MeetingCardSectionMessages.IMAGES, Emojis.IMAGES, meeting))

    assert lines[images + 1] == ButtonMessages.EDIT_IMAGES.text(lang=lang)


def test_the_banner_is_drawn_once_inside_the_section_that_titles_it():
    """The Images section is the only place the card draws the banner."""
    meeting = with_images(populated_meeting(), 3)

    html = meeting_views.owner_view(meeting).message.html

    assert html.count("<tg-collage>") == 1
    assert html.index("<tg-collage>") > html.index(section_header_markup(MeetingCardSectionMessages.IMAGES, meeting))
    assert html.index("</tg-collage>") < html.index(
        section_header_markup(MeetingCardSectionMessages.DESCRIPTION, meeting)
    )


def test_the_banner_is_drawn_in_the_arrangement_the_meeting_holds():
    meeting = with_images(owned_meeting(), 3, ImageLayout.SLIDESHOW)

    assert "<tg-slideshow>" in meeting_views.owner_view(meeting).message.html


def test_the_owner_card_carries_every_photo_it_draws():
    assert owner_media_ids(with_images(owned_meeting(), 3)) == ["uniq-0", "uniq-1", "uniq-2"]


def test_a_card_with_no_photos_carries_none():
    assert owner_media_ids(with_images(owned_meeting(), 0)) == []
