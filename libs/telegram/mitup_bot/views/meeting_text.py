from __future__ import annotations

import urllib.parse
from string.templatelib import Template
from typing import TYPE_CHECKING

from telegram import MessageEntity

from mitup_bot.utils import (
    ButtonMessages,
    Emojis,
    MeetingDisplayMessages,
    MeetingEditParticipantsMessages,
)
from mitup_bot.utils.entities import EntityDateTime, FormattedText, parse_format_tags, render

if TYPE_CHECKING:
    from mitup_bot.models import JoinedUsers, Meetup, MeetupLocation


def rich_title(meeting: Meetup) -> FormattedText:
    """Meeting title with the owner's formatting and custom-emoji entities restored."""
    return parse_format_tags(meeting.tagged_title, {})


def rich_description(meeting: Meetup) -> FormattedText | None:
    """Meeting description with its entities restored; None mirrors an unset or empty
    description so callers keep their placeholder branches."""
    if not (tagged := meeting.tagged_description):
        return None
    return parse_format_tags(tagged, {})


def participant_name(link: JoinedUsers) -> FormattedText:
    name = link.user.display_name
    if link.invited_by is not None:
        language = link.meetup.lang
        invited_by_text = MeetingDisplayMessages.INVITED_BY.get(lang=language, user=link.invited_by.inline_name)
        return render(t"{name} ({invited_by_text})")
    return FormattedText(name)


MAPS_SEARCH_URL = "https://www.google.com/maps/search/"


def maps_url(location: MeetupLocation) -> str | None:
    """Google Maps universal search link for the location, or None when it has no coordinates or name.

    Coordinates are stored as (longitude, latitude); Google expects latitude,longitude, so the pair is
    flipped here. `api=1` is required by Google's Maps URL API.
    """
    if location.coordinates is not None:
        longitude, latitude = location.coordinates
        query = f"{latitude},{longitude}"
    elif location.coerced_name is not None:
        query = location.coerced_name
    else:
        return None
    return f"{MAPS_SEARCH_URL}?{urllib.parse.urlencode({'api': 1, 'query': query})}"


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


def participants_list_text(meeting: Meetup) -> FormattedText:
    """
    Textual representation of the list of participants in the meeting with one line per participant.

    If there are users in the waiting list, they are shown after the participants with a separator and a title.
    """
    participant_list = [participant_name(link) for link in meeting.joined_links if not link.is_waiting_list]
    waiting_list = [participant_name(link) for link in meeting.joined_links if link.is_waiting_list]

    participants_part = (
        FormattedText("\n  ").append(FormattedText.join("\n  ", participant_list))
        if participant_list
        else FormattedText("")
    )

    if waiting_list:
        waiting_header = ButtonMessages.WAITING_LIST.get(lang=meeting.lang)
        waiting_names = FormattedText.join("\n  ", waiting_list)
        waiting_section = (
            FormattedText(f"\n--- {Emojis.WAITING} ").append(waiting_header).append(" \n  ").append(waiting_names)
        )
        return participants_part.append(waiting_section)

    return participants_part


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


def meeting_message(meeting: Meetup) -> FormattedText:
    description = rich_description(meeting) or MeetingDisplayMessages.DESCRIPTION_NOT_SET.get(lang=meeting.lang)
    created_by = MeetingDisplayMessages.CREATED_BY.get(lang=meeting.lang, owner=meeting.owner.display_name)
    location = location_description(meeting.location, lang=meeting.lang)
    datetime_part = datetime_section(meeting)
    participants_part = participants_text_with_list(meeting)
    title = rich_title(meeting).wrap(MessageEntity.BOLD)
    return render(
        t"{title} ({created_by})\n\n"
        t"--- {Emojis.DESCRIPTION} {description}\n"
        t"{datetime_part}"
        t"--- {Emojis.MAP} {location}\n"
        t"--- {Emojis.JOINED} {participants_part}"
    )


def inline_message(meeting: Meetup) -> FormattedText:
    """
    Similar to `meeting_message` but used when the meeting is shared inline.
    Properties that are not set are omitted.
    """
    created_by = MeetingDisplayMessages.CREATED_BY.get(lang=meeting.lang, owner=meeting.owner.display_name)
    description_section: Template | str = (
        t"\n--- {Emojis.DESCRIPTION} {description}" if (description := rich_description(meeting)) else ""
    )
    location_section: Template | str = (
        ""
        if meeting.location.empty()
        else t"\n--- {Emojis.MAP} {location_description(meeting.location, lang=meeting.lang)}\n"
    )
    participants_part = participants_text(meeting)
    title = rich_title(meeting).wrap(MessageEntity.BOLD)
    if meeting.datetime is not None:
        datetime_part = datetime_section(meeting)
        return render(
            t"{title} ({created_by})"
            t"{description_section}"
            t"\n{datetime_part}"
            t"{location_section}"
            t"--- {Emojis.JOINED} {participants_part}"
        )
    return render(
        t"{title} ({created_by}){description_section}{location_section}\n--- {Emojis.JOINED} {participants_part}"
    )


def inline_query_message(meeting: Meetup) -> FormattedText:
    """Plain-text preview shown below the title in inline query results."""
    result = t"{Emojis.JOINED} {participants_badge(meeting)}"

    if meeting.datetime:
        return render(t"{result}\n{Emojis.CLOCK} {plain_datetime(meeting)}")

    return render(result)
