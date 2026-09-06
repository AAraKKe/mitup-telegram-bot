from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from mitup_bot.datetimes import DateFormat
from mitup_bot.emojis import Emojis
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.translations import SUPPORTED_LANGUAGES
from mitup_bot.utils import ButtonMessages, MeetingEditSettingsMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.rich_message import RichContent, RichTag, horizontal_rule_content, keyboard_content
from mitup_bot.views import settings_sections
from mitup_bot.views.datetime_format import localized_datetime
from mitup_bot.views.factory import LANGUAGE_BUTTONS, LANGUAGE_GRID_COLUMNS
from mitup_bot.views.mitup_view import MitupView, arrange_in_grid

if TYPE_CHECKING:
    from mitup_bot.models import Meetup


def language_button(meeting: Meetup, index: int, language_code: str) -> ButtonConfig:
    """One language on the settings card. The meeting's current language carries the primary
    accent, so the choice in force reads off the row itself.

    The index is the language's position in `SUPPORTED_LANGUAGES`, which is what the callback
    carries: a language code would not fit the callback's numeric id.
    """
    return ButtonConfig(
        text=LANGUAGE_BUTTONS[language_code].text(lang=meeting.user_language),
        callback_data=cb.SET_MEETING_LANGUAGE.with_ids(meeting_id=meeting.db_id, id=index),
        style="primary" if language_code == meeting.lang else None,
    )


def language_group(meeting: Meetup) -> RichContent:
    """The language the meeting speaks, picked in place.

    The languages are laid out as full-width rows in the same grid the user's own picker uses, so
    a language reads as something to tap rather than as one more word in the paragraph: an inline
    chip is drawn at a fixed size the message cannot influence, and a button row is the one shape
    that fills the width available.
    """
    buttons = [language_button(meeting, index, code) for index, code in enumerate(SUPPORTED_LANGUAGES)]
    grid = arrange_in_grid(buttons, min(len(SUPPORTED_LANGUAGES), LANGUAGE_GRID_COLUMNS))
    heading = ButtonMessages.LANGUAGE.rich(lang=meeting.user_language).wrap(RichTag.BOLD)
    return heading.append(keyboard_content(grid))


def behavior_sections(meeting: Meetup) -> list[RichContent]:
    lang = meeting.owner.lang
    return [
        settings_sections.toggle_section(
            ButtonMessages.WAITING_LIST,
            MeetingEditSettingsMessages.WAITING_LIST_EXPLANATION,
            lang,
            cb.SET_MEETING_WAITING_LIST.with_id(meeting.db_id),
            meeting.waiting_list,
        ),
        settings_sections.toggle_section(
            ButtonMessages.PUBLIC,
            MeetingEditSettingsMessages.PUBLIC_EXPLANATION,
            lang,
            cb.SET_MEETING_PUBLIC.with_id(meeting.db_id),
            meeting.public,
        ),
        settings_sections.toggle_section(
            ButtonMessages.OPEN_INVITATION,
            MeetingEditSettingsMessages.OPEN_INVITATIONS_EXPLANATION,
            lang,
            cb.SET_MEETING_ALLOW_INVITATIONS.with_id(meeting.db_id),
            meeting.allow_invitation,
        ),
        settings_sections.toggle_section(
            ButtonMessages.INCOGNITO,
            MeetingEditSettingsMessages.INCOGNITO_EXPLANATION,
            lang,
            cb.SET_MEETING_INCOGNITO.with_id(meeting.db_id),
            meeting.incognito,
        ),
        settings_sections.toggle_section(
            ButtonMessages.LOCK_ON_START,
            MeetingEditSettingsMessages.LOCK_ON_START_EXPLANATION,
            lang,
            cb.SET_MEETING_LOCK_ON_START.with_id(meeting.db_id),
            meeting.lock_on_start,
        ),
    ]


def date_format_preview(meeting: Meetup, date_format: DateFormat) -> str:
    """The meeting's start written under *date_format*, in the language everyone reads the meeting in."""
    return localized_datetime(
        meeting.datetime or settings_sections.SAMPLE_MOMENT,
        lang=meeting.lang,
        tz=meeting.timezone,
        created=meeting.created_time if meeting.datetime else None,
        time_format=replace(meeting.time_format, date_format=date_format),
    )


def time_format_sections(meeting: Meetup) -> list[RichContent]:
    lang = meeting.owner.lang
    return [
        settings_sections.toggle_section(
            ButtonMessages.SHOW_TIMEZONE,
            MeetingEditSettingsMessages.SHOW_TIMEZONE_EXPLANATION,
            lang,
            cb.SET_MEETING_SHOW_TIMEZONE.with_id(meeting.db_id),
            meeting.show_timezone,
        ),
        settings_sections.toggle_section(
            ButtonMessages.CLOCK_24H,
            MeetingEditSettingsMessages.CLOCK_24H_EXPLANATION,
            lang,
            cb.SET_MEETING_CLOCK_24H.with_id(meeting.db_id),
            meeting.clock_24h,
        ),
        settings_sections.date_format_section(
            lang,
            current=meeting.date_format,
            chip_callback=lambda index: cb.SET_MEETING_DATE_FORMAT.with_ids(meeting_id=meeting.db_id, id=index),
            explanation=MeetingEditSettingsMessages.DATE_FORMAT_EXPLANATION,
            preview=lambda date_format: date_format_preview(meeting, date_format),
        ),
    ]


def settings_view(meeting: Meetup) -> MitupView:
    """The meeting's settings card: the language picked in place, and one line per sub-card saying
    what it holds."""
    lang = meeting.owner.lang
    heading = ButtonMessages.SETTINGS.rich(lang=lang).wrap(RichTag.H2)
    screens = RichContent.join(
        "\n\n",
        [
            settings_sections.behavior_open_section(lang, cb.OPEN_MEETING_BEHAVIOR.with_id(meeting.db_id)),
            settings_sections.time_format_open_section(lang, cb.OPEN_MEETING_TIME_FORMAT.with_id(meeting.db_id)),
        ],
    )
    body = RichContent.join(horizontal_rule_content(), [heading, language_group(meeting), screens])
    return MitupView(body).with_back_button(ButtonMessages.MEETING, lang, cb.EDIT_MEETING.with_id(meeting.db_id))


def behavior_view(meeting: Meetup) -> MitupView:
    lang = meeting.owner.lang
    body = settings_sections.sub_card_body(
        MeetingEditSettingsMessages.BEHAVIOR_GROUP, Emojis.CONTROLS, lang, behavior_sections(meeting)
    )
    return MitupView(body).with_back_button(
        ButtonMessages.SETTINGS, lang, cb.EDIT_MEETING_SETTINGS.with_id(meeting.db_id)
    )


def time_format_view(meeting: Meetup) -> MitupView:
    lang = meeting.owner.lang
    body = settings_sections.sub_card_body(
        MeetingEditSettingsMessages.TIME_FORMAT_GROUP, Emojis.CLOCK, lang, time_format_sections(meeting)
    )
    return MitupView(body).with_back_button(
        ButtonMessages.SETTINGS, lang, cb.EDIT_MEETING_SETTINGS.with_id(meeting.db_id)
    )
