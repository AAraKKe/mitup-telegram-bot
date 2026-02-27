from __future__ import annotations

import datetime as dt
from math import ceil
from typing import TYPE_CHECKING

from mitup_bot.callback_data import CallbackData
from mitup_bot.translations import SUPPORTED_LANGUAGES
from mitup_bot.utils import ButtonMessages, Emojis, MeetingMessages, Messages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import Languages, SettingsMessages
from mitup_bot.views import ButtonConfig, CalendarKeyboard, Keyboard, MitupView, PaginatedMitupView

if TYPE_CHECKING:
    from mitup_bot.models import Meetup, User

# Representation from language code to button to be used when generating views
LANGUAGE_BUTTONS = {
    "es_ES": Languages.SPANISH,
    "gl_ES": Languages.GALICIAN,
    "en": Languages.ENGLISH,
    "de_DE": Languages.GERMAN,
    "pt_BR": Languages.PORTUGUESE,
    "it_IT": Languages.ITALIAN,
}


def main_menu_view(*, lang: str, message: str | None = None) -> MitupView:
    return MitupView(
        message or Messages.DEFAULT_MAIN_MENU_DESCRIPTION.get(lang=lang),
        keyboard=[
            [
                ButtonConfig(text=ButtonMessages.NEW_MEETING.get(lang=lang), callback_data=cb.CREATE_MEETING),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.ACTIVE_MEETINGS.get(lang=lang),
                    callback_data=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(1),
                ),
            ],
            [
                ButtonConfig(text=ButtonMessages.PAST_MEETINGS.get(lang=lang), callback_data=cb.PAST_MEETINGS),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.JOINED_MEETINGS.get(lang=lang),
                    callback_data=cb.SHOW_JOINED_MEETINGS_PAGE.with_id(1),
                ),
                ButtonConfig(text=ButtonMessages.SETTINGS.get(lang=lang), callback_data=cb.SETTINGS),
            ],
            [
                ButtonConfig(text=ButtonMessages.HELP.get(lang=lang), callback_data=cb.HELP),
                ButtonConfig(text=ButtonMessages.COLLABORATE.get(lang=lang), callback_data=cb.COLLABORATE),
            ],
        ],
    )


def settings_view(*, lang: str, message: str | None = None) -> MitupView:
    return MitupView(
        message or Messages.DEFAULT_SETTINGS_DESCRIPTION.get(lang=lang),
        [
            [
                ButtonConfig(text=ButtonMessages.LANGUAGE.get(lang=lang), callback_data=cb.EDIT_LANGUAGE),
                ButtonConfig(text=ButtonMessages.TIMEOUT.get(lang=lang), callback_data=cb.EDIT_TIMEOUT),
            ],
            [
                ButtonConfig(text=ButtonMessages.NOTIFICATIONS.get(lang=lang), callback_data=cb.EDIT_NOTIFICATIONS),
                ButtonConfig(text=ButtonMessages.TIMEZONE.get(lang=lang), callback_data=cb.EDIT_TIEMZONE),
            ],
            [
                ButtonConfig(text=ButtonMessages.DEFAULT_OPTIONS.get(lang=lang), callback_data=cb.EDIT_DEFAULT_OPTIONS),
                ButtonConfig(text=ButtonMessages.PRIVACY.get(lang=lang), callback_data=cb.EDIT_PRIVACY),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.MAIN_MENU.back(lang=lang),
                    callback_data=cb.MAIN_MENU,
                )
            ],
        ],
    )


def create_meeting_view(*, lang: str, message: str | None = None) -> MitupView:
    return MitupView(
        message or MeetingMessages.CREATE.get(lang=lang),
        [
            [
                ButtonConfig(text=ButtonMessages.CANCEL.get(lang=lang), callback_data=cb.CANCEL_CREATE_MEETING),
            ],
        ],
    )


def request_information_with_cancel_view(*, lang: str, message: str, callback_data: cb.CallbackData) -> MitupView:
    """
    Use this wen we want to ask the user for information and give them the option to cancel the action.

    The callback_data represents what the action taken when the user clicks on Cancel.
    """
    return MitupView(
        message,
        [
            [
                ButtonConfig(text=ButtonMessages.CANCEL.get(lang=lang), callback_data=callback_data),
            ],
        ],
    )


def change_settings_element_view(*, lang: str, message: str, callback_data=cb.CANCEL_SETTINGS) -> MitupView:
    """
    This view is used when in order to change a setting the user is asked for a message and we want to give
    them the option to Cancel the action and go back to settings.
    """
    return request_information_with_cancel_view(lang=lang, message=message, callback_data=callback_data)


def settings_set_language_view(*, lang: str, message: str | None = None) -> MitupView:
    message = message or SettingsMessages.SELECT_LANGUAGE.get(lang=lang, language=LANGUAGE_BUTTONS[lang].get(lang=lang))
    return set_language_view(lang, message, cb.SET_LANGUAGE).with_back_button(
        ButtonMessages.SETTINGS, lang, cb.SETTINGS
    )


def meeting_set_language_view(*, meeting: Meetup) -> MitupView:
    message = MeetingMessages.EDIT_MEETING_LANGUAGE.get(
        lang=meeting.user_language, language=LANGUAGE_BUTTONS[meeting.lang].get(lang=meeting.user_language)
    )

    return set_language_view(
        meeting.user_language, message, cb.SET_MEETING_LANGUAGE.with_ids(meeting.db_id, 0)
    ).with_back_button(ButtonMessages.EDIT, meeting.user_language, cb.EDIT_MEETING.with_id(meeting.db_id))


def set_language_view(lang: str, message: str, callback_data: CallbackData) -> PaginatedMitupView:
    n_languages = len(SUPPORTED_LANGUAGES)
    n_columns = min(n_languages, 3)
    buttons = [
        ButtonConfig(text=LANGUAGE_BUTTONS[lang_code].get(lang=lang), callback_data=callback_data.with_id(idx))
        for idx, lang_code in enumerate(SUPPORTED_LANGUAGES)
    ]

    return PaginatedMitupView(
        description=message,
        buttons=buttons,
        column_size=n_columns,
        row_size=ceil(n_languages / n_columns),
        page_number=1,
    )


def edit_meeting_property_view(
    *,
    lang: str,
    message: str,
    meeting_id: int,
    extra_buttons: list[list[ButtonConfig]] | None = None,
    back_button: ButtonConfig | None = None,
) -> MitupView:
    back_button = back_button or ButtonConfig(
        text=ButtonMessages.EDIT.back(lang=lang), callback_data=cb.EDIT_MEETING.with_id(meeting_id)
    )
    keyboard = [[back_button]]

    if extra_buttons:
        keyboard[:0] = extra_buttons

    return MitupView(message, keyboard=keyboard)


def __edit_meeting_date_final_row(*, lang: str, meeting_id: int, new: bool) -> list[list[ButtonConfig]]:
    final_rows = [
        [ButtonConfig(text=ButtonMessages.EDIT.back(lang=lang), callback_data=cb.EDIT_MEETING.with_id(meeting_id))]
    ]

    if not new:
        # Allow deleting the date if date is already set
        final_rows.append(
            [
                ButtonConfig(
                    text=ButtonMessages.DELETE_DATE.get(lang=lang),
                    callback_data=cb.DELETE_MEETING_DATE.with_id(meeting_id),
                )
            ],
        )

    return final_rows


def edit_meeting_date_view(
    *, lang: str, meeting_id: int, anchor_date: dt.date, current_date: dt.date, new: bool
) -> MitupView:
    message = MeetingMessages.ADD_DATE.get(lang=lang) if new else MeetingMessages.EDIT_DATE.get(lang=lang)
    calendar_keyboard = CalendarKeyboard(
        anchor_date,
        current_date,
        cb.SET_MEETING_DATE.with_id(meeting_id),
        cb.EDIT_MEETING_DATE.with_id(meeting_id),
    ).keyboard

    # Final row includes the back button and the delete date button when the date is set
    calendar_keyboard.extend(__edit_meeting_date_final_row(lang=lang, meeting_id=meeting_id, new=new))

    return MitupView(description=message, keyboard=calendar_keyboard)


def options_button(callback_data: CallbackData, text: str, option: bool) -> ButtonConfig:
    boolean_emojin = Emojis.boolean(option)
    text = f"{boolean_emojin} {text}"

    return ButtonConfig(text=text, callback_data=callback_data)


def user_button(user: User, callback_data: CallbackData) -> ButtonConfig:
    return ButtonConfig(text=user.inline_name, callback_data=callback_data)


def confirmation_view(
    *,
    lang: str,
    message: str,
    confirm_callback_data: CallbackData,
    decline_callback_data: CallbackData,
) -> MitupView:
    return MitupView(
        message,
        [
            [ButtonConfig(text=ButtonMessages.CONFIRM.get(lang=lang), callback_data=confirm_callback_data)],
            [ButtonConfig(text=ButtonMessages.DECLINE.get(lang=lang), callback_data=decline_callback_data)],
        ],
    )


def reactivation_prompt_view(*, lang: str, meeting_id: int, back_rows: Keyboard | None = None) -> MitupView:
    """
    View shown to the owner when they try to access a meeting that is no longer active.

    If `back_rows` is provided it is used as the back-navigation row(s) below the action buttons.
    Otherwise a single "Back to main menu" row is rendered.
    """
    resolved_back_rows: Keyboard = back_rows or [
        [ButtonConfig(text=ButtonMessages.MAIN_MENU.back(lang=lang), callback_data=cb.MAIN_MENU)]
    ]
    return MitupView(
        description=MeetingMessages.PAST_MEETING_DESCRIPTION.get(lang=lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.REACTIVATE_MEETING.get(lang=lang),
                    callback_data=cb.REACTIVATE_MEETING.with_id(meeting_id),
                ),
                ButtonConfig(
                    text=ButtonMessages.DELETE.get(lang=lang),
                    callback_data=cb.DELETE_MEETING.with_id(meeting_id),
                ),
            ],
            *resolved_back_rows,
        ],
    )
