from mitup_bot.utils import Emojis, Messages
from mitup_bot.views import ButtonConfig, MitupView


def main_menu_view(message: str = Messages.DEFAULT_MAIN_MENU_DESCRIPTION.get()) -> MitupView:
    return MitupView(
        message,
        keyboard=[
            [
                ButtonConfig(f"{Emojis.NEW_MEETING} New meeting", callback_data="new_meeting"),
            ],
            [
                ButtonConfig(f"{Emojis.LIST} Your active meetings", callback_data="active_meetings"),
            ],
            [
                ButtonConfig(f"{Emojis.PAST} Your past meetings", callback_data="past_meetings"),
            ],
            [
                ButtonConfig(f"{Emojis.JOINED} Joined meetings", callback_data="meetups"),
                ButtonConfig(f"{Emojis.SETTINGS} Settings", callback_data="settings"),
            ],
            [
                ButtonConfig(f"{Emojis.HELP} Help", callback_data="help"),
                ButtonConfig(f"{Emojis.HEART} Collaborate", callback_data="collaborate"),
            ],
        ],
    )


def settings_view(message: str = Messages.DEFAULT_SETTINGS_DESCRIPTION.get()) -> MitupView:
    return MitupView(
        message,
        [
            [
                ButtonConfig(f"{Emojis.LANG} Language", callback_data="language"),
                ButtonConfig(f"{Emojis.TIMEOUT} Timeout", callback_data="timeout"),
            ],
            [
                ButtonConfig(f"{Emojis.NOTIF} Notifications", callback_data="notifications"),
                ButtonConfig(f"{Emojis.TIME} Timezone", callback_data="global_timezone"),
            ],
            [
                ButtonConfig(f"{Emojis.PEOPLE} Default Options", callback_data="default_options"),
                ButtonConfig(f"{Emojis.SHIELD} Privacy", callback_data="privacy"),
            ],
            [ButtonConfig("≪ Main Menu", callback_data="main_menu")],
        ],
    )


def change_settings_element_view(message: str) -> MitupView:
    return MitupView(
        message,
        [
            [
                ButtonConfig(f"{Emojis.CANCEL} Cancel", callback_data="cancel_settings"),
            ],
        ],
    )
