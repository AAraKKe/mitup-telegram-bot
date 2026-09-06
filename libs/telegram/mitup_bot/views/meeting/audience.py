from __future__ import annotations

from typing import TYPE_CHECKING

from mitup_bot.views.meeting.owner_card import owner_view
from mitup_bot.views.meeting.shared_card import external_view, inline_view

if TYPE_CHECKING:
    from telegram import Update

    from mitup_bot.keyboards import ButtonConfig, Keyboard
    from mitup_bot.models import Meetup, User
    from mitup_bot.views.mitup_view import MitupView


def view_for(meeting: Meetup, user: User, back_button: ButtonConfig | None = None) -> MitupView:
    """Get the appropriate view for the given user depending on whether they own the meeting or not.

    `back_button` is forwarded to whichever view is rendered.
    """
    return owner_view(meeting, back_button) if meeting.is_owned_by(user) else external_view(meeting, back_button)


def keyboard_for_update(update: Update, meeting: Meetup, user: User | None) -> Keyboard:
    """Choose the keyboard to store for the message this update points at.

    Shared (inline) messages get the inline keyboard, rebuilt with the chat_instance once it is
    known. Bot-chat messages only show the edit controls when the user owns the meeting; a caller
    with no account owns nothing, so they get the participant's keyboard.
    """
    if update.callback_query and update.callback_query.inline_message_id:
        return inline_view(meeting, chat_instance=update.callback_query.chat_instance).menu
    if update.effective_message:
        if user is not None and user.own_meeting(meeting.db_id):
            return owner_view(meeting).menu
        return external_view(meeting).menu
    return inline_view(meeting).menu
