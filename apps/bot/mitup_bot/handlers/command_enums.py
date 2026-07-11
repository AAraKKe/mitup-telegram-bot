from enum import auto

from mitup_bot.handler_id import HandlerId


class CommandsId(HandlerId):
    START_WITH_EXISTING_USER = auto()
    MAIN_MENU = auto()
