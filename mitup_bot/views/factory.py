import datetime as dt
from math import ceil

from mitup_bot.callback_data import CallbackData
from mitup_bot.translations import SUPPORTED_LANGUAGES
from mitup_bot.utils import ButtonMessages, Emojis, MeetingMessages, Messages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import Languages, SettingsMessages
from mitup_bot.views import ButtonConfig, CalendarKeyboard, MitupView, PaginatedMitupView

# Representation from language code to button to be used when generating views
LANGUAGE_BUTTONS = {
    "es_ES": Languages.SPANISH,
    "gl_ES": Languages.GALICIAN,
    "en": Languages.ENGLISH,
    "de_DE": Languages.GERMAN,
    "pt_BR": Languages.PORTUGUESE,
    "it_IT": Languages.ITALIAN,
}


def main_menu_view(lang: str, message: str | None = None) -> MitupView:
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
                ButtonConfig(text=ButtonMessages.JOINED_MEETINGS.get(lang=lang), callback_data=cb.JOINED_MEETINGS),
                ButtonConfig(text=ButtonMessages.SETTINGS.get(lang=lang), callback_data=cb.SETTINGS),
            ],
            [
                ButtonConfig(text=ButtonMessages.HELP.get(lang=lang), callback_data=cb.HELP),
                ButtonConfig(text=ButtonMessages.COLLABORATE.get(lang=lang), callback_data=cb.COLLABORATE),
            ],
        ],
    )


def settings_view(lang: str, message: str | None = None) -> MitupView:
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
                    text=f"{ButtonMessages.GO_BACK}{ButtonMessages.MAIN_MENU.get(lang=lang)}",
                    callback_data=cb.MAIN_MENU,
                )
            ],
        ],
    )


def create_meeting_view(lang: str, message: str | None = None) -> MitupView:
    return MitupView(
        message or MeetingMessages.CREATE.get(lang=lang),
        [
            [
                ButtonConfig(text=ButtonMessages.CANCEL.get(lang=lang), callback_data=cb.CANCEL_MEETING),
            ],
        ],
    )


def request_information_with_cancel_view(lang: str, message: str, callback_data: cb.CallbackData) -> MitupView:
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


def change_settings_element_view(lang: str, message: str, callback_data=cb.CANCEL_SETTINGS) -> MitupView:
    """
    This view is used when in order to change a setting the user is asked for a message and we want to give
    them the option to Cancel the action and go back to settings.
    """
    return request_information_with_cancel_view(lang, message, callback_data)


def settings_set_language_view(lang: str, message: str | None = None) -> MitupView:
    n_languages = len(SUPPORTED_LANGUAGES)
    n_columns = min(n_languages, 3)
    buttons = [
        ButtonConfig(text=LANGUAGE_BUTTONS[lang_code].get(lang=lang), callback_data=cb.SET_LANGUAGE.with_id(idx))
        for idx, lang_code in enumerate(SUPPORTED_LANGUAGES)
    ]

    return PaginatedMitupView(
        description=(
            message or SettingsMessages.SELECT_LANGUAGE.get(lang=lang, language=LANGUAGE_BUTTONS[lang].get(lang=lang))
        ),
        buttons=buttons,
        column_size=n_columns,
        row_size=ceil(n_languages / n_columns),
        page_number=1,
    ).with_context_menu(
        [
            [
                ButtonConfig(
                    text=ButtonMessages.SETTINGS.back(lang=lang),
                    callback_data=cb.SETTINGS,
                ),
            ]
        ]
    )


def edit_meeting_property_view(
    lang: str,
    message: str,
    meeting_id: int,
    extra_buttons: list[list[ButtonConfig]] | None = None,
    back_button: ButtonConfig | None = None,
) -> MitupView:
    back_button = back_button or ButtonConfig(
        text=ButtonMessages.BACK_EDIT.get(lang=lang), callback_data=cb.EDIT_MEETING.with_id(meeting_id)
    )
    keyboard = [[back_button]]

    if extra_buttons:
        keyboard[:0] = extra_buttons

    return MitupView(message, keyboard=keyboard)


def __edit_meeting_date_final_row(lang: str, meeting_id: int, new: bool) -> list[list[ButtonConfig]]:
    final_rows = [
        [ButtonConfig(text=ButtonMessages.BACK_EDIT.get(lang=lang), callback_data=cb.EDIT_MEETING.with_id(meeting_id))]
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
    lang: str, meeting_id: int, anchor_date: dt.date, current_date: dt.date, new: bool
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
