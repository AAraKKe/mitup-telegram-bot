from mitup_bot.utils import ButtonMessages, MeetingMessages, Messages
from mitup_bot.views import ButtonConfig, MitupView


def main_menu_view(message: str = Messages.DEFAULT_MAIN_MENU_DESCRIPTION.get()) -> MitupView:
    return MitupView(
        message,
        keyboard=[
            [
                ButtonConfig(text=ButtonMessages.NEW_MEETING.get(), callback_data="new_meeting"),
            ],
            [
                ButtonConfig(text=ButtonMessages.ACTIVE_MEETINGS.get(), callback_data="active_meetings"),
            ],
            [
                ButtonConfig(text=ButtonMessages.PAST_MEETINGS.get(), callback_data="past_meetings"),
            ],
            [
                ButtonConfig(text=ButtonMessages.JOINED_MEETINGS.get(), callback_data="meetups"),
                ButtonConfig(text=ButtonMessages.SETTINGS.get(), callback_data="settings"),
            ],
            [
                ButtonConfig(text=ButtonMessages.HELP.get(), callback_data="help"),
                ButtonConfig(text=ButtonMessages.COLLABORATE.get(), callback_data="collaborate"),
            ],
        ],
    )


def settings_view(message: str = Messages.DEFAULT_SETTINGS_DESCRIPTION.get()) -> MitupView:
    return MitupView(
        message,
        [
            [
                ButtonConfig(text=ButtonMessages.LANGUAGE.get(), callback_data="language"),
                ButtonConfig(text=ButtonMessages.TIMEOUT.get(), callback_data="timeout"),
            ],
            [
                ButtonConfig(text=ButtonMessages.NOTIFICATIONS.get(), callback_data="notifications"),
                ButtonConfig(text=ButtonMessages.TIMEZONE.get(), callback_data="global_timezone"),
            ],
            [
                ButtonConfig(text=ButtonMessages.DEFAULT_OPTIONS.get(), callback_data="default_options"),
                ButtonConfig(text=ButtonMessages.PRIVACY.get(), callback_data="privacy"),
            ],
            [ButtonConfig(text=ButtonMessages.MAIN_MENU.get(), callback_data="main_menu")],
        ],
    )


def create_meeting_view(message: str = MeetingMessages.CREATE.get()) -> MitupView:
    return MitupView(
        message,
        [
            [
                ButtonConfig(text=ButtonMessages.CANCEL.get(), callback_data="cancel_meeting"),
            ],
        ],
    )


def change_settings_element_view(message: str) -> MitupView:
    return MitupView(
        message,
        [
            [
                ButtonConfig(text=ButtonMessages.CANCEL.get(), callback_data="cancel_settings"),
            ],
        ],
    )
