"""This module contains a list of predefined callbacks that can be reused through the code.
While custom CallbackData classes can be crated, it is common to use the same set of CallbackData
instances throughout the entire bot.
"""

from mitup_bot.callback_data import (
    CallbackData,
    CodeCallbackData,
    DateCallbackData,
    GrantCallbackData,
    MeetingCallbackData,
    PaginatedCallbackData,
)

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
# The supporter-grant flow: SET_GRANT_LEVEL and CONFIRM_GRANT carry the target user id plus the
# picked tier rank (see GrantCallbackData). CANCEL_GRANT uses action="cancel" so the global
# stale-cancel handler catches a tap that arrives outside the conversation.
SUPPORTER_GRANT = CallbackData(action="start", entity="grant")
SET_GRANT_LEVEL = GrantCallbackData(action="set", entity="grant")
CONFIRM_GRANT = GrantCallbackData(action="confirm", entity="grant")
CANCEL_GRANT = CallbackData(action="cancel", entity="grant")

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
# ---- When: the meeting's start and end, each with a date and a time
# The two halves are wire-symmetric: one entity per half, and the same six gestures on each. The
# `aliases` on a gesture are the retired forms it still answers — a keyboard already sitting in a
# chat sends the string it was built with, and nothing on the tap says which form it is.
EDIT_MEETING_WHEN = CallbackData(action="edit", entity="meet_when")
DELETE_MEETING_TIMES = CallbackData(action="delete", entity="meet_times")
CONFIRM_DELETE_MEETING_TIMES = CallbackData(action="confirm_delete", entity="meet_times")
DECLINE_DELETE_MEETING_TIMES = CallbackData(action="decline_delete", entity="meet_times")
SET_MEETING_LOCK_ON_START = CallbackData(action="set", entity="meet_lock")
# Start half
OPEN_START_EDITOR = CallbackData(action="open", entity="meet_start", aliases=(("set", "meet_st"),))
# The one gesture of either half with no alias, and it must stay that way. The retired form here
# would be `edit;meeting`, which is the Edit screen's own wire, and this callback's handler is an
# entry point of the start conversation. A conversation's entry points are matched ahead of plain
# handlers, so accepting that form would divert every Edit-button tap in the bot into the start-time
# flow. A button carrying it reaches the Edit screen, which is where it visibly goes.
REOPEN_START_EDITOR = CallbackData(action="reopen", entity="meet_start")
NAVIGATE_START_CALENDAR = DateCallbackData(action="nav", entity="meet_start", aliases=(("edit", "meet_date"),))
PICK_START_DATE = DateCallbackData(action="pick", entity="meet_start", aliases=(("set", "md"),))
OPEN_START_TIME_PROMPT = CallbackData(action="ask_time", entity="meet_start", aliases=(("edit", "meet_time"),))
CANCEL_START_EDIT = CallbackData(action="cancel", entity="meet_start", aliases=(("cancel", "meet_st"),))
# End half
OPEN_END_EDITOR = CallbackData(action="open", entity="meet_end", aliases=(("set", "meet_et"),))
REOPEN_END_EDITOR = CallbackData(action="reopen", entity="meet_end", aliases=(("edit", "meet_edt"),))
NAVIGATE_END_CALENDAR = DateCallbackData(action="nav", entity="meet_end", aliases=(("edit", "meet_ed"),))
PICK_END_DATE = DateCallbackData(action="pick", entity="meet_end", aliases=(("set", "med"),))
OPEN_END_TIME_PROMPT = CallbackData(action="ask_time", entity="meet_end", aliases=(("edit", "meet_et"),))
CANCEL_END_EDIT = CallbackData(action="cancel", entity="meet_end", aliases=(("cancel", "meet_dur"),))
# ---- General
EDIT_MEETING_CANCEL = CallbackData(action="cancel", entity="meet_edit")

# ----------------------------------------
# Main menu callbacks
# These are callbacks for the main menu actions
# ----------------------------------------
MAIN_MENU = CallbackData(entity="main_menu")
# SEND_MAIN_MENU sends the main menu as a NEW message instead of editing the tapped one, so a
# standalone message (e.g. a broadcast) stays in the chat while the user still gets a way back to
# the menu. Distinct action keeps its pattern from colliding with MAIN_MENU, whose handler edits.
SEND_MAIN_MENU = CallbackData(action="send", entity="main_menu")
PAST_MEETINGS = CallbackData(entity="past_meetings")
SETTINGS = CallbackData(entity="settings")
HELP = CallbackData(entity="help")
COLLABORATE = CallbackData(entity="collaborate")
UNLINK_PATREON = CallbackData(action="unlink", entity="patreon")
# The unlink-confirmation pair. The subscription is resolved server-side from the account that
# presses the button, so the callbacks carry no id: a forged confirm can only unlink the forger.
CONFIRM_PATREON_UNLINK = CallbackData(action="confirm", entity="patreon_unlink")
DECLINE_PATREON_UNLINK = CallbackData(action="decline", entity="patreon_unlink")
# The link-confirmation pair. Addressed by the pairing code rather than the pending row's id:
# callback data is client-supplied, and a row id is a small guessable integer, so a forged button
# could otherwise name somebody else's pending link. The entity is kept to two characters because
# the code already spends 32 of the 64 bytes Telegram allows.
CONFIRM_PATREON_LINK = CodeCallbackData(action="confirm", entity="pl")
DECLINE_PATREON_LINK = CodeCallbackData(action="decline", entity="pl")

# ----------------------------------------
# Settings callbacks
# These are callbacks for the user settings actions
# ----------------------------------------
CANCEL_SETTINGS = CallbackData(action="cancel", entity="settings")
EDIT_PRIVACY = CallbackData(action="edit", entity="privacy")
# SEND_PRIVACY sends the privacy screen as a NEW message instead of editing the tapped one, so a
# standalone message (the data-export document) stays in the chat while the user still gets a way
# back into the flow. Distinct action keeps its pattern from colliding with EDIT_PRIVACY, whose
# handler edits.
SEND_PRIVACY = CallbackData(action="send", entity="privacy")
EXPORT_USER_DATA = CallbackData(action="export", entity="user_data")
# Data-deletion flow: two confirmation steps, both declining back to the privacy screen through
# the single DECLINE callback.
DELETE_USER_DATA = CallbackData(action="delete", entity="user_data")
CONFIRM_DELETE_USER_DATA = CallbackData(action="confirm_delete", entity="user_data")
CONFIRM_DELETE_USER_DATA_FINAL = CallbackData(action="confirm_delete", entity="user_data_final")
DECLINE_DELETE_USER_DATA = CallbackData(action="decline_delete", entity="user_data")
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
