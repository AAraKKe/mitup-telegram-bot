from __future__ import annotations

from typing import TYPE_CHECKING

from mitup_bot.keyboards import ButtonConfig, ButtonRow, ButtonStyle
from mitup_bot.utils import ButtonMessages
from mitup_bot.utils import callbacks as cb

if TYPE_CHECKING:
    from mitup_bot.callback_data import CallbackData
    from mitup_bot.models import Meetup


def meeting_chip(
    label: ButtonMessages, meeting: Meetup, callback: CallbackData, style: ButtonStyle | None = None
) -> ButtonConfig:
    """One inline chip on the meeting card: a catalog label wired to a callback carrying the
    meeting's id."""
    return ButtonConfig(
        text=label.text(lang=meeting.user_language),
        callback_data=callback.with_id(meeting.db_id),
        style=style,
    )


def main_menu_back_button(meeting: Meetup) -> ButtonConfig:
    return ButtonConfig(
        text=ButtonMessages.MAIN_MENU.back(lang=meeting.user_language),
        callback_data=cb.MAIN_MENU,
    )


def join_chip(meeting: Meetup, lang: str, *, inert_when_full: bool) -> ButtonConfig:
    """The way into the meeting, or the chip naming why there is none.

    Only a surface whose buttons render as rich markup may ask for the inert form: a classic
    keyboard cannot express a disabled button, and its serializer refuses one outright, so a
    surface serialized that way keeps the tappable Join answering with the full-meeting alert.
    """
    if inert_when_full and not meeting.join_allowed():
        return ButtonConfig(text=ButtonMessages.FULL.text(lang=lang), disabled=True)
    return ButtonConfig(
        text=ButtonMessages.JOIN.text(lang=lang), callback_data=cb.JOIN.with_id(meeting.db_id), style="success"
    )


def join_leave_row(meeting: Meetup, lang: str, *, inert_when_full: bool = False) -> ButtonRow:
    """Return the [JOIN, (INVITE,) LEAVE] row, inserting INVITE only when allow_invitation is True.

    The three are the same layer of interaction, so they share a row; the accents mark which way
    each one moves the reader, and the labels carry their emoji so the meaning survives a context
    that repaints the colours (an outgoing bubble on a card the reader shared themselves).
    """
    join = join_chip(meeting, lang, inert_when_full=inert_when_full)
    invite = ButtonConfig(text=ButtonMessages.INVITE.text(lang=lang), callback_data=cb.INVITE.with_id(meeting.db_id))
    leave = ButtonConfig(
        text=ButtonMessages.LEAVE.text(lang=lang), callback_data=cb.LEAVE.with_id(meeting.db_id), style="danger"
    )
    return [join, invite, leave] if meeting.allow_invitation else [join, leave]
