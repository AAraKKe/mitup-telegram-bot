from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from mitup_bot.datetimes import as_utc
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.utils import ButtonMessages, NotificationMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.rich_message import RichContent, RichTag, horizontal_rule_content
from mitup_bot.views.datetime_format import relative_time_content
from mitup_bot.views.meeting.shared_card import when_section, where_section
from mitup_bot.views.meeting_text import title_content
from mitup_bot.views.mitup_view import MitupView

if TYPE_CHECKING:
    from mitup_bot.models import JoinedUsers
    from mitup_bot.utils.messages import MessageBase

JUST_NOW = dt.timedelta(minutes=1)


def countdown_line(link: JoinedUsers, message: MessageBase, *, now: dt.datetime) -> RichContent:
    """How far the meeting's start sits from *now*, empty when it carries no start time."""
    start = link.meetup.datetime
    if start is None:
        return RichContent()
    lang = link.user.lang
    return message.rich(lang=lang, when=relative_time_content(start, now=now, lang=lang))


def notification_card(
    link: JoinedUsers, heading: MessageBase, countdown: RichContent, closing_line: RichContent | None = None
) -> MitupView:
    """Reads nothing beyond what the notification queries load: the meeting's columns and its
    owner's settings."""
    meeting = link.meetup
    lang = link.user.lang
    opening = heading.rich(lang=lang).wrap(RichTag.H2).append(title_content(meeting))
    fields = [section for section in (when_section(meeting, with_status=False), where_section(meeting)) if section]
    blocks = [
        RichContent.join("\n", [part for part in (opening, countdown) if part]),
        RichContent.join("\n\n", fields),
        closing_line if closing_line is not None else RichContent(),
    ]
    body = RichContent.join(horizontal_rule_content(), [block for block in blocks if block])
    open_meeting = ButtonConfig(
        text=ButtonMessages.OPEN_MEETING.text(lang=lang),
        callback_data=cb.SHOW_MEETING.with_id(meeting.db_id),
        style="primary",
    )
    return MitupView(body, [[open_meeting]]).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)


def starting_soon_view(link: JoinedUsers, *, now: dt.datetime) -> MitupView:
    lang = link.user.lang
    leave = ButtonConfig(
        text=ButtonMessages.LEAVE.text(lang=lang),
        callback_data=cb.LEAVE.with_id(link.meetup.db_id),
        style="danger",
    )
    return notification_card(
        link,
        NotificationMessages.STARTING_SOON_HEADING,
        countdown_line(link, NotificationMessages.STARTS_IN, now=now),
        NotificationMessages.CANNOT_MAKE_IT.rich(lang=lang, button_leave=leave),
    )


def started_view(link: JoinedUsers, *, now: dt.datetime) -> MitupView:
    """A start within the last minute is said to be just now: counting in minutes would round those
    seconds up and claim the meeting began a minute ago."""
    start = link.meetup.datetime
    if start is not None and abs(as_utc(start) - as_utc(now)) < JUST_NOW:
        countdown = NotificationMessages.STARTED_JUST_NOW.rich(lang=link.user.lang)
    else:
        countdown = countdown_line(link, NotificationMessages.STARTED_AGO, now=now)
    return notification_card(link, NotificationMessages.STARTED_HEADING, countdown)
