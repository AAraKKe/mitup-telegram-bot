from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from mitup_bot.callback_data import CallbackData, DateCallbackData
from mitup_bot.translations import SUPPORTED_LANGUAGES
from mitup_bot.utils import (
    ButtonMessages,
    Emojis,
    Languages,
    MainMenuMessages,
    MeetingCreationMessages,
    MeetingEditDateTimeMessages,
    MeetingEditLanguageMessages,
    MeetingLifecycleMessages,
    SettingsMessages,
)
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import FormattedText
from mitup_bot.views import ButtonConfig, CalendarKeyboard, GridMitupView, Keyboard, MitupView

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


def main_menu_view(*, lang: str, message: str | FormattedText | None = None) -> MitupView:
    return MitupView(
        message or MainMenuMessages.DESCRIPTION.get(lang=lang),
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


def settings_view(*, lang: str, message: str | FormattedText | None = None) -> MitupView:
    return MitupView(
        message or SettingsMessages.DESCRIPTION.get(lang=lang),
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


def create_meeting_view(
    *, lang: str, message: str | FormattedText | None = None, datetime_link: FormattedText | None = None
) -> MitupView:
    if message is None:
        kwargs: dict[str, FormattedText] = {}
        if datetime_link is not None:
            kwargs["datetime_link"] = datetime_link
        message = MeetingCreationMessages.PROMPT.get(lang=lang, **kwargs)
    return MitupView(
        message,
        [
            [
                ButtonConfig(text=ButtonMessages.CANCEL.get(lang=lang), callback_data=cb.CANCEL_CREATE_MEETING),
            ],
        ],
    )


def request_information_with_cancel_view(
    *, lang: str, message: str | FormattedText, callback_data: cb.CallbackData
) -> MitupView:
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


def change_settings_element_view(
    *, lang: str, message: str | FormattedText, callback_data=cb.CANCEL_SETTINGS
) -> MitupView:
    """
    This view is used when in order to change a setting the user is asked for a message and we want to give
    them the option to Cancel the action and go back to settings.
    """
    return request_information_with_cancel_view(lang=lang, message=message, callback_data=callback_data)


def settings_set_language_view(*, lang: str, message: FormattedText | None = None) -> MitupView:
    language_text = LANGUAGE_BUTTONS[lang].get(lang=lang)
    message = message or SettingsMessages.LANGUAGE_PROMPT.get(lang=lang, language=language_text)
    return set_language_view(lang, message, cb.SET_LANGUAGE).with_back_button(
        ButtonMessages.SETTINGS, lang, cb.SETTINGS
    )


def meeting_set_language_view(*, meeting: Meetup) -> MitupView:
    language_text = LANGUAGE_BUTTONS[meeting.lang].get(lang=meeting.user_language)
    message = MeetingEditLanguageMessages.DESCRIPTION.get(lang=meeting.user_language, language=language_text)

    return set_language_view(
        meeting.user_language, message, cb.SET_MEETING_LANGUAGE.with_ids(meeting.db_id, 0)
    ).with_back_button(ButtonMessages.EDIT, meeting.user_language, cb.EDIT_MEETING.with_id(meeting.db_id))


def set_language_view(lang: str, message: str | FormattedText, callback_data: CallbackData) -> GridMitupView:
    n_columns = min(len(SUPPORTED_LANGUAGES), 3)
    buttons = [
        ButtonConfig(text=LANGUAGE_BUTTONS[lang_code].get(lang=lang), callback_data=callback_data.with_id(idx))
        for idx, lang_code in enumerate(SUPPORTED_LANGUAGES)
    ]

    return GridMitupView(
        description=message,
        buttons=buttons,
        column_size=n_columns,
    )


def edit_meeting_property_view(
    *,
    lang: str,
    message: str | FormattedText,
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


def edit_meeting_date_view(
    *,
    lang: str,
    meeting_id: int,
    anchor_date: dt.date,
    current_date: dt.date,
    new: bool,
    set_date_callback: DateCallbackData | None = None,
    nav_callback: DateCallbackData | None = None,
    back_callback: CallbackData | None = None,
    back_button_text: ButtonMessages | None = None,
) -> MitupView:
    """Build a calendar view for date selection, parameterized by callback data.

    When no callback overrides are provided, defaults to the start-datetime callbacks
    used by the edit_meeting_datetime conversation. ``back_button_text`` must name the
    screen ``back_callback`` actually resolves to — callers overriding ``back_callback``
    should also override ``back_button_text`` to match.
    """
    resolved_set = set_date_callback or cb.SET_MEETING_DATE
    resolved_nav = nav_callback or cb.EDIT_MEETING_DATE
    resolved_back = back_callback or cb.EDIT_MEETING
    resolved_back_text = back_button_text or ButtonMessages.DATE_TIME

    message = (
        MeetingEditDateTimeMessages.DATE_ADD_PROMPT.get(lang=lang)
        if new
        else MeetingEditDateTimeMessages.DATE_EDIT_PROMPT.get(lang=lang)
    )
    calendar_keyboard = CalendarKeyboard(
        anchor_date,
        current_date,
        resolved_set.with_id(meeting_id),
        resolved_nav.with_id(meeting_id),
    ).keyboard

    calendar_keyboard.append(
        [ButtonConfig(text=resolved_back_text.back(lang=lang), callback_data=resolved_back.with_id(meeting_id))]
    )

    return MitupView(description=message, keyboard=calendar_keyboard)


def options_button(callback_data: CallbackData, text: str | FormattedText, option: bool) -> ButtonConfig:
    boolean_emojin = Emojis.boolean(option)
    text_str = text.text if isinstance(text, FormattedText) else text
    return ButtonConfig(text=f"{boolean_emojin} {text_str}", callback_data=callback_data)


def user_button(user: User, callback_data: CallbackData) -> ButtonConfig:
    return ButtonConfig(text=user.inline_name, callback_data=callback_data)


def confirmation_view(
    *,
    lang: str,
    message: str | FormattedText,
    confirm_callback_data: CallbackData,
    decline_callback_data: CallbackData,
) -> MitupView:
    return MitupView(
        message,
        [
            [
                ButtonConfig(text=ButtonMessages.CONFIRM.get(lang=lang), callback_data=confirm_callback_data),
                ButtonConfig(text=ButtonMessages.DECLINE.get(lang=lang), callback_data=decline_callback_data),
            ],
        ],
    )


def conversation_interrupted_view(
    *, lang: str, message: str | FormattedText, cancel_callback: CallbackData
) -> MitupView:
    """Shown when a conversation is unexpectedly interrupted. Lets the user resume or cancel."""
    return MitupView(
        message,
        [[ButtonConfig(text=ButtonMessages.CANCEL.get(lang=lang), callback_data=cancel_callback)]],
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
        description=MeetingLifecycleMessages.PAST_DESCRIPTION.get(lang=lang),
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
