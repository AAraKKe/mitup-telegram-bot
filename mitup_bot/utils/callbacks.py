"""This module contains a list of predefined callbacks that can be reused through the code.
While custom CallbackData classes can be crated, it is common to use the same set of CallbackData
instances throughout the entire bot.
"""

from mitup_bot.callback_data import CallbackData, DateCallbackData, MeetingCallbackData, PaginatedCallbackData

# Empty callback data. Inline keyboards are forced to include some callback data but sometimes
# we just need a button for display purposes (i.e. CalendarKeyboard)
EMPTY = CallbackData(action="empty", entity="empty", id=0)

# ----------------------------------------
# Admin callbacks
# Operator-only actions reached from the admin menu. Every handler bound to these must be
# registered with `admin_only=True` so forged callback data from non-admins is dropped.
# ----------------------------------------
ADMIN_MENU = CallbackData(action="show", entity="admin")
BROADCAST = CallbackData(action="start", entity="broadcast")

# ----------------------------------------
# Meeting callbacks
# These are callbacks for the meeting and edit meeting views
# ----------------------------------------
# SHOW_MEETING and SHOW_PAST_MEETING remember the list page (and, for SHOW_MEETING, which
# list) they were opened from so the detail view can send the user back to that exact page
# (see PaginatedCallbackData).
SHOW_MEETING = PaginatedCallbackData(action="show", entity="meeting")
SHOW_ACTIVE_MEETING_PAGE = CallbackData(action="show", entity="active_meeting_page")
SHOW_JOINED_MEETINGS_PAGE = CallbackData(action="show", entity="joined_meetings")
SHOW_PAST_MEETING = PaginatedCallbackData(action="show", entity="past_meeting")
SHOW_PAST_MEETING_PAGE = CallbackData(action="show", entity="past_meeting_page")
EDIT_MEETING = CallbackData(action="edit", entity="meeting")
CREATE_MEETING = CallbackData(action="create", entity="meeting")
CANCEL_CREATE_MEETING = CallbackData(action="cancel", entity="meeting")
JOIN = CallbackData(action="join", entity="meeting")
INVITE = CallbackData(action="invite", entity="meeting")
LEAVE = CallbackData(action="leave", entity="meeting")
CHAT = CallbackData(action="chat", entity="meeting")
DELETE_MEETING = CallbackData(action="delete", entity="meeting")
CONFIRM_DELETE_MEETING = CallbackData(action="confirm_delete", entity="meeting")
DECLINE_DELETE_MEETING = CallbackData(action="decline_delete", entity="meeting")
SHARE = CallbackData(action="share", entity="meeting")
ATTACH_TO_CHAT = CallbackData(action="attach", entity="meeting")
LOAD_CHAT_MEETINGS = CallbackData(entity="chat_meet")
REACTIVATE_MEETING = CallbackData(action="reac", entity="meeting")
# The past-meeting delete flow threads the originating list page through every step so the
# "Back" button (on both the detail view and the delete-success view) returns to that page.
DELETE_PAST_MEETING = PaginatedCallbackData(action="delete", entity="past_meeting")
CONFIRM_DELETE_PAST_MEETING = PaginatedCallbackData(action="confirm_delete", entity="past_meeting")
DECLINE_DELETE_PAST_MEETING = PaginatedCallbackData(action="decline_delete", entity="past_meeting")
CONFIRM_INVITE_USER = CallbackData(action="confirm", entity="invite")
CANCEL_INVITE_USER = CallbackData(action="cancel", entity="invite")

# ----------------------------------------
# Edit meeting callbacks
# These are callbacks for the edit meeting actions
# ----------------------------------------
# ---- Title and description
EDIT_MEETING_DESCRIPTION = CallbackData(action="edit", entity="meet_desc")
EDIT_MEETING_TITLE = CallbackData(action="edit", entity="meet_title")
# ---- Datetime
EDIT_MEETING_DATE = DateCallbackData(action="edit", entity="meet_date")
# This callback is part of the calendar view, the one above is part of the menu
SET_MEETING_DATE = DateCallbackData(action="set", entity="md")
EDIT_MEETING_TIME = CallbackData(action="edit", entity="meet_time")
# ---- Participants
EDIT_MEETING_PARTICIPANTS = CallbackData(action="edit", entity="meet_part")
EDIT_MEETING_MAX_PARTICIPANTS = CallbackData(action="edit", entity="meet_max_part")
EDIT_MEETING_NO_LIMIT_PARTICIPANTS = CallbackData(action="edit", entity="meet_nl_part")
EDIT_MEETING_KICK_OUT_PARTICIPANTS = MeetingCallbackData(action="show", entity="kickout_page")
EDIT_MEETING_KICK_OUT_ACTION = MeetingCallbackData(action="kickout", entity="user")
CONFIRM_KICK_OUT = MeetingCallbackData(action="confirm", entity="kickout")
CANCEL_EDIT_MEETING_PARTICIPANS = CallbackData(action="cancel", entity="meet_part")
# ---- Location
EDIT_MEETING_LOCATION = CallbackData(action="edit", entity="meet_loc")
EDIT_MEETING_LOCATION_NAME = CallbackData(action="edit", entity="meet_loc_name")
EDIT_MEETING_LOCATION_COORDINATES = CallbackData(action="edit", entity="meet_loc_coords")
CANCEL_EDIT_MEETING_LOCATION = CallbackData(action="cancel", entity="meet_loc")
# ---- Language
EDIT_MEETING_LANGUAGE = CallbackData(action="edit", entity="meet_lang")
SET_MEETING_LANGUAGE = MeetingCallbackData(action="set", entity="meet_lang")
# ---- Settings
EDIT_MEETING_SETTINGS = CallbackData(action="edit", entity="meet_settings")
SET_MEETING_WAITING_LIST = CallbackData(action="set", entity="meet_wait")
SET_MEETING_PUBLIC = CallbackData(action="set", entity="meet_pub")
SET_MEETING_ALLOW_INVITATIONS = CallbackData(action="set", entity="meet_inv")
SET_MEETING_INCOGNITO = CallbackData(action="set", entity="meet_inc")
# ---- When / Start & End datetime
EDIT_MEETING_WHEN = CallbackData(action="edit", entity="meet_when")
SET_MEETING_START_TIME = CallbackData(action="set", entity="meet_st")
SET_MEETING_END_TIME = CallbackData(action="set", entity="meet_et")
DELETE_MEETING_TIMES = CallbackData(action="delete", entity="meet_times")
CONFIRM_DELETE_MEETING_TIMES = CallbackData(action="confirm_delete", entity="meet_times")
DECLINE_DELETE_MEETING_TIMES = CallbackData(action="decline_delete", entity="meet_times")
CANCEL_EDIT_START_TIME = CallbackData(action="cancel", entity="meet_st")
CANCEL_EDIT_MEETING_DURATION = CallbackData(action="cancel", entity="meet_dur")
SET_MEETING_LOCK_ON_START = CallbackData(action="set", entity="meet_lock")
# End datetime within duration conversation
EDIT_MEETING_END_DATE_TIME = CallbackData(action="edit", entity="meet_edt")
EDIT_MEETING_END_DATE = DateCallbackData(action="edit", entity="meet_ed")
SET_MEETING_END_DATE = DateCallbackData(action="set", entity="med")
EDIT_MEETING_END_TIME = CallbackData(action="edit", entity="meet_et")
CANCEL_EDIT_MEETING_END_DATETIME = CallbackData(action="cancel", entity="meet_edt")
# ---- General
EDIT_MEETING_CANCEL = CallbackData(action="cancel", entity="meet_edit")

# ----------------------------------------
# Main menu callbacks
# These are callbacks for the main menu actions
# ----------------------------------------
MAIN_MENU = CallbackData(entity="main_menu")
PAST_MEETINGS = CallbackData(entity="past_meetings")
SETTINGS = CallbackData(entity="settings")
HELP = CallbackData(entity="help")
COLLABORATE = CallbackData(entity="collaborate")
UNLINK_PATREON = CallbackData(action="unlink", entity="patreon")

# ----------------------------------------
# Settings callbacks
# These are callbacks for the user settings actions
# ----------------------------------------
CANCEL_SETTINGS = CallbackData(action="cancel", entity="settings")
EDIT_LANGUAGE = CallbackData(action="edit", entity="lang")
SET_LANGUAGE = CallbackData(action="set", entity="lang")
EDIT_TIMEOUT = CallbackData(action="edit", entity="timeout")
EDIT_NOTIFICATIONS = CallbackData(action="edit", entity="notif")
EDIT_TIEMZONE = CallbackData(action="edit", entity="timezone")
# Default meeting options
EDIT_DEFAULT_OPTIONS = CallbackData(action="edit", entity="defaults")
SET_DEFAULT_WAITING_LIST = CallbackData(action="set", entity="def_wait")
SET_DEFAULT_PUBLIC = CallbackData(action="set", entity="def_pub")
SET_DEFAULT_INVITATIONS = CallbackData(action="set", entity="def_inv")
SET_DEFAULT_INCOGNITO = CallbackData(action="set", entity="def_inc")
SET_DEFAULT_LOCK_ON_START = CallbackData(action="set", entity="def_lock")
EDIT_PRIVACY = CallbackData(action="edit", entity="privacy")
# Notifications
TOGGLE_NOTIFICATIONS = CallbackData(action="toggle", entity="notif")
SET_NOTIFICATION_TIME = CallbackData(action="set", entity="notif_time")

# ----------------------------------------
# Broadcast callbacks (operator-only)
# CANCEL_BROADCAST uses action="cancel" so the global stale-cancel handler catches a tap that
# lands outside an active broadcast conversation.
# ----------------------------------------
CONFIRM_BROADCAST = CallbackData(action="confirm", entity="broadcast")
CANCEL_BROADCAST = CallbackData(action="cancel", entity="broadcast")
