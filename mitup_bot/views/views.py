from mitup_bot.utils import ButtonMessages, MeetingMessages, Messages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import ButtonConfig, MitupView


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
