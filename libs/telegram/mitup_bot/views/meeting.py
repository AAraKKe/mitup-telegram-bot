from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, cast

from mitup_bot.keyboards import ButtonConfig, Keyboard
from mitup_bot.utils import (
    ButtonMessages,
    MeetingAttachMessages,
    MeetingDisplayMessages,
    MeetingEditSettingsMessages,
    MeetingEditWhenMessages,
)
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import EntityDateTime, FormattedText, render
from mitup_bot.views import MitupInlineView, MitupView
from mitup_bot.views.factory import options_button
from mitup_bot.views.meeting_text import inline_message, inline_query_message, maps_url, meeting_message

if TYPE_CHECKING:
    from telegram import Update

    from mitup_bot.models import Meetup, User


def join_leave_row(meeting: Meetup, lang: str) -> list[ButtonConfig]:
    """Return the [JOIN, (INVITE,) LEAVE] row, inserting INVITE only when allow_invitation is True."""
    join = ButtonConfig(text=ButtonMessages.JOIN.get_text(lang=lang), callback_data=cb.JOIN.with_id(meeting.db_id))
    invite = ButtonConfig(
        text=ButtonMessages.INVITE.get_text(lang=lang), callback_data=cb.INVITE.with_id(meeting.db_id)
    )
    leave = ButtonConfig(text=ButtonMessages.LEAVE.get_text(lang=lang), callback_data=cb.LEAVE.with_id(meeting.db_id))
    return [join, invite, leave] if meeting.allow_invitation else [join, leave]


def maps_row(meeting: Meetup, lang: str) -> Keyboard:
    """Single-button row linking the location to Google Maps, or no rows when the location is unset."""
    url = maps_url(meeting.location)
    if url is None:
        return []
    return [[ButtonConfig(text=ButtonMessages.OPEN_IN_MAPS.get_text(lang=lang), url=url)]]


def main_menu_back_button(meeting: Meetup) -> ButtonConfig:
    return ButtonConfig(
        text=ButtonMessages.MAIN_MENU.back(lang=meeting.user_language),
        callback_data=cb.MAIN_MENU,
    )


def main_view(meeting: Meetup, back_button: ButtonConfig | None = None) -> MitupView:
    """Owner-facing detail view.

    `back_button` lets the caller point the trailing back button at the list the user came
    from; when omitted it defaults to the main menu.
    """
    keyboard: Keyboard = []
    if not (meeting.lock_on_start and meeting.is_in_progress):
        keyboard.append(join_leave_row(meeting, meeting.user_language))
    keyboard.extend(maps_row(meeting, meeting.user_language))
    keyboard.extend(
        [
            [
                ButtonConfig(
                    text=ButtonMessages.EDIT.get_text(lang=meeting.user_language),
                    callback_data=cb.EDIT_MEETING.with_id(meeting.db_id),
                ),
                ButtonConfig(
                    text=ButtonMessages.DELETE.get_text(lang=meeting.user_language),
                    callback_data=cb.DELETE_MEETING.with_id(meeting.db_id),
                ),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.SHARE.get_text(lang=meeting.user_language),
                    switch_inline_query=str(meeting.db_id),
                ),
            ],
            [back_button or main_menu_back_button(meeting)],
        ]
    )
    view = MitupView(meeting_message(meeting), keyboard)
    if meeting.is_in_progress:
        view.with_context(MeetingDisplayMessages.IN_PROGRESS_BANNER.get(lang=meeting.user_language))
    return view


def external_view(meeting: Meetup, back_button: ButtonConfig | None = None) -> MitupView:
    """This is the view shown to users that do not own the meeting when checking through meetings I have joined.

    `back_button` lets the caller point the trailing back button at the list the user came
    from; when omitted it defaults to the main menu.
    """
    keyboard: Keyboard = []
    if not (meeting.lock_on_start and meeting.is_in_progress):
        keyboard.append(join_leave_row(meeting, meeting.user_language))
    keyboard.extend(maps_row(meeting, meeting.user_language))
    keyboard.append([back_button or main_menu_back_button(meeting)])
    view = MitupView(inline_message(meeting), keyboard)
    if meeting.is_in_progress:
        view.with_context(MeetingDisplayMessages.IN_PROGRESS_BANNER.get(lang=meeting.user_language))
    return view


def view_for(meeting: Meetup, user: User, back_button: ButtonConfig | None = None) -> MitupView:
    """Get the appropriate view for the given user depending on whether they own the meeting or not.

    `back_button` is forwarded to whichever view is rendered.
    """
    return main_view(meeting, back_button) if meeting.is_owned_by(user) else external_view(meeting, back_button)


def edit_view(meeting: Meetup) -> MitupView:
    keyboard: Keyboard = [
        [
            ButtonConfig(
                text=ButtonMessages.TITLE.get_text(lang=meeting.user_language),
                callback_data=cb.EDIT_MEETING_TITLE.with_id(meeting.db_id),
            ),
            ButtonConfig(
                text=ButtonMessages.DESCRIPTION.get_text(lang=meeting.user_language),
                callback_data=cb.EDIT_MEETING_DESCRIPTION.with_id(meeting.db_id),
            ),
        ],
        [
            ButtonConfig(
                text=ButtonMessages.WHEN.get_text(lang=meeting.user_language),
                callback_data=cb.EDIT_MEETING_WHEN.with_id(meeting.db_id),
            ),
        ],
    ]
    keyboard.extend(
        [
            [
                ButtonConfig(
                    text=ButtonMessages.PARTICIPANTS.get_text(lang=meeting.user_language),
                    callback_data=cb.EDIT_MEETING_PARTICIPANTS.with_id(meeting.db_id),
                ),
                ButtonConfig(
                    text=ButtonMessages.LOCATION.get_text(lang=meeting.user_language),
                    callback_data=cb.EDIT_MEETING_LOCATION.with_id(meeting.db_id),
                ),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.LANGUAGE.get_text(lang=meeting.user_language),
                    callback_data=cb.EDIT_MEETING_LANGUAGE.with_id(meeting.db_id),
                ),
                ButtonConfig(
                    text=ButtonMessages.SETTINGS.get_text(lang=meeting.user_language),
                    callback_data=cb.EDIT_MEETING_SETTINGS.with_id(meeting.db_id),
                ),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.DONE.get_text(lang=meeting.user_language),
                    callback_data=cb.SHOW_MEETING.with_id(meeting.db_id),
                ),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.MAIN_MENU.back(lang=meeting.user_language),
                    callback_data=cb.MAIN_MENU,
                ),
            ],
        ]
    )
    return MitupView(meeting_message(meeting), keyboard)


def settings_view(meeting: Meetup) -> MitupView:
    keyboard = [
        [
            options_button(
                cb.SET_MEETING_WAITING_LIST.with_id(meeting.db_id),
                ButtonMessages.WAITING_LIST.get(lang=meeting.owner.lang),
                meeting.waiting_list,
            ),
            options_button(
                cb.SET_MEETING_PUBLIC.with_id(meeting.db_id),
                ButtonMessages.PUBLIC.get(lang=meeting.owner.lang),
                meeting.public,
            ),
        ],
        [
            options_button(
                cb.SET_MEETING_ALLOW_INVITATIONS.with_id(meeting.db_id),
                ButtonMessages.OPEN_INVITATION.get(lang=meeting.owner.lang),
                meeting.allow_invitation,
            ),
            options_button(
                cb.SET_MEETING_INCOGNITO.with_id(meeting.db_id),
                ButtonMessages.INCOGNITO.get(lang=meeting.owner.lang),
                meeting.incognito,
            ),
        ],
    ]

    return MitupView(
        MeetingEditSettingsMessages.DESCRIPTION.get(lang=meeting.owner.lang),
        keyboard=keyboard,
    ).with_back_button(ButtonMessages.EDIT, meeting.owner.lang, cb.EDIT_MEETING.with_id(meeting.db_id))


def when_view_with_start(meeting: Meetup) -> tuple[str | FormattedText, Keyboard]:
    start_entity = EntityDateTime(
        MeetingDisplayMessages.DATETIME_ENTITY_LABEL.get_text(), cast(dt.datetime, meeting.datetime), "DT"
    )
    set_start_button = ButtonConfig(
        text=ButtonMessages.SET_START_TIME.get_text(lang=meeting.user_language),
        callback_data=cb.SET_MEETING_START_TIME.with_id(meeting.db_id),
    )
    set_end_button = ButtonConfig(
        text=ButtonMessages.SET_END_TIME.get_text(lang=meeting.user_language),
        callback_data=cb.SET_MEETING_END_TIME.with_id(meeting.db_id),
    )
    lock_toggle = options_button(
        cb.SET_MEETING_LOCK_ON_START.with_id(meeting.db_id),
        ButtonMessages.LOCK_ON_START.get(lang=meeting.user_language),
        meeting.lock_on_start,
    )
    clear_button = ButtonConfig(
        text=ButtonMessages.CLEAR_TIMES.get_text(lang=meeting.user_language),
        callback_data=cb.DELETE_MEETING_TIMES.with_id(meeting.db_id),
    )

    if meeting.end_datetime is not None:
        end_entity = EntityDateTime(MeetingDisplayMessages.DATETIME_ENTITY_LABEL.get_text(), meeting.end_datetime, "DT")
        description: str | FormattedText = MeetingEditWhenMessages.DESCRIPTION_BOTH.get(
            lang=meeting.user_language,
            start_datetime=render(t"{start_entity}"),
            end_datetime=render(t"{end_entity}"),
        )
    else:
        description = MeetingEditWhenMessages.DESCRIPTION_START_ONLY.get(
            lang=meeting.user_language,
            start_datetime=render(t"{start_entity}"),
        )

    keyboard: Keyboard = [
        [set_start_button, set_end_button],
        [lock_toggle],
        [clear_button],
    ]
    return description, keyboard


def when_view(meeting: Meetup) -> MitupView:
    if meeting.datetime is None:
        # Lock-on-start is only offered once a start time exists: without a start there is no
        # window to freeze on, so the toggle would be a no-op. Setting a start time reveals it.
        description: str | FormattedText = MeetingEditWhenMessages.DESCRIPTION_NO_TIMES.get(lang=meeting.user_language)
        keyboard: Keyboard = [
            [
                ButtonConfig(
                    text=ButtonMessages.SET_START_TIME.get_text(lang=meeting.user_language),
                    callback_data=cb.SET_MEETING_START_TIME.with_id(meeting.db_id),
                ),
            ],
        ]
    else:
        description, keyboard = when_view_with_start(meeting)

    return MitupView(description, keyboard).with_back_button(
        ButtonMessages.EDIT, meeting.user_language, cb.EDIT_MEETING.with_id(meeting.db_id)
    )


def inline_view(meeting: Meetup, *, chat_instance: str | None = None) -> MitupInlineView:
    is_searchable = chat_instance is not None
    footnote = (
        MeetingAttachMessages.FOOTNOTE_ACTIVE.get(lang=meeting.lang)
        if is_searchable
        else MeetingAttachMessages.FOOTNOTE_INACTIVE.get(lang=meeting.lang)
    )
    view = MitupInlineView(
        description=inline_message(meeting),
        keyboard=build_inline_keyboard(
            meeting,
            is_searchable=is_searchable,
            is_locked_and_in_progress=meeting.lock_on_start and meeting.is_in_progress,
        ),
        id=str(meeting.db_id),
        title=meeting.plain_title,
        inline_description=inline_query_message(meeting),
    )
    if meeting.is_in_progress:
        view.with_context(MeetingDisplayMessages.IN_PROGRESS_BANNER.get(lang=meeting.lang))
    return view.with_footnote(footnote)


def build_inline_keyboard(
    meeting: Meetup, *, is_searchable: bool = False, is_locked_and_in_progress: bool = False
) -> Keyboard:
    keyboard: Keyboard = []

    if not is_locked_and_in_progress:
        keyboard.append(join_leave_row(meeting, meeting.lang))

    keyboard.extend(maps_row(meeting, meeting.lang))

    if meeting.public:
        keyboard.append(
            [
                ButtonConfig(
                    text=ButtonMessages.SHARE.get_text(lang=meeting.lang), switch_inline_query=str(meeting.db_id)
                ),
            ]
        )

    if not is_searchable:
        keyboard.append(
            [
                ButtonConfig(
                    text=ButtonMessages.MAKE_SEARCHABLE.get_text(lang=meeting.lang),
                    callback_data=cb.ATTACH_TO_CHAT.with_id(meeting.db_id),
                ),
            ]
        )

    return keyboard


def keyboard_for_update(update: Update, meeting: Meetup, user: User) -> Keyboard:
    """Choose the keyboard to store for the message this update points at.

    Shared (inline) messages get the inline keyboard, rebuilt with the chat_instance once it is
    known. Bot-chat messages only show the edit controls when the user owns the meeting.
    """
    if update.callback_query and update.callback_query.inline_message_id:
        return inline_view(meeting, chat_instance=update.callback_query.chat_instance).keyboard
    if update.effective_message:
        if user.own_meeting(meeting.db_id):
            return main_view(meeting).keyboard
        return external_view(meeting).keyboard
    return inline_view(meeting).keyboard
