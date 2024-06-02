import datetime as dt

from mitup_bot.utils import ButtonMessages, MeetingMessages, Messages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import ButtonConfig, CalendarKeyboard, MitupView


def main_menu_view(message: str = Messages.DEFAULT_MAIN_MENU_DESCRIPTION.get()) -> MitupView:
    return MitupView(
        message,
        keyboard=[
            [
                ButtonConfig(text=ButtonMessages.NEW_MEETING.get(), callback_data=cb.CREATE_MEETING),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.ACTIVE_MEETINGS.get(), callback_data=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(1)
                ),
            ],
            [
                ButtonConfig(text=ButtonMessages.PAST_MEETINGS.get(), callback_data=cb.PAST_MEETINGS),
            ],
            [
                ButtonConfig(text=ButtonMessages.JOINED_MEETINGS.get(), callback_data=cb.JOINED_MEETINGS),
                ButtonConfig(text=ButtonMessages.SETTINGS.get(), callback_data=cb.SETTINGS),
            ],
            [
                ButtonConfig(text=ButtonMessages.HELP.get(), callback_data=cb.HELP),
                ButtonConfig(text=ButtonMessages.COLLABORATE.get(), callback_data=cb.COLLABORATE),
            ],
        ],
    )


def settings_view(message: str = Messages.DEFAULT_SETTINGS_DESCRIPTION.get()) -> MitupView:
    return MitupView(
        message,
        [
            [
                ButtonConfig(text=ButtonMessages.LANGUAGE.get(), callback_data=cb.EDIT_LANGUAGE),
                ButtonConfig(text=ButtonMessages.TIMEOUT.get(), callback_data=cb.EDIT_TIMEOUT),
            ],
            [
                ButtonConfig(text=ButtonMessages.NOTIFICATIONS.get(), callback_data=cb.EDIT_NOTIFICATIONS),
                ButtonConfig(text=ButtonMessages.TIMEZONE.get(), callback_data=cb.EDIT_TIEMZONE),
            ],
            [
                ButtonConfig(text=ButtonMessages.DEFAULT_OPTIONS.get(), callback_data=cb.EDIT_DEFAULTS),
                ButtonConfig(text=ButtonMessages.PRIVACY.get(), callback_data=cb.EDIT_PRIVACY),
            ],
            [ButtonConfig(text=ButtonMessages.MAIN_MENU.get(), callback_data=cb.MAIN_MENU)],
        ],
    )


def create_meeting_view(message: str = MeetingMessages.CREATE.get()) -> MitupView:
    return MitupView(
        message,
        [
            [
                ButtonConfig(text=ButtonMessages.CANCEL.get(), callback_data=cb.CANCEL_MEETING),
            ],
        ],
    )


def change_settings_element_view(message: str) -> MitupView:
    return MitupView(
        message,
        [
            [
                ButtonConfig(text=ButtonMessages.CANCEL.get(), callback_data=cb.CANCEL_SETTINGS),
            ],
        ],
    )


def edit_meeting_property_view(
    message: str,
    meeting_id: int,
    extra_buttons: list[list[ButtonConfig]] | None = None,
    back_button: ButtonConfig | None = None,
) -> MitupView:
    back_button = back_button or ButtonConfig(
        text=ButtonMessages.BACK_EDIT.get(), callback_data=cb.EDIT_MEETING.with_id(meeting_id)
    )
    keyboard = [[back_button]]

    if extra_buttons:
        keyboard[:0] = extra_buttons

    return MitupView(message, keyboard=keyboard)


def __edit_meeting_date_final_row(meeting_id: int, new: bool) -> list[list[ButtonConfig]]:
    final_rows = [
        [ButtonConfig(text=ButtonMessages.BACK_EDIT.get(), callback_data=cb.EDIT_MEETING.with_id(meeting_id))]
    ]

    if not new:
        # Allow deleting the date if date is already set
        final_rows.append(
            [
                ButtonConfig(
                    text=ButtonMessages.DELETE_DATE.get(), callback_data=cb.DELETE_MEETING_DATE.with_id(meeting_id)
                )
            ],
        )

    return final_rows


def edit_meeting_date_view(meeting_id: int, anchor_date: dt.date, current_date: dt.date, new: bool) -> MitupView:
    message = MeetingMessages.ADD_DATE.get() if new else MeetingMessages.EDIT_DATE.get()
    calendar_keyboard = CalendarKeyboard(
        anchor_date,
        current_date,
        cb.SET_MEETING_DATE.with_id(meeting_id),
        cb.EDIT_MEETING_DATE.with_id(meeting_id),
    ).keyboard

    # Final row includes the back button and the delete date button when the date is set
    calendar_keyboard.extend(__edit_meeting_date_final_row(meeting_id, new))

    return MitupView(description=message, keyboard=calendar_keyboard)
