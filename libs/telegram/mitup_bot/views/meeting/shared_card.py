from __future__ import annotations

import datetime as dt
from functools import partial
from typing import TYPE_CHECKING

from mitup_bot import bot_links
from mitup_bot.keyboards import ButtonConfig, Keyboard
from mitup_bot.utils import (
    ButtonMessages,
    Emojis,
    InlineQueryMessages,
    MeetingAttachMessages,
    MeetingCardSectionMessages,
    MeetingDisplayMessages,
    MeetingJoinMessages,
)
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.rich_message import (
    RichContent,
    RichTag,
    button_content,
    horizontal_rule_content,
    map_content,
    unordered_list_content,
)
from mitup_bot.utils.rich_template import render_rich
from mitup_bot.views.meeting.banner import banner_content, meeting_photos
from mitup_bot.views.meeting.controls import join_leave_row, main_menu_back_button, meeting_chip
from mitup_bot.views.meeting.fitting import fitted_body
from mitup_bot.views.meeting.schedule import schedule_content
from mitup_bot.views.meeting.sections import (
    named_items,
    participants_count_line,
    waiting_list_line,
)
from mitup_bot.views.meeting_text import (
    description_content,
    inline_query_message,
    title_content,
)
from mitup_bot.views.mitup_view import MitupInlineView, MitupView
from mitup_bot.views.sections import card_section, section_header

if TYPE_CHECKING:
    from mitup_bot.models import Meetup

# The `/start` payload the closing footer carries, naming the surface a new reader arrived from.
# Reading a card in the bot chat and finding one in a chat somebody shared it into are different
# acquisition stories, so they are counted apart.
BOT_CHAT_SOURCE = "meetingcard"
SHARED_CHAT_SOURCE = "sharedcard"


def byline(meeting: Meetup) -> RichContent:
    """Who is hosting, and on a public meeting an inert chip saying it may be shared on.

    The muted block sets it below the title as the quieter half of one heading, and the rule and
    section that follow absorb the margin it draws.
    """
    created_by = MeetingDisplayMessages.CREATED_BY.rich(lang=meeting.lang, owner=meeting.owner.display_name)
    if not meeting.public:
        return created_by.wrap(RichTag.FOOTER)
    chip = button_content(
        ButtonConfig(text=ButtonMessages.PUBLIC.text(lang=meeting.lang), disabled=True, style="success")
    )
    return render_rich(t"{created_by} · {chip}").wrap(RichTag.FOOTER)


def when_lines(
    meeting: Meetup, start_value: dt.datetime | None, *, finished: bool = False, with_status: bool = True
) -> RichContent:
    """The schedule as one line, the end folded into a span when the meeting has one, and under it
    the state the clock puts the meeting in.

    A finished meeting says so whatever the clock reads: it may have no end time, or no schedule
    at all, for the clock to have passed. A surface whose heading already names the state passes
    `with_status` off.
    """
    lines = []
    if start_value is not None:
        lines.append(schedule_content(meeting, start_value, meeting.end_datetime))
    if with_status and finished:
        lines.append(MeetingDisplayMessages.FINISHED_STATUS.rich(lang=meeting.lang))
    elif with_status and meeting.is_in_progress:
        lines.append(MeetingDisplayMessages.IN_PROGRESS_STATUS.rich(lang=meeting.lang))
    return RichContent.join("\n", lines)


def location_lines(meeting: Meetup) -> RichContent:
    """The place name, and the map itself once the meeting names coordinates.

    The map is the location's own row rather than a link out to one: it is tappable where it sits,
    which is why the card carries no Open-in-Maps button beside it.
    """
    location = meeting.location
    name_line = RichContent(location.coerced_name) if location.coerced_name else RichContent()
    if location.coordinates is None:
        return name_line
    longitude, latitude = location.coordinates
    return name_line.append(map_content(latitude, longitude))


def attendee_names(meeting: Meetup, shown: int | None) -> RichContent:
    """The confirmed attendees as a bullet list, closing on an item counting the ones left out.

    The names are plain text: a mention would ping the person it names on every edit a live card
    makes, and a busy meeting makes a great many.
    """
    return unordered_list_content(named_items(meeting.participants, shown, meeting.lang))


def description_section(meeting: Meetup) -> RichContent:
    """What the meeting says about itself, under its title, or nothing at all when it says nothing.

    A reader of somebody else's card cannot fill an empty description, so being told it is empty
    only costs them a section.
    """
    if (description := description_content(meeting)) is None:
        return RichContent()
    return card_section(MeetingCardSectionMessages.DESCRIPTION, Emojis.DESCRIPTION, meeting.lang, description)


def when_section(meeting: Meetup, *, finished: bool = False, with_status: bool = True) -> RichContent:
    """A finished meeting keeps the section even without a schedule: its status line is the one
    place the card names the state."""
    if meeting.datetime is None and not finished:
        return RichContent()
    return card_section(
        MeetingCardSectionMessages.WHEN,
        Emojis.CLOCK,
        meeting.lang,
        when_lines(meeting, meeting.datetime, finished=finished, with_status=with_status),
    )


def where_section(meeting: Meetup) -> RichContent:
    """The location under its title, or nothing at all when the meeting names none."""
    if meeting.location.empty():
        return RichContent()
    return card_section(MeetingCardSectionMessages.WHERE, Emojis.MAP, meeting.lang, location_lines(meeting))


def participants_section(meeting: Meetup, shown: int | None) -> RichContent:
    """Who is coming, counted on the title line itself: the names as a bullet list, then the
    waiting list as its own counted line over its own list. An incognito meeting names nobody in
    either list; the counts stand for them."""
    header = section_header(
        MeetingCardSectionMessages.PARTICIPANTS,
        Emojis.JOINED,
        meeting.lang,
        trailer=participants_count_line(meeting),
    )
    names_shown = not meeting.incognito
    named = bool(meeting.participants) and names_shown
    body = attendee_names(meeting, shown) if named else RichContent()
    if meeting.n_waiting:
        line = waiting_list_line(meeting, meeting.lang)
        # The list block draws its own break; a line following the header needs one authored.
        body = body.append(line) if named else body.append("\n").append(line)
        if names_shown:
            body = body.append(unordered_list_content(named_items(meeting.waiting_links(), shown, meeting.lang)))
    return header.append(body)


def closing_footer(lang: str, source: str, state: RichContent | None = None) -> RichContent:
    """The one muted block a shared card closes on: *state*, then the link back into the bot.

    The link's `/start` payload names *source*, which the acquisition stamp reads to attribute
    whatever registration the tap leads to. Callers append this to the body rather than adding it as
    a footnote: the footer draws its own top margin, and a footnote's blank line stacks with it.
    """
    mitup = button_content(ButtonConfig(text=bot_links.MITUP, url=bot_links.start_link(source)))
    built_with = MeetingDisplayMessages.BUILT_WITH.rich(lang=lang, mitup=mitup)
    if state is None:
        return built_with.wrap(RichTag.FOOTER)
    return RichContent.join("\n", [state, built_with]).wrap(RichTag.FOOTER)


def shared_body(meeting: Meetup, shown: int | None = None, *, finished: bool = False) -> RichContent:
    """The meeting as everyone but its owner reads it, its edit chips taken out.

    A section holding nothing is left out, participants excepted: a meeting nobody has joined yet is
    a fact about it rather than a gap in it. *shown* caps how many attendees are named. Blocks draw
    their own margins, so no blank line is authored against one.
    """
    heading = title_content(meeting).wrap(RichTag.H2).append(byline(meeting))
    if (banner := banner_content(meeting)) is not None:
        heading = heading.append("\n").append(banner)
    fields = [section for section in (when_section(meeting, finished=finished), where_section(meeting)) if section]
    blocks = [
        heading,
        description_section(meeting),
        RichContent.join("\n\n", fields),
        participants_section(meeting, shown),
    ]
    return RichContent.join(horizontal_rule_content(), [block for block in blocks if block])


def fitted_shared_body(meeting: Meetup) -> RichContent:
    """The shared body, naming as many attendees and waiters as the budget has room for."""
    return fitted_body(partial(shared_body, meeting), max(len(meeting.participants), meeting.n_waiting))


def external_view(meeting: Meetup, back_button: ButtonConfig | None = None) -> MitupView:
    """This is the view shown to users that do not own the meeting when checking through meetings I have joined.

    `back_button` lets the caller point the trailing back button at the list the user came
    from; when omitted it defaults to the main menu.
    """
    keyboard: Keyboard = []
    if meeting.attendance_is_open:
        keyboard.append(join_leave_row(meeting, meeting.user_language, inert_when_full=True))
    keyboard.append([meeting_chip(ButtonMessages.REFRESH, meeting, cb.REFRESH_MEETING)])
    keyboard.append([back_button or main_menu_back_button(meeting)])
    body = (
        fitted_shared_body(meeting)
        .append(horizontal_rule_content())
        .append(closing_footer(meeting.lang, BOT_CHAT_SOURCE))
    )
    return MitupView(body, keyboard, photos=meeting_photos(meeting))


def left_view(meeting: Meetup, lang: str) -> MitupView:
    """The screen shown after the user taps Leave in the private chat with the bot: the meeting's
    title, a line saying they are no longer in it, and the Main menu button.

    It has no Join button: on a private meeting Join only works for current participants, which
    the user has just stopped being.
    """
    body = title_content(meeting).wrap(RichTag.H2).append(MeetingJoinMessages.LEFT_CONFIRMATION.rich(lang=lang))
    return MitupView(body, []).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)


def build_inline_keyboard(
    meeting: Meetup, *, is_searchable: bool = False, is_locked_and_in_progress: bool = False
) -> Keyboard:
    """The keyboard a shared card carries.

    Its Join stays tappable on a full meeting instead of becoming the inert Full chip the bot-chat
    card shows. A card addressed by `inline_message_id` carries its buttons as a classic keyboard
    (see `classic_markup`), which cannot express an inert button and refuses one outright, so the
    reader keeps a Join that answers with the alert saying the meeting is full.
    """
    keyboard: Keyboard = []

    if not is_locked_and_in_progress:
        keyboard.append(join_leave_row(meeting, meeting.lang))

    if meeting.public:
        keyboard.append(
            [
                ButtonConfig(text=ButtonMessages.SHARE.text(lang=meeting.lang), switch_inline_query=str(meeting.db_id)),
            ]
        )

    if not is_searchable:
        keyboard.append(
            [
                ButtonConfig(
                    text=ButtonMessages.MAKE_SEARCHABLE.text(lang=meeting.lang),
                    callback_data=cb.ATTACH_TO_CHAT.with_id(meeting.db_id),
                ),
            ]
        )

    return keyboard


def inline_view(meeting: Meetup, *, chat_instance: str | None = None) -> MitupInlineView:
    is_searchable = chat_instance is not None
    searchable_state = (
        MeetingAttachMessages.STATE_SEARCHABLE.rich(lang=meeting.lang)
        if is_searchable
        else MeetingAttachMessages.STATE_NOT_SEARCHABLE.rich(lang=meeting.lang)
    )
    body = (
        fitted_shared_body(meeting)
        .append(horizontal_rule_content())
        .append(closing_footer(meeting.lang, SHARED_CHAT_SOURCE, searchable_state))
    )
    return MitupInlineView(
        message=body,
        menu=build_inline_keyboard(
            meeting,
            is_searchable=is_searchable,
            is_locked_and_in_progress=not meeting.attendance_is_open,
        ),
        photos=meeting_photos(meeting),
        id=str(meeting.db_id),
        title=meeting.plain_title,
        inline_description=inline_query_message(meeting),
    )


def unavailable_inline_view(lang: str) -> MitupInlineView:
    """Inline placeholder shown when a shared meeting is cancelled or inaccessible.

    Telegram requires every inline query to be answered; returning this result keeps the
    picker from stalling silently when the meeting behind a stale share button is gone.
    """
    return MitupInlineView(
        message=InlineQueryMessages.MEETING_UNAVAILABLE_MESSAGE.rich(lang=lang),
        menu=[],
        id="meeting_unavailable",
        title=InlineQueryMessages.MEETING_UNAVAILABLE_TITLE.text(lang=lang),
        inline_description=InlineQueryMessages.MEETING_UNAVAILABLE_DESCRIPTION.text(lang=lang),
    )
