from typing import assert_never

from mitup_bot.callback_data import BackOrigin, BackTarget
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages


def main_menu_back_button(lang: str) -> ButtonConfig:
    return ButtonConfig(text=ButtonMessages.MAIN_MENU.back(lang=lang), callback_data=cb.MAIN_MENU)


def back_button_for(origin: BackOrigin | None, lang: str) -> ButtonConfig:
    """The back button of a screen opened from *origin*, defaulting to the main menu without one.

    A meeting target naming no meeting also falls back to the main menu: callback data is
    client-supplied, so an origin missing its id addresses no screen to return to.
    """
    if origin is None:
        return main_menu_back_button(lang)
    match origin.target:
        case BackTarget.MEETING_EDITOR:
            if origin.id is None:
                return main_menu_back_button(lang)
            return ButtonConfig(
                text=ButtonMessages.MEETING.back(lang=lang), callback_data=cb.EDIT_MEETING.with_id(origin.id)
            )
        case _ as unreachable:
            assert_never(unreachable)
