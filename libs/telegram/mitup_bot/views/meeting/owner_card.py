from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from mitup_bot.keyboards import ButtonConfig, Keyboard
from mitup_bot.utils import ButtonMessages, Emojis, MeetingCardSectionMessages, MeetingDisplayMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.rich_message import RichContent, RichTag, button_content, horizontal_rule_content, map_content
from mitup_bot.utils.rich_template import render_rich
from mitup_bot.views.meeting.attendees import attendee_section
from mitup_bot.views.meeting.banner import banner_content, meeting_photos
from mitup_bot.views.meeting.controls import main_menu_back_button, meeting_chip
from mitup_bot.views.meeting.fitting import fitted_body
from mitup_bot.views.meeting.schedule import schedule_content
from mitup_bot.views.meeting.sections import participants_count_line
from mitup_bot.views.meeting_text import description_content, title_content
from mitup_bot.views.mitup_view import MitupView
from mitup_bot.views.sections import card_section

if TYPE_CHECKING:
    from mitup_bot.models import Meetup


def editor_title_line(meeting: Meetup) -> RichContent:
    """The title at the card's settled heading size, its edit chip on the same line."""
    title = title_content(meeting)
    edit = meeting_chip(ButtonMessages.EDIT, meeting, cb.EDIT_MEETING_TITLE)
    return render_rich(t"{title} {edit}").wrap(RichTag.H2)


def editor_description_lines(meeting: Meetup) -> RichContent:
    """The description and the chips that change it, or the chip that sets one.

    The title above the block carries the page for the whole section, so neither row carries one.
    """
    description = description_content(meeting)
    if description is None:
        return button_content(meeting_chip(ButtonMessages.ADD_DESCRIPTION, meeting, cb.EDIT_MEETING_DESCRIPTION))
    edit = meeting_chip(ButtonMessages.EDIT, meeting, cb.EDIT_MEETING_DESCRIPTION)
    remove = meeting_chip(ButtonMessages.REMOVE, meeting, cb.DELETE_MEETING_DESCRIPTION, style="danger")
    # The chips get their own line: trailing a multi-line description, they would wrap apart from
    # each other at whatever point the last line happens to end.
    return render_rich(t"{description}\n{edit} {remove}")


def editor_when_lines(meeting: Meetup) -> RichContent:
    """The start row and, once a start exists, the end row under it, a blank line between them so
    the chips of one cannot be taken for the other's.

    Each row opens its own editor, so a schedule is set from the card itself. The start's remove
    chip clears the whole schedule (an end without a start is meaningless, and the lock has no
    window left to freeze), which its confirmation states.
    """
    if meeting.datetime is None:
        return button_content(meeting_chip(ButtonMessages.SET_DATETIME, meeting, cb.OPEN_START_EDITOR))

    lang = meeting.user_language
    start = MeetingCardSectionMessages.START_TIME.rich(
        lang=lang, when=schedule_content(meeting, meeting.datetime, None)
    )
    start_edit = meeting_chip(ButtonMessages.EDIT, meeting, cb.OPEN_START_EDITOR)
    start_remove = meeting_chip(ButtonMessages.REMOVE, meeting, cb.DELETE_MEETING_TIMES, style="danger")
    start_row = render_rich(t"{start}\n{start_edit} {start_remove}")

    if meeting.end_datetime is None:
        end_row = button_content(meeting_chip(ButtonMessages.SET_END_DATETIME, meeting, cb.OPEN_END_EDITOR))
    else:
        end = MeetingCardSectionMessages.END_TIME.rich(
            lang=lang, when=schedule_content(meeting, meeting.end_datetime, None)
        )
        end_edit = meeting_chip(ButtonMessages.EDIT, meeting, cb.OPEN_END_EDITOR)
        end_remove = meeting_chip(ButtonMessages.REMOVE, meeting, cb.DELETE_MEETING_END_TIME, style="danger")
        end_row = render_rich(t"{end}\n{end_edit} {end_remove}")

    rows = RichContent.join("\n\n", [start_row, end_row])
    if meeting.is_in_progress:
        return rows.append("\n").append(MeetingDisplayMessages.IN_PROGRESS_STATUS.rich(lang=lang))
    return rows


def editor_location_lines(meeting: Meetup) -> RichContent:
    """The place-name row and, a blank line below it, the map-pin row: one property per row, so
    each row's chips read against the value they act on and cannot be taken for the other's.

    The title above the block carries the map for the whole section, so the name row carries none.
    The pin keeps its own glyph: it tells the two rows apart rather than repeating the title.
    """
    location = meeting.location
    if location.coerced_name:
        name = location.coerced_name
        name_edit = meeting_chip(ButtonMessages.EDIT, meeting, cb.EDIT_MEETING_LOCATION_NAME)
        name_remove = meeting_chip(ButtonMessages.REMOVE, meeting, cb.DELETE_MEETING_LOCATION_NAME, style="danger")
        name_row = render_rich(t"{name}\n{name_edit} {name_remove}")
    else:
        set_name = meeting_chip(ButtonMessages.SET_LOCATION_NAME, meeting, cb.EDIT_MEETING_LOCATION_NAME)
        name_row = button_content(set_name)

    if location.coordinates:
        pin_edit = meeting_chip(ButtonMessages.EDIT, meeting, cb.EDIT_MEETING_LOCATION_COORDINATES)
        pin_remove = meeting_chip(ButtonMessages.REMOVE, meeting, cb.DELETE_MEETING_COORDINATES, style="danger")
        pin_row = render_rich(t"{Emojis.PIN} {pin_edit} {pin_remove}")
    else:
        set_pin = meeting_chip(ButtonMessages.SET_COORDINATES, meeting, cb.EDIT_MEETING_LOCATION_COORDINATES)
        pin_row = render_rich(t"{Emojis.PIN} {set_pin}")

    rows = RichContent.join("\n\n", [name_row, pin_row])
    if location.coordinates is None:
        return rows
    # The chips stay on the lines above: the map itself is tappable (it opens the location), so it
    # cannot carry controls of its own.
    longitude, latitude = location.coordinates
    return rows.append(map_content(latitude, longitude))


def editor_images_lines(meeting: Meetup) -> RichContent:
    """The banner, and under it the chip that opens the Images screen.

    The chip is shown to every owner, Host or not, so a non-Host learns about the perk from the
    card itself.
    """
    label = ButtonMessages.EDIT_IMAGES if meeting.images else ButtonMessages.ADD_IMAGES
    chip = button_content(meeting_chip(label, meeting, cb.EDIT_MEETING_IMAGES))
    banner = banner_content(meeting)
    return chip if banner is None else banner.append(chip)


def images_section(meeting: Meetup) -> RichContent:
    """The photos the card shows, under their title."""
    return card_section(
        MeetingCardSectionMessages.IMAGES,
        Emojis.IMAGES,
        meeting.user_language,
        editor_images_lines(meeting),
    )


def description_section(meeting: Meetup) -> RichContent:
    """What the meeting says about itself, under its title."""
    return card_section(
        MeetingCardSectionMessages.DESCRIPTION,
        Emojis.DESCRIPTION,
        meeting.user_language,
        editor_description_lines(meeting),
    )


def when_section(meeting: Meetup) -> RichContent:
    """The schedule under its title."""
    return card_section(
        MeetingCardSectionMessages.WHEN, Emojis.CLOCK, meeting.user_language, editor_when_lines(meeting)
    )


def where_section(meeting: Meetup) -> RichContent:
    """The location under its title."""
    return card_section(
        MeetingCardSectionMessages.WHERE, Emojis.MAP, meeting.user_language, editor_location_lines(meeting)
    )


def participants_section(meeting: Meetup, shown: int | None = None) -> RichContent:
    """Who is coming, counted on its own title line. *shown* caps how many guests are named."""
    return card_section(
        MeetingCardSectionMessages.PARTICIPANTS,
        Emojis.JOINED,
        meeting.user_language,
        attendee_section(meeting, shown),
        trailer=participants_count_line(meeting),
    )


def owner_body(meeting: Meetup, shown: int | None = None) -> RichContent:
    """The owner card's body: the meeting's own fields above the rule, who is coming below it.

    Every section is titled, so the card holds the same shape from an empty meeting to a full one
    and each row offering to fill a field is named by the title standing over it.
    """
    # The card title and the rules are blocks that end their own line, so only the sections need
    # explicit separation, and a blank line between them keeps the card breathable.
    return (
        editor_title_line(meeting)
        .append(horizontal_rule_content())
        .append(images_section(meeting))
        .append(horizontal_rule_content())
        .append(description_section(meeting))
        .append(horizontal_rule_content())
        .append(RichContent.join("\n\n", [when_section(meeting), where_section(meeting)]))
        .append(horizontal_rule_content())
        .append(participants_section(meeting, shown))
    )


def fitted_owner_body(meeting: Meetup) -> RichContent:
    """The owner card's body, naming as many guests and waiters as the budget has room for.

    Everything on the card besides the names is a control the owner acts on, so the names are what
    gives way. The owner's own row is built apart from the guest list, so it is never what a fitted
    card drops.
    """
    return fitted_body(partial(owner_body, meeting), max(len(meeting.guest_links), meeting.n_waiting))


def owner_view(meeting: Meetup, back_button: ButtonConfig | None = None) -> MitupView:
    """The owner's card: every field carries the chip that edits it, so an edit starts on the card
    itself, and the attendee list names who is coming.

    `back_button` lets the caller point the trailing back button at the list the user came from;
    when omitted it defaults to the main menu.
    """
    lang = meeting.user_language
    menu: Keyboard = [
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
        [meeting_chip(ButtonMessages.REFRESH, meeting, cb.REFRESH_MEETING)],
        [back_button or main_menu_back_button(meeting)],
    ]
    return MitupView(fitted_owner_body(meeting), menu, photos=meeting_photos(meeting))
