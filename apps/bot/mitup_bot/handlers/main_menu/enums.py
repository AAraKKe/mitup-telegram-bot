from enum import auto

from mitup_bot.handler_id import HandlerId


class MainMenuHandlerId(HandlerId):
    MAIN_MENU_CALLBACK = auto()
    # Sends the main menu as a new message (does not edit the tapped message).
    SEND_MAIN_MENU_CALLBACK = auto()
    # Show my meetings
    SHOW_MEETINGS_CALLBACK = auto()
    # Show meetings I have joined
    SHOW_JOINED_MEETINGS_CALLBACK = auto()
    # Show past meetings
    SHOW_PAST_MEETINGS_CALLBACK = auto()
    SHOW_PAST_MEETING_PAGE_CALLBACK = auto()
