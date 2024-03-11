from mitup_bot.utils import ButtonMessages, MeetingMessages, Messages
from mitup_bot.views import ButtonConfig, MitupView


def main_menu_view(message: str = Messages.DEFAULT_MAIN_MENU_DESCRIPTION.get()) -> MitupView:
    return MitupView(
        message,
        keyboard=[
            [
                ButtonConfig(ButtonMessages.NEW_MEETING.get(), callback_data="new_meeting"),
            ],
            [
                ButtonConfig(ButtonMessages.ACTIVE_MEETINGS.get(), callback_data="active_meetings"),
            ],
            [
                ButtonConfig(ButtonMessages.PAST_MEETINGS.get(), callback_data="past_meetings"),
            ],
            [
                ButtonConfig(ButtonMessages.JOINED_MEETINGS.get(), callback_data="meetups"),
                ButtonConfig(ButtonMessages.SETTINGS.get(), callback_data="settings"),
            ],
            [
                ButtonConfig(ButtonMessages.HELP.get(), callback_data="help"),
                ButtonConfig(ButtonMessages.COLLABORATE.get(), callback_data="collaborate"),
            ],
        ],
    )


def settings_view(message: str = Messages.DEFAULT_SETTINGS_DESCRIPTION.get()) -> MitupView:
    return MitupView(
        message,
        [
            [
                ButtonConfig(ButtonMessages.LANGUAGE.get(), callback_data="language"),
                ButtonConfig(ButtonMessages.TIMEOUT.get(), callback_data="timeout"),
            ],
            [
                ButtonConfig(ButtonMessages.NOTIFICATIONS.get(), callback_data="notifications"),
                ButtonConfig(ButtonMessages.TIMEZONE.get(), callback_data="global_timezone"),
            ],
            [
                ButtonConfig(ButtonMessages.DEFAULT_OPTIONS.get(), callback_data="default_options"),
                ButtonConfig(ButtonMessages.PRIVACY.get(), callback_data="privacy"),
            ],
            [ButtonConfig(ButtonMessages.MAIN_MENU.get(), callback_data="main_menu")],
        ],
    )


def create_meeting_view(message: str = MeetingMessages.CREATE.get()) -> MitupView:
    return MitupView(
        message,
        [
            [
                ButtonConfig(ButtonMessages.CANCEL.get(), callback_data="cancel_meeting"),
            ],
        ],
    )


def change_settings_element_view(message: str) -> MitupView:
    return MitupView(
        message,
        [
            [
                ButtonConfig(ButtonMessages.CANCEL.get(), callback_data="cancel_settings"),
            ],
        ],
    )
