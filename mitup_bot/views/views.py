from mitup_bot.utils import ButtonMessages, Messages
from mitup_bot.views import ButtonConfig, MitupView


def main_menu_view(message: str = Messages.DEFAULT_MAIN_MENU_DESCRIPTION.get()) -> MitupView:
    return MitupView(
        message,
        keyboard=[
            [
                ButtonConfig(ButtonMessages.BUTTON_NEW_MEETING.get(), callback_data="new_meeting"),
            ],
            [
                ButtonConfig(ButtonMessages.BUTTON_ACTIVE_MEETINGS.get(), callback_data="active_meetings"),
            ],
            [
                ButtonConfig(ButtonMessages.BUTTON_PAST_MEETINGS.get(), callback_data="past_meetings"),
            ],
            [
                ButtonConfig(ButtonMessages.BUTTON_JOINED_MEETINGS.get(), callback_data="meetups"),
                ButtonConfig(ButtonMessages.BUTTON_SETTINGS.get(), callback_data="settings"),
            ],
            [
                ButtonConfig(ButtonMessages.BUTTON_HELP.get(), callback_data="help"),
                ButtonConfig(ButtonMessages.BUTTON_COLLABORATE.get(), callback_data="collaborate"),
            ],
        ],
    )


def settings_view(message: str = Messages.DEFAULT_SETTINGS_DESCRIPTION.get()) -> MitupView:
    return MitupView(
        message,
        [
            [
                ButtonConfig(ButtonMessages.BUTTON_LANGUAGE.get(), callback_data="language"),
                ButtonConfig(ButtonMessages.BUTTON_TIMEOUT.get(), callback_data="timeout"),
            ],
            [
                ButtonConfig(ButtonMessages.BUTTON_NOTIFICATIONS.get(), callback_data="notifications"),
                ButtonConfig(ButtonMessages.BUTTON_TIMEZONE.get(), callback_data="global_timezone"),
            ],
            [
                ButtonConfig(ButtonMessages.BUTTON_DEFAULT_OPTIONS.get(), callback_data="default_options"),
                ButtonConfig(ButtonMessages.BUTTON_PRIVACY.get(), callback_data="privacy"),
            ],
            [ButtonConfig(ButtonMessages.BUTTON_MAIN_MENU.get(), callback_data="main_menu")],
        ],
    )


def change_settings_element_view(message: str) -> MitupView:
    return MitupView(
        message,
        [
            [
                ButtonConfig(ButtonMessages.BUTTON_CANCEL.get(), callback_data="cancel_settings"),
            ],
        ],
    )
