from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from mitup_bot.datetimes import as_utc
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.lifecycle import LifecyclePolicy
from mitup_bot.utils import ButtonMessages, MeetingDisplayMessages, NotificationMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.rich_message import RichContent, RichTag, horizontal_rule_content, unordered_list_content
from mitup_bot.views.datetime_format import datetime_content, relative_time_content
from mitup_bot.views.meeting.shared_card import when_section, where_section
from mitup_bot.views.meeting_text import title_content
from mitup_bot.views.mitup_view import MitupView

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mitup_bot.models import JoinedUsers, Meetup, User
    from mitup_bot.utils.messages import MessageBase

JUST_NOW = dt.timedelta(minutes=1)

# A deletion digest names this many meetings and counts the rest in its closing line.
DIGEST_MEETINGS_NAMED = 5


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


def digest_meeting_line(meetup: Meetup, owner: User) -> RichContent:
    """One meeting of a deletion digest: its title, and when it was created in the owner's formats."""
    lang = owner.lang
    title = RichContent(meetup.plain_title.strip()) or MeetingDisplayMessages.UNTITLED.rich(lang=lang)
    if meetup.created_time is None:
        return title.wrap(RichTag.BOLD)
    created = datetime_content(
        meetup.created_time,
        lang=lang,
        tz=owner.settings.tz,
        created=dt.datetime.now(dt.UTC),
        time_format=owner.settings.default_time_format,
    )
    return NotificationMessages.DELETION_MEETING_LINE.rich(lang=lang, meeting_title=title, created=created)


def digest_list(meetups: Sequence[Meetup], owner: User) -> RichContent:
    """The first `DIGEST_MEETINGS_NAMED` meetings as a list, closed by a count of the rest."""
    named = unordered_list_content(digest_meeting_line(meetup, owner) for meetup in meetups[:DIGEST_MEETINGS_NAMED])
    unnamed = len(meetups) - DIGEST_MEETINGS_NAMED
    if unnamed < 1:
        return named
    return named.append(NotificationMessages.DELETION_MORE_MEETINGS.rich(lang=owner.lang, count=unnamed))


def deletion_warning_view(meetups: Sequence[Meetup]) -> MitupView:
    """Warn the owner of *meetups*, all of which are theirs, about every one of them at once."""
    owner = meetups[0].owner
    lang = owner.lang
    policy = LifecyclePolicy.get(owner.supporter_level)
    deadline = NotificationMessages.DELETION_WARNING_DEADLINE.rich(
        lang=lang, days_until_deletion=LifecyclePolicy.interval_days(policy.deletion_warning_lead)
    )
    past_meetings = ButtonConfig(text=ButtonMessages.PAST_MEETINGS.text(lang=lang), callback_data=cb.PAST_MEETINGS)
    closing = RichContent.join(
        "\n",
        [
            NotificationMessages.DELETION_WARNING_REACTIVATE.rich(lang=lang, button_past_meetings=past_meetings),
            NotificationMessages.DELETION_WARNING_IGNORE.rich(lang=lang),
        ],
    )
    body = RichContent.join(
        horizontal_rule_content(),
        [
            NotificationMessages.DELETION_WARNING_HEADING.rich(lang=lang).wrap(RichTag.H2).append(deadline),
            digest_list(meetups, owner),
            closing,
        ],
    )
    return MitupView(body).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)


def deletion_notice_view(meetups: Sequence[Meetup]) -> MitupView:
    """Tell the owner of *meetups*, all of which were theirs, that every one of them is gone."""
    owner = meetups[0].owner
    lang = owner.lang
    body = RichContent.join(
        horizontal_rule_content(),
        [
            NotificationMessages.DELETION_NOTICE_HEADING.rich(lang=lang)
            .wrap(RichTag.H2)
            .append(NotificationMessages.DELETION_NOTICE_BODY.rich(lang=lang)),
            digest_list(meetups, owner),
        ],
    )
    return MitupView(body).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)
