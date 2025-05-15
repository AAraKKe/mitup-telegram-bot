from enum import auto

from mitup_bot.callback_id import CallbackId


class MainMenuHandlerId(CallbackId):
    MAIN_MENU_CALLBACK = auto()
    # Show my meetings
    SHOW_MEETINGS_CALLBACK = auto()
    # Show meetings I have joined
    SHOW_JOINED_MEETINGS_CALLBACK = auto()
