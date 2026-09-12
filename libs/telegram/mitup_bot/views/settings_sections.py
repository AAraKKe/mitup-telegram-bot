"""The section shapes shared by the meeting settings screens and the default options screens."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from mitup_bot.datetimes import DateFormat
from mitup_bot.emojis import Emojis
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.utils import ButtonMessages, MeetingEditSettingsMessages, SettingsMessages
from mitup_bot.utils.messages import MessageBase
from mitup_bot.utils.rich_message import RichContent, RichTag, horizontal_rule_content, keyboard_content
from mitup_bot.utils.rich_template import render_rich
from mitup_bot.views.factory import settings_section, toggle_chip
from mitup_bot.views.sections import chip_section

if TYPE_CHECKING:
    from mitup_bot.callback_data import CallbackData

# The moment the date-format previews are written with when there is no meeting start to show.
SAMPLE_MOMENT = dt.datetime(2026, 9, 18, 20, 45, tzinfo=dt.UTC)

DATE_FORMAT_LABELS = {
    DateFormat.DEFAULT: ButtonMessages.DATE_FORMAT_DEFAULT,
    DateFormat.LONG: ButtonMessages.DATE_FORMAT_LONG,
    DateFormat.FULL: ButtonMessages.DATE_FORMAT_FULL,
}


def sub_card_body(title: MessageBase, glyph: Emojis, lang: str, sections: Sequence[RichContent]) -> RichContent:
    """The shape every settings sub-card takes: its own title, the tap hint, then its settings."""
    heading = (
        render_rich(t"{glyph} {title.rich(lang=lang)}")
        .wrap(RichTag.H2)
        .append(SettingsMessages.TOGGLE_HINT.rich(lang=lang))
    )
    return RichContent.join(horizontal_rule_content(), [heading, RichContent.join("\n\n", sections)])


def open_chip(lang: str, callback_data: CallbackData) -> ButtonConfig:
    return ButtonConfig(text=ButtonMessages.OPEN.text(lang=lang), callback_data=callback_data)


def behavior_open_section(lang: str, callback_data: CallbackData) -> RichContent:
    return chip_section(
        MeetingEditSettingsMessages.BEHAVIOR_GROUP,
        Emojis.CONTROLS,
        lang,
        open_chip(lang, callback_data),
        MeetingEditSettingsMessages.BEHAVIOR_LINE.rich(lang=lang),
    )


def time_format_open_section(lang: str, callback_data: CallbackData) -> RichContent:
    return chip_section(
        MeetingEditSettingsMessages.TIME_FORMAT_GROUP,
        Emojis.CLOCK,
        lang,
        open_chip(lang, callback_data),
        MeetingEditSettingsMessages.TIME_FORMAT_LINE.rich(lang=lang),
    )


def toggle_section(
    name: ButtonMessages, explanation: MessageBase, lang: str, callback_data: CallbackData, value: bool
) -> RichContent:
    """One boolean setting: its name with the state chip beside it, and below them the paragraph
    saying what the setting does."""
    return settings_section(name, lang, [toggle_chip(callback_data, value, lang)], explanation.rich(lang=lang))


def date_format_section(
    lang: str,
    *,
    current: DateFormat,
    chip_callback: Callable[[int], CallbackData],
    explanation: MessageBase,
    preview: Callable[[DateFormat], str],
) -> RichContent:
    """Every format previewed on the same moment, then the row picking one, the format in force
    accented. *chip_callback* takes the format's position in `DateFormat`, which the callback carries.
    """
    previews = []
    buttons = []
    for index, date_format in enumerate(DateFormat):
        label = DATE_FORMAT_LABELS[date_format].rich(lang=lang).wrap(RichTag.BOLD)
        written = RichContent(preview(date_format))
        previews.append(render_rich(t"{label} · {written}"))
        buttons.append(
            ButtonConfig(
                text=DATE_FORMAT_LABELS[date_format].text(lang=lang),
                callback_data=chip_callback(index),
                style="primary" if date_format is current else None,
            )
        )
    name = ButtonMessages.DATE_FORMAT.rich(lang=lang).wrap(RichTag.BOLD)
    return (
        name.append("\n")
        .append(explanation.rich(lang=lang))
        .append("\n\n")
        .append(RichContent.join("\n", previews))
        .append(keyboard_content([buttons]))
    )
