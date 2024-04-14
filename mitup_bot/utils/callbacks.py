"""This module contains a list of predefined callbacks that can be reused through the code.
While custom CallbackData classes can be crated, it is common to use the same set of CallbackData
instances throughout the entire bot.
"""

from mitup_bot.callback_data import CallbackData

# Meeting callbacks
SHOW_MEETING = CallbackData(action="show", entity="meeting")
SHOW_ACTIVE_MEETING_PAGE = CallbackData(action="show", entity="active_meeting_page")
EDIT_MEETING = CallbackData(action="edit", entity="meeting")
CREATE_MEETING = CallbackData(action="create", entity="meeting")
CANCEL_MEETING = CallbackData(action="cancel", entity="meeting")
JOIN = CallbackData(action="join", entity="meeting")
INVITE = CallbackData(action="invite", entity="meeting")
LEAVE = CallbackData(action="leave", entity="meeting")
CHAT = CallbackData(action="chat", entity="meeting")
DELETE_MEETING = CallbackData(action="delete", entity="meeting")
SHARE = CallbackData(action="share", entity="meeting")

# Edit meeting callbacks
EDIT_MEETING_DESCRIPTION = CallbackData(action="edit", entity="meet_desc")
EDIT_MEETING_TITLE = CallbackData(action="edit", entity="meet_title")
EDIT_MEETING_DATE = CallbackData(action="edit", entity="meet_date")
EDIT_MEETING_TIME = CallbackData(action="edit", entity="meet_time")
EDIT_MEETING_PARTICIPANTS = CallbackData(action="edit", entity="meet_part")
EDIT_MEETING_MAX_PARTICIPANTS = CallbackData(action="edit", entity="meet_max_part")
EDIT_MEETING_NO_LIMIT_PARTICIPANTS = CallbackData(action="edit", entity="meet_nl_part")
EDIT_MEETING_KICK_OUT_PARTICIPANS = CallbackData(action="edit", entity="meet_ko_part")
EDIT_MEETING_LOCATION = CallbackData(action="edit", entity="meet_loc")
EDIT_MEETING_LOCATION_NAME = CallbackData(action="edit", entity="meet_loc_name")
EDIT_MEETING_LOCATION_COORDINATES = CallbackData(action="edit", entity="meet_loc_coords")
EDIT_MEETING_LANGUAGE = CallbackData(action="edit", entity="meet_lang")
EDIT_MEETING_SETTINGS = CallbackData(action="edit", entity="meet_settings")
EDIT_MEETING_CANCEL = CallbackData(action="cancel", entity="meet_edit")
CANCEL_EDIT_MEETING_LOCATION = CallbackData(action="cancel", entity="meet_loc")
CANCEL_EDIT_MEETING_PARTICIPANS = CallbackData(action="cancel", entity="meet_part")

# Main menu callbacks
MAIN_MENU = CallbackData(entity="main_menu")
ACTIVE_MEETINGS = CallbackData(entity="meetings")
PAST_MEETINGS = CallbackData(entity="past_meetings")
JOINED_MEETINGS = CallbackData(entity="joined_meetings")
SETTINGS = CallbackData(entity="settings")
HELP = CallbackData(entity="help")
COLLABORATE = CallbackData(entity="collaborate")

# Settings callbacks
CANCEL_SETTINGS = CallbackData(action="cancel", entity="settings")
EDIT_LANGUAGE = CallbackData(action="edit", entity="lang")
EDIT_TIMEOUT = CallbackData(action="edit", entity="timeout")
EDIT_NOTIFICATIONS = CallbackData(action="edit", entity="notif")
EDIT_TIEMZONE = CallbackData(action="edit", entity="timezone")
EDIT_DEFAULTS = CallbackData(action="edit", entity="defaults")
EDIT_PRIVACY = CallbackData(action="edit", entity="privacy")
