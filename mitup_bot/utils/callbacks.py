"""This module contains a list of predefined callbacks that can be reused through the code.
While custom CallbackData classes can be crated, it is common to use the same set of CallbackData
instances throughout the entire bot.
"""

from mitup_bot.callback_data import CallbackData

SHOW_MEETING = CallbackData(action="show", entity="meeting")
EDIT_MEETING = CallbackData(action="edit", entity="meeting")
MAIN_MENU = CallbackData(entity="main_menu")
SETTINGS = CallbackData(entity="settings")
EDIT_TIEMZONE = CallbackData(action="edit", entity="timezone")
