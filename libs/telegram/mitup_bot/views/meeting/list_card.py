from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from mitup_bot.callback_data import CallbackData
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.utils import ButtonMessages, Emojis, MeetingDisplayMessages, MeetingListMessages
from mitup_bot.utils.rich_message import RichContent, RichTag, keyboard_content
from mitup_bot.utils.rich_template import render_rich
from mitup_bot.views.datetime_format import duration_days_content, localized_date
from mitup_bot.views.meeting.sections import participants_count_line
from mitup_bot.views.meeting.shared_card import when_lines
from mitup_bot.views.meeting_text import title_content

if TYPE_CHECKING:
    from mitup_bot.models import Meetup


def list_heading(title: ButtonMessages, lang: str) -> RichContent:
    return title.rich(lang=lang).wrap(RichTag.H2)


def deletion_line(meeting: Meetup, lang: str) -> RichContent:
    """When this meeting will be deleted, counted in days once its owner has been warned.

    Empty when no stamp dates the meeting.
    """
    due = meeting.deletion_due_time
    if due is None:
        return RichContent()
    now = dt.datetime.now(dt.UTC)
    if meeting.expiration_notification_sent:
        days_left = max((due - now).days, 1)
        return MeetingListMessages.DELETION_COUNTDOWN.rich(
            lang=lang, duration=duration_days_content(days_left, lang=lang)
        )
    date = localized_date(due, lang=lang, tz=meeting.timezone, created=now, date_format=meeting.time_format.date_format)
    return MeetingListMessages.DELETION_DATE.rich(lang=lang, date=date)


def meeting_list_section(
    meeting: Meetup,
    open_callback: CallbackData,
    lang: str,
    delete_callback: CallbackData | None = None,
    *,
    with_deletion_notice: bool = False,
) -> RichContent:
    """One meeting of a list: its title and key lines, then a button row to open it and, on the
    lists of meetings the user owns, to delete it right away."""
    title = title_content(meeting) if meeting.plain_title.strip() else MeetingDisplayMessages.UNTITLED.rich(lang=lang)
    lines = [title.wrap(RichTag.BOLD)]
    if with_deletion_notice and (deletion := deletion_line(meeting, lang)):
        lines.append(deletion.wrap(RichTag.ITALIC))
    if meeting.datetime is not None:
        when = when_lines(meeting, meeting.datetime, with_status=False)
        lines.append(render_rich(t"{Emojis.CLOCK} {when}"))
    if location_name := meeting.location.coerced_name:
        lines.append(render_rich(t"{Emojis.MAP} {location_name}"))
    count = participants_count_line(meeting)
    lines.append(render_rich(t"{Emojis.JOINED} {count}"))
    buttons = [ButtonConfig(text=ButtonMessages.OPEN.text(lang=lang), callback_data=open_callback, style="primary")]
    if delete_callback is not None:
        buttons.append(
            ButtonConfig(text=ButtonMessages.DELETE.text(lang=lang), callback_data=delete_callback, style="danger")
        )
    return RichContent.join("\n", lines).append(keyboard_content([buttons]))
