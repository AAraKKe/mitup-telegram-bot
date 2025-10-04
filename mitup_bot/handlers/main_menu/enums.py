from enum import auto

from mitup_bot.handler_id import HandlerId


class MainMenuHandlerId(HandlerId):
    MAIN_MENU_CALLBACK = auto()
    # Show my meetings
    SHOW_MEETINGS_CALLBACK = auto()
    # Show meetings I have joined
    SHOW_JOINED_MEETINGS_CALLBACK = auto()
