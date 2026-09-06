from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from mitup_bot.datetimes import DateFormat
from mitup_bot.emojis import Emojis
from mitup_bot.utils import ButtonMessages, MeetingEditSettingsMessages, SettingsMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.rich_message import RichContent, RichTag, horizontal_rule_content
from mitup_bot.views import MitupView, settings_sections
from mitup_bot.views.datetime_format import localized_datetime

if TYPE_CHECKING:
    from mitup_bot.models import Settings


def behavior_sections(settings: Settings) -> list[RichContent]:
    lang = settings.language
    return [
        settings_sections.toggle_section(
            ButtonMessages.WAITING_LIST,
            MeetingEditSettingsMessages.WAITING_LIST_EXPLANATION,
            lang,
            cb.SET_DEFAULT_WAITING_LIST,
            settings.default_waiting_list,
        ),
        settings_sections.toggle_section(
            ButtonMessages.PUBLIC,
            MeetingEditSettingsMessages.PUBLIC_EXPLANATION,
            lang,
            cb.SET_DEFAULT_PUBLIC,
            settings.default_public,
        ),
        settings_sections.toggle_section(
            ButtonMessages.OPEN_INVITATION,
            MeetingEditSettingsMessages.OPEN_INVITATIONS_EXPLANATION,
            lang,
            cb.SET_DEFAULT_INVITATIONS,
            settings.default_allow_invitation,
        ),
        settings_sections.toggle_section(
            ButtonMessages.INCOGNITO,
            MeetingEditSettingsMessages.INCOGNITO_EXPLANATION,
            lang,
            cb.SET_DEFAULT_INCOGNITO,
            settings.default_incognito,
        ),
        settings_sections.toggle_section(
            ButtonMessages.LOCK_ON_START,
            MeetingEditSettingsMessages.LOCK_ON_START_EXPLANATION,
            lang,
            cb.SET_DEFAULT_LOCK_ON_START,
            settings.default_lock_on_start,
        ),
    ]


def time_format_sections(settings: Settings) -> list[RichContent]:
    lang = settings.language

    def preview(date_format: DateFormat) -> str:
        return localized_datetime(
            settings_sections.SAMPLE_MOMENT,
            lang=lang,
            tz=settings.tz,
            created=None,
            time_format=replace(settings.default_time_format, date_format=date_format),
        )

    return [
        settings_sections.toggle_section(
            ButtonMessages.SHOW_TIMEZONE,
            MeetingEditSettingsMessages.SHOW_TIMEZONE_EXPLANATION,
            lang,
            cb.SET_DEFAULT_SHOW_TIMEZONE,
            settings.default_show_timezone,
        ),
        settings_sections.toggle_section(
            ButtonMessages.CLOCK_24H,
            MeetingEditSettingsMessages.CLOCK_24H_EXPLANATION,
            lang,
            cb.SET_DEFAULT_CLOCK_24H,
            settings.default_clock_24h,
        ),
        settings_sections.date_format_section(
            lang,
            current=settings.default_date_format,
            chip_callback=cb.SET_DEFAULT_DATE_FORMAT.with_id,
            explanation=MeetingEditSettingsMessages.DATE_FORMAT_EXPLANATION,
            preview=preview,
        ),
    ]


def default_meeting_settings_view(settings: Settings) -> MitupView:
    """What every meeting the user creates starts with, as one line per sub-card saying what it
    holds."""
    lang = settings.language
    heading = (
        ButtonMessages.DEFAULT_OPTIONS.rich(lang=lang)
        .wrap(RichTag.H2)
        .append(SettingsMessages.DEFAULT_OPTIONS_LEAD.rich(lang=lang))
    )
    screens = RichContent.join(
        "\n\n",
        [
            settings_sections.behavior_open_section(lang, cb.OPEN_DEFAULT_BEHAVIOR),
            settings_sections.time_format_open_section(lang, cb.OPEN_DEFAULT_TIME_FORMAT),
        ],
    )
    body = RichContent.join(horizontal_rule_content(), [heading, screens])
    return MitupView(body).with_back_button(ButtonMessages.SETTINGS, lang, cb.SETTINGS)


def default_behavior_view(settings: Settings) -> MitupView:
    lang = settings.language
    body = settings_sections.sub_card_body(
        MeetingEditSettingsMessages.BEHAVIOR_GROUP, Emojis.CONTROLS, lang, behavior_sections(settings)
    )
    return MitupView(body).with_back_button(ButtonMessages.DEFAULT_OPTIONS, lang, cb.EDIT_DEFAULT_OPTIONS)


def default_time_format_view(settings: Settings) -> MitupView:
    lang = settings.language
    body = settings_sections.sub_card_body(
        MeetingEditSettingsMessages.TIME_FORMAT_GROUP, Emojis.CLOCK, lang, time_format_sections(settings)
    )
    return MitupView(body).with_back_button(ButtonMessages.DEFAULT_OPTIONS, lang, cb.EDIT_DEFAULT_OPTIONS)
