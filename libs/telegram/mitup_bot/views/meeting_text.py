from __future__ import annotations

import urllib.parse
from bisect import bisect_right
from dataclasses import dataclass
from string.templatelib import Template
from typing import TYPE_CHECKING

from telegram import MessageEntity

from mitup_bot import limits
from mitup_bot.utils import (
    ButtonMessages,
    Emojis,
    MeetingDisplayMessages,
    MeetingEditParticipantsMessages,
)
from mitup_bot.utils.entities import (
    MAX_MESSAGE_UTF16_LENGTH,
    EntityDateTime,
    FormattedText,
    ellipsize,
    parse_stored_tagged_text,
    render,
    truncate,
    utf16_len,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from mitup_bot.models import JoinedUsers, Meetup, MeetupLocation


def rich_title(meeting: Meetup) -> FormattedText:
    """Meeting title with the owner's formatting and custom-emoji entities restored."""
    return parse_stored_tagged_text(meeting.tagged_title, field="title")


def rich_description(meeting: Meetup) -> FormattedText | None:
    """Meeting description with its entities restored; None mirrors an unset or empty
    description so callers keep their placeholder branches."""
    if not (tagged := meeting.tagged_description):
        return None
    return parse_stored_tagged_text(tagged, field="description")


def participant_name(link: JoinedUsers) -> FormattedText:
    name = link.user.display_name
    if link.invited_by is not None:
        language = link.meetup.lang
        invited_by_text = MeetingDisplayMessages.INVITED_BY.get(lang=language, user=link.invited_by.inline_name)
        return render(t"{name} ({invited_by_text})")
    return FormattedText(name)


MAPS_SEARCH_URL = "https://www.google.com/maps/search/"


def maps_url(location: MeetupLocation) -> str | None:
    """Google Maps universal search link for the location, or None when it has no coordinates.

    The name never links: it is arbitrary user text ("my cousin's place"), so a search link built
    from it points anywhere or nowhere. Coordinates are stored as (longitude, latitude); Google
    expects latitude,longitude, so the pair is flipped here. `api=1` is required by Google's Maps
    URL API.
    """
    if location.coordinates is None:
        return None
    longitude, latitude = location.coordinates
    return f"{MAPS_SEARCH_URL}?{urllib.parse.urlencode({'api': 1, 'query': f'{latitude},{longitude}'})}"


def location_description(location: MeetupLocation, lang: str) -> FormattedText:
    match location.coerced_name, location.coordinates:
        case (None, None):
            return MeetingDisplayMessages.LOCATION_NOT_SET.get(lang=lang)
        case _:
            name_section = f"{location.coerced_name}" if location.coerced_name else ""
            coordinates_section = f"[{Emojis.PIN}]" if location.coordinates else ""
            return FormattedText(f"{name_section} {coordinates_section}".strip())


def plain_datetime(meeting: Meetup) -> str:
    """UTC-formatted plain datetime string used in inline query previews."""
    if meeting.datetime:
        return f"{meeting.datetime:%Y-%m-%d %H:%M}"
    return MeetingDisplayMessages.DATE_NOT_SET.get_text(lang=meeting.lang)


def participants_badge(meeting: Meetup) -> Template:
    """Plain-text badge shown in inline query result descriptions."""
    empty = MeetingDisplayMessages.PARTICIPANT_COUNT_EMPTY.get(lang=meeting.lang)
    joined_count = len(meeting.joined_links)
    no_limit = t"({MeetingEditParticipantsMessages.NO_LIMIT_LABEL.get(lang=meeting.user_language)})"

    incognito_prefix = f"{Emojis.GLASSES} " if meeting.incognito else ""

    cap = meeting.effective_max_members
    if cap is None:
        result_badged = empty if joined_count == 0 else t"{len(meeting.joined_links)} {no_limit}"
        return t"{incognito_prefix}{result_badged}"

    max_label = MeetingDisplayMessages.MAX_PARTICIPANTS_LABEL.get(lang=meeting.lang, max_participants=cap)
    empty_with_max = t"{empty} {max_label}"
    result_badged = empty_with_max if joined_count == 0 else t"({joined_count}/{cap})"
    return t"{incognito_prefix}{result_badged}"


@dataclass(frozen=True)
class ListedParticipants:
    """The participant links a card lists by name, and how many it leaves out."""

    joined: list[JoinedUsers]
    waiting: list[JoinedUsers]
    hidden: int


def listed_participants(meeting: Meetup, shown: int | None) -> ListedParticipants:
    """Split the meeting's links into the names a card lists and a count of the rest.

    Confirmed participants are listed ahead of the waiting list, so a card with room for only a
    few names spends it on the people who are actually going.
    """
    joined = [link for link in meeting.joined_links if not link.is_waiting_list]
    waiting = [link for link in meeting.joined_links if link.is_waiting_list]
    limit = len(meeting.joined_links) if shown is None else shown
    listed_joined = joined[:limit]
    listed_waiting = waiting[: max(limit - len(joined), 0)]
    hidden = len(meeting.joined_links) - len(listed_joined) - len(listed_waiting)
    return ListedParticipants(joined=listed_joined, waiting=listed_waiting, hidden=hidden)


def names_block(links: list[JoinedUsers]) -> FormattedText:
    """One indented line per participant, or nothing at all when there are none."""
    if not links:
        return FormattedText("")
    return FormattedText("\n  ").append(FormattedText.join("\n  ", [participant_name(link) for link in links]))


def waiting_list_block(meeting: Meetup, links: list[JoinedUsers]) -> FormattedText:
    waiting_header = ButtonMessages.WAITING_LIST.get(lang=meeting.lang)
    waiting_names = FormattedText.join("\n  ", [participant_name(link) for link in links])
    return FormattedText(f"\n--- {Emojis.WAITING} ").append(waiting_header).append(" \n  ").append(waiting_names)


def hidden_participants_line(meeting: Meetup, hidden: int) -> FormattedText:
    truncated = MeetingDisplayMessages.PARTICIPANTS_TRUNCATED.get(lang=meeting.lang, count=hidden)
    return FormattedText("\n  ").append(truncated)


def participants_list_text(meeting: Meetup, shown: int | None = None) -> FormattedText:
    """
    Textual representation of the list of participants in the meeting with one line per participant.

    If there are users in the waiting list, they are shown after the participants with a separator and a title.

    *shown* caps how many names are listed, replacing the rest with a single line counting them.
    None lists everyone.
    """
    listed = listed_participants(meeting, shown)
    block = names_block(listed.joined)
    if listed.waiting:
        block = block.append(waiting_list_block(meeting, listed.waiting))
    if listed.hidden:
        block = block.append(hidden_participants_line(meeting, listed.hidden))
    return block


def participants_text(meeting: Meetup) -> Template:
    """
    Textual representation of the participants information of the meeting. The list of participants is not included
    for incognito meetings.

    To get the participants text ignoring whether the meeting is incognito or not,
    use `participants_text_with_list`.
    """
    participant_list: Template | FormattedText | str = t"" if meeting.incognito else participants_list_text(meeting)
    return t"{participants_text_title(meeting)}{participant_list}"


def participants_text_title(meeting: Meetup) -> Template:
    """
    This is the title of the participants section of the meeting.

    It includes things like the number of participants, the maximum number of participants, etc.
    """
    if len(meeting.joined_links) == 0:
        total_participants: FormattedText | Template = MeetingDisplayMessages.PARTICIPANT_COUNT_EMPTY.get(
            lang=meeting.lang
        )
    elif len(meeting.joined_links) == 1:
        total_participants = t"1 {MeetingDisplayMessages.PARTICIPANT_LABEL.get(lang=meeting.lang)}"
    else:
        n = len(meeting.joined_links)
        total_participants = t"{n} {MeetingDisplayMessages.PARTICIPANTS_LABEL.get(lang=meeting.lang)}"

    cap = meeting.effective_max_members
    max_participants: FormattedText | Template = (
        MeetingDisplayMessages.MAX_PARTICIPANTS_LABEL.get(lang=meeting.lang, max_participants=cap)
        if cap is not None
        else t"({MeetingEditParticipantsMessages.NO_LIMIT_LABEL.get(lang=meeting.lang)})"
    )

    incognito_prefix = f"{Emojis.GLASSES} " if meeting.incognito else ""
    return t"{incognito_prefix}{total_participants} {max_participants}"


def participants_text_with_list(meeting: Meetup) -> Template:
    """
    Textual representation of the participants section of the meeting. The list of participants is always included.
    """
    return t"{participants_text_title(meeting)}{participants_list_text(meeting)}"


def datetime_section(meeting: Meetup) -> Template:
    """Date/time section for the meeting message.

    When an end time is set, shows separate start and stop lines using ▶️/⏹️.
    Otherwise shows a single clock line with the datetime or a not-set placeholder.
    """
    if meeting.datetime is None:
        datetime_display = MeetingDisplayMessages.DATE_NOT_SET.get(lang=meeting.lang)
        return t"--- {Emojis.CLOCK} {datetime_display}\n"

    start_entity = EntityDateTime(MeetingDisplayMessages.DATETIME_ENTITY_LABEL.get_text(), meeting.datetime, "DT")

    if meeting.end_datetime is None:
        return t"--- {Emojis.CLOCK} {start_entity}\n"

    stop_entity = EntityDateTime(MeetingDisplayMessages.DATETIME_ENTITY_LABEL.get_text(), meeting.end_datetime, "DT")
    start_label = MeetingDisplayMessages.START_LABEL.get(lang=meeting.lang)
    stop_label = MeetingDisplayMessages.END_LABEL.get(lang=meeting.lang)
    return t"--- {Emojis.START} {start_label}: {start_entity}\n--- {Emojis.STOP} {stop_label}: {stop_entity}\n"


# The view layer prepends a state banner ("in progress", the finished summary) to a rendered card,
# in whichever language the meeting is in, so a card holds this much back for the longest of them.
STATE_BANNER_RESERVE = 256

# A card is built from stored free text — title, description, location name — plus a participants
# list that grows with every join, and stored values honour no length limit. A card rendered past
# Telegram's cap leaves the meeting permanently unviewable, since every tap on it re-renders the
# same rejected edit, so a card is fitted to this budget instead of being sent as it comes out.
MEETING_CARD_BUDGET = MAX_MESSAGE_UTF16_LENGTH - STATE_BANNER_RESERVE


@dataclass(frozen=True)
class CardSections:
    """The parts of a meeting card that grow without bound, in the order they are given up when
    the card does not fit its budget.

    A card renderer substitutes each part verbatim exactly once, which is what makes the length of
    the surrounding chrome measurable by subtraction.
    """

    description: FormattedText
    location: FormattedText
    participants: FormattedText

    @property
    def length(self) -> int:
        """Combined length of the three parts in UTF-16 code units."""
        return sum(utf16_len(part.text) for part in (self.description, self.location, self.participants))


type CardRenderer = Callable[[Meetup, CardSections], FormattedText]


def collapsed_participants(meeting: Meetup, section: FormattedText, room: int) -> FormattedText:
    """Re-render the participants list with only the leading names that fit in *room*.

    It bottoms out at the line counting the names left out rather than at nothing, because a card
    that drops the section outright reads as if nobody had joined. A card that lists nobody by
    design — an incognito one — gets no names put back: an empty section stays empty.
    """
    if not section.text or utf16_len(section.text) <= room:
        return section
    # Below the full list every rendering carries the "names not shown" line, so length grows
    # strictly with the number of names and the largest count that fits is a bisection.
    counts = range(len(meeting.joined_links))
    fitting = bisect_right(counts, room, key=lambda count: utf16_len(participants_list_text(meeting, count).text))
    return participants_list_text(meeting, max(fitting - 1, 0))


def wrapped_section(section: FormattedText, floor: int, overflow: int) -> tuple[FormattedText, int]:
    """Ellipsize *section* by up to *overflow* UTF-16 code units without taking it under *floor*.

    Returns the shortened section together with the overflow it could not absorb. A section already
    at or under its floor — including one the card leaves out entirely — comes back untouched, so a
    floor never reserves room a section is not using.
    """
    natural = utf16_len(section.text)
    fitted = ellipsize(section, max(floor, natural - overflow))
    return fitted, overflow - (natural - utf16_len(fitted.text))


def fitted_sections(meeting: Meetup, sections: CardSections, room: int) -> CardSections:
    """Shrink *sections* so their combined length fits *room* UTF-16 code units.

    Each step gives up only as much as the remaining overflow demands, in the order the parts are
    worth losing: the description down to its guaranteed length first, then the place name down to
    its own, and the participants list only once both stand at their floors and the card is still
    over. Names are what a card protects longest: a collapsed list misreads as a meeting nobody has
    joined, while an ellipsized description still reads as a description.
    """
    overflow = sections.length - room
    if overflow <= 0:
        return sections
    description, overflow = wrapped_section(sections.description, limits.DESCRIPTION_GUARANTEED_CHARS, overflow)
    location, overflow = wrapped_section(sections.location, limits.LOCATION_NAME_GUARANTEED_CHARS, overflow)
    participants_room = utf16_len(sections.participants.text) - overflow
    return CardSections(
        description=description,
        location=location,
        participants=collapsed_participants(meeting, sections.participants, participants_room),
    )


def fitted_card(meeting: Meetup, sections: CardSections, render_card: CardRenderer) -> FormattedText:
    """Render a meeting card, giving up its unbounded sections until it fits the budget.

    The chrome a card never gives up — title, owner, date/time, participant counts — is itself
    unbounded in stored data, so the result is cut outright as a last resort: a card that loses its
    closing lines still shows the meeting, while an over-long one cannot be sent at all.
    """
    card = render_card(meeting, sections)
    if utf16_len(card.text) <= MEETING_CARD_BUDGET:
        return card
    chrome = utf16_len(card.text) - sections.length
    fitted = fitted_sections(meeting, sections, max(MEETING_CARD_BUDGET - chrome, 0))
    return truncate(render_card(meeting, fitted), MEETING_CARD_BUDGET)


def meeting_card_sections(meeting: Meetup) -> CardSections:
    return CardSections(
        description=rich_description(meeting) or MeetingDisplayMessages.DESCRIPTION_NOT_SET.get(lang=meeting.lang),
        location=location_description(meeting.location, lang=meeting.lang),
        participants=participants_list_text(meeting),
    )


def meeting_card(meeting: Meetup, sections: CardSections) -> FormattedText:
    created_by = MeetingDisplayMessages.CREATED_BY.get(lang=meeting.lang, owner=meeting.owner.display_name)
    datetime_part = datetime_section(meeting)
    participants_title = participants_text_title(meeting)
    title = rich_title(meeting).wrap(MessageEntity.BOLD)
    return render(
        t"{title} ({created_by})\n\n"
        t"--- {Emojis.DESCRIPTION} {sections.description}\n"
        t"{datetime_part}"
        t"--- {Emojis.MAP} {sections.location}\n"
        t"--- {Emojis.JOINED} {participants_title}{sections.participants}"
    )


def meeting_message(meeting: Meetup) -> FormattedText:
    return fitted_card(meeting, meeting_card_sections(meeting), meeting_card)


def inline_card_sections(meeting: Meetup) -> CardSections:
    """The unbounded parts of the inline card, empty where the card omits them entirely."""
    has_location = not meeting.location.empty()
    return CardSections(
        description=rich_description(meeting) or FormattedText(""),
        location=location_description(meeting.location, lang=meeting.lang) if has_location else FormattedText(""),
        participants=FormattedText("") if meeting.incognito else participants_list_text(meeting),
    )


def inline_card(meeting: Meetup, sections: CardSections) -> FormattedText:
    created_by = MeetingDisplayMessages.CREATED_BY.get(lang=meeting.lang, owner=meeting.owner.display_name)
    description_section: Template | str = (
        t"\n--- {Emojis.DESCRIPTION} {sections.description}" if sections.description.text else ""
    )
    location_section: Template | str = "" if meeting.location.empty() else t"\n--- {Emojis.MAP} {sections.location}\n"
    participants_title = participants_text_title(meeting)
    title = rich_title(meeting).wrap(MessageEntity.BOLD)
    if meeting.datetime is not None:
        datetime_part = datetime_section(meeting)
        return render(
            t"{title} ({created_by})"
            t"{description_section}"
            t"\n{datetime_part}"
            t"{location_section}"
            t"--- {Emojis.JOINED} {participants_title}{sections.participants}"
        )
    return render(
        t"{title} ({created_by}){description_section}{location_section}"
        t"\n--- {Emojis.JOINED} {participants_title}{sections.participants}"
    )


def inline_message(meeting: Meetup) -> FormattedText:
    """
    Similar to `meeting_message` but used when the meeting is shared inline.
    Properties that are not set are omitted.
    """
    return fitted_card(meeting, inline_card_sections(meeting), inline_card)


def inline_query_message(meeting: Meetup) -> FormattedText:
    """Plain-text preview shown below the title in inline query results."""
    result = t"{Emojis.JOINED} {participants_badge(meeting)}"

    if meeting.datetime:
        return render(t"{result}\n{Emojis.CLOCK} {plain_datetime(meeting)}")

    return render(result)
