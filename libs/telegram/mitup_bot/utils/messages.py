from __future__ import annotations

from enum import StrEnum
from typing import Protocol, assert_never

from mitup_bot.emojis import Emojis
from mitup_bot.supporter import SupporterLevel
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils.entities import FormattedText, parse_format_tags

MessageParams = str | int | float | FormattedText | None


class TranslationEngineProtocol(Protocol):
    @classmethod
    def translate(cls, message_id: str, lang: str) -> str: ...


class MessageBase(StrEnum):
    def get(
        self,
        *,
        lang: str = TranslationEngine.FALLBACK_LANG,
        **kwargs: MessageParams,
    ) -> FormattedText:
        """Return the translated, tag-parsed message as a `FormattedText`.

        Keyword arguments are substituted into `${varname}` placeholders.
        Values may be plain scalars *or* a `FormattedText` from another
        `get()` call — in which case its entities are preserved and their
        offsets are adjusted to their final position in the outer string.
        """
        translated = self.to_lang(lang)
        substitutions = {k: v if isinstance(v, FormattedText) else str(v) for k, v in kwargs.items()}
        return parse_format_tags(translated, substitutions)

    def get_text(self, *, lang: str = TranslationEngine.FALLBACK_LANG, **kwargs: MessageParams) -> str:
        formatted_text = self.get(lang=lang, **kwargs)
        if formatted_text.entities:
            raise ValueError("Requested `get_text` from a message that contains entities")
        return formatted_text.text

    def to_lang(self, lang: str) -> str:
        """Given a message, return the translation in the given language."""
        # For now we are not yet translating messages but here is where we should implement it
        return self.translations_class().translate(self.id(), lang)

    def id(self) -> str:
        return f"{self.__class__.__name__}.{self.name}"

    def translations_class(self) -> type[TranslationEngineProtocol]:
        """This allows defining specific MessageBase types for testing without needed to provide translations"""
        return TranslationEngine


class ButtonMessages(MessageBase):
    # Navigation buttons
    MAIN_MENU = "Main Menu"
    GO_BACK = "≪"
    GO_FORWARD = "≫"
    CONFIRM = f"{Emojis.CHECK} Confirm"
    DECLINE = f"{Emojis.CANCEL} Decline"

    # Main Menu buttons
    NEW_MEETING = f"{Emojis.NEW_MEETING} New meeting"
    ACTIVE_MEETINGS = f"{Emojis.LIST} Your active meetings"
    PAST_MEETINGS = f"{Emojis.PAST} Your past meetings"
    JOINED_MEETINGS = f"{Emojis.JOINED} Joined meetings"
    SETTINGS = f"{Emojis.SETTINGS} Settings"
    HELP = f"{Emojis.HELP} Help"
    COLLABORATE = f"{Emojis.HEART} Collaborate"

    # Settings buttons
    LANGUAGE = f"{Emojis.LANG} Language"
    TIMEOUT = f"{Emojis.HOURGLASS} Timeout"
    NOTIFICATIONS = f"{Emojis.NOTIF} Notifications"
    TIMEZONE = f"{Emojis.TIME} Timezone"
    DEFAULT_OPTIONS = f"{Emojis.PEOPLE} Default Options"
    PRIVACY = f"{Emojis.SHIELD} Privacy"
    PRIVACY_POLICY = f"{Emojis.SHIELD} Privacy policy"
    OPEN_USER_GUIDE = f"{Emojis.BOOK} Open the user guide"
    EXPORT_MY_DATA = f"{Emojis.PACKAGE} Export my data"
    DELETE_MY_DATA = f"{Emojis.DELETE} Delete my data"
    WAITING_LIST = "Waiting list"
    PUBLIC = "Public"
    OPEN_INVITATION = "Open invitations"
    INCOGNITO = "Incognito"
    ENABLE = "Enable"
    DISABLE = "Disable"

    # Meeting buttons
    CANCEL = f"{Emojis.CANCEL} Cancel"
    TITLE = f"{Emojis.TITLE} Title"
    DESCRIPTION = f"{Emojis.DESCRIPTION} Description"
    DATE = f"{Emojis.CALENDAR} Date"
    DATE_TIME = f"{Emojis.CALENDAR} Date & Time"
    END_DATE_TIME = f"{Emojis.CALENDAR} End Date & Time"
    TIME = f"{Emojis.CLOCK} Time"
    NOTIFICATIONS_TIME = f"{Emojis.NOTIF} Time"
    SET_TIME = f"{Emojis.CLOCK} Set time"
    PARTICIPANTS = f"{Emojis.JOINED} Participants"
    LOCATION = f"{Emojis.MAP} Location"
    OPEN_IN_MAPS = f"{Emojis.PIN} Open in Maps"
    DONE = f"{Emojis.CHECK} Done"
    JOIN = f"{Emojis.CHECK} Join"
    INVITE = f"{Emojis.FRIEND} Invite"
    LEAVE = f"{Emojis.CANCEL} Leave"
    DELETE = f"{Emojis.DELETE} Delete"
    EDIT = f"{Emojis.EDIT} Edit"
    SHARE = f"{Emojis.SHARE} Share"
    MEETING_LOCATION_NAME = f"{Emojis.TITLE} Name"
    MEETING_LOCATION_COORDINATES = f"{Emojis.PIN} Location"
    MEETING_MAX_PARTICIPANTS = "Max participants"
    MEETING_NO_LIMIT_PARTICIPANTS = "No limit"
    # Replaces MEETING_NO_LIMIT_PARTICIPANTS for owners whose plan caps participant capacity.
    MEETING_MAX_CAP_PARTICIPANTS = "Max (${cap})"
    MEETING_KICK_OUT = "Kick out"
    DELETE_DATE = f"{Emojis.DELETE} Delete date"
    DELETE_DURATION = f"{Emojis.DELETE} Delete duration"
    MAKE_SEARCHABLE = "Make it searchable"
    LOAD_CHAT_MEETINGS = f"{Emojis.SEARCH} Load meetings"
    SEARCH_CHAT_MEETINGS = f"{Emojis.SEARCH} Search meetings"

    # When screen buttons
    WHEN = f"{Emojis.CLOCK} When"
    SET_START_TIME = f"{Emojis.START} Set start time"
    SET_END_TIME = f"{Emojis.STOP} Set end time"
    CLEAR_TIMES = f"{Emojis.DELETE} Clear times"

    # Duration buttons
    DURATION = f"{Emojis.HOURGLASS} Duration"
    SET_DURATION = f"{Emojis.HOURGLASS} Set duration"
    LOCK_ON_START = "Lock on start"

    # Settings — Default Options buttons
    MEETING_DURATION = f"{Emojis.HOURGLASS} Meeting duration"

    # Notification buttons
    REACTIVATE_MEETING = "Reactivate meeting"

    # Collaborate / Patreon buttons
    LINK_PATREON = f"{Emojis.HEART} Link Patreon account"
    BECOME_PATRON = f"{Emojis.DONATE} Become a Host"
    UNLINK_PATREON = "Unlink Patreon account"
    HOSTS_GROUP_JOIN = f"{Emojis.PEOPLE} Join the Hosts-Only Group"
    HOSTS_GROUP_OPEN = f"{Emojis.PEOPLE} Open the Hosts-Only Group"

    def back(self, lang: str, **kwargs: str) -> str:
        return f"{self.GO_BACK} {self.get(lang=lang, **kwargs)}"


# --- Main menu and meeting lists ---


class MainMenuMessages(MessageBase):
    DESCRIPTION = "Welcome to Mitup Bot!\n\nChoose one of the following options:"


class HelpMessages(MessageBase):
    DESCRIPTION = (
        "<b>Help</b>\n\n"
        "There are two ways to get help with Mitup. The user guide walks you through how Mitup "
        "works, page by page, and is the best place to start. If you still have a question or need "
        "to reach a person, email us at support@mitup.social."
    )


class MeetingListMessages(MessageBase):
    ACTIVE_DESCRIPTION = "These are all your active meetings."
    ACTIVE_EMPTY = (
        "You don't have any meetings yet.\n\nClick on <b>${new_meeting_button}</b> in the main menu to create one."
    )
    JOINED_DESCRIPTION = "These are the meetings you have joined."
    JOINED_EMPTY = "<i>You have not joined any meeting yet.</i>"
    PAST_DESCRIPTION = "These are all your past meetings."
    PAST_EMPTY = "<i>You have no past meetings yet.</i>"


# --- Cross-screen / shared messages ---


class CommonMessages(MessageBase):
    # Stale cancel button alert
    STALE_CANCEL_ALERT = "You've already answered this question"
    # Shared positive-integer validation (timeout + notification-time flows)
    POSITIVE_INTEGER_INVALID = (
        "Oops! That doesn't look like a valid number. Please enter a positive whole number. No decimals allowed!"
    )
    # Time/datetime input helpers shared by datetime and duration flows
    TIME_PROMPT = "Send me the time of the meeting in the format <i>HH:MM</i>"
    TIME_INVALID_VALUE = (
        f"My internal clock is old and cannot understand this science fiction time {Emojis.UFO}.\n\n"
        "Try again with a time that has valid hours (00-23) and minutes (00-59), "
        f"I am sure I can work with that {Emojis.BRAIN}."
    )
    TIME_INVALID_FORMAT = (
        f"Not sure I understand that time {Emojis.THINK}...\n\n"
        "Please, send the time in the format <i>HH:MM</i>, for example <i>15:30</i> or <i>09:15</i>"
    )
    DATETIME_INVALID = (
        "Tap <b>Date</b> or <b>Time</b> to update them, or send a message with a ${datetime_link} to set both at once."
    )
    # Raised from guards.py across access paths
    DELETED_MEETING_ALERT = "This meeting has been deleted"
    # Generic error when an internal guard check fails
    UNEXPECTED_ERROR = "Oops! Something unexpected happened. I've brought you back to the main menu. Please try again."
    # Shown when in-memory conversation state was lost (e.g. after a rolling deploy mid-flow)
    CONTEXT_LOST = (
        "<b>Sorry, we lost our place!</b>\n\nThe bot had a quick internal update and forgot where you "
        "were in the process. Everything you saved is safe. Please start what you were doing again from "
        "the menu below."
    )


class SupporterMessages(MessageBase):
    """Plan-limit notices for free and Gamemaster users. Every one is shown next to a Collaborate
    button ("Collaborate below"), states what the user can do right now, and invites them to become
    a Host on Patreon in an encouraging, thankful tone. They render as plain text in views and
    alerts alike, so they must stay entity-free (no inline-formatting tags)."""

    # Free user at the active-meetings cap.
    ACTIVE_MEETINGS_CAP = (
        "You've reached your limit of ${cap} active meetings. Wrap up or delete one to start "
        "another, or become a Mitup Host on Patreon to unlock more. Collaborate below has all "
        "the details."
    )
    # Gamemaster user at their raised cap: thank them and point at Commissioner, which lifts it.
    ACTIVE_MEETINGS_CAP_PATRON = (
        "Your Gamemaster plan hosts up to ${cap} active meetings, thanks for backing Mitup! Wrap "
        "up or delete one to start another, or go Commissioner on Patreon to remove the cap. "
        "Collaborate below has all the details."
    )
    # Free user picking a start date beyond the horizon (calendar pick or a sent date while editing).
    SCHEDULING_HORIZON = (
        "You can schedule meetings up to ${days} days ahead for now. Pick an earlier date, or "
        "become a Mitup Host on Patreon to schedule further out. Collaborate below has all the "
        "details."
    )
    # Gamemaster user beyond their raised horizon: thank them and point at Commissioner.
    SCHEDULING_HORIZON_PATRON = (
        "Your Gamemaster plan schedules up to ${days} days ahead, thanks for backing Mitup! Pick "
        "an earlier date, or go Commissioner on Patreon to schedule as far out as you like. "
        "Collaborate below has all the details."
    )
    # Free user whose new-meeting title embeds a date beyond the horizon: they stay in the title
    # step, so the actionable step is resending the title.
    SCHEDULING_HORIZON_TITLE = (
        "That date is more than ${days} days ahead, the furthest you can schedule for now. Send "
        "your meeting title again with an earlier date, or become a Mitup Host on Patreon to "
        "schedule further out. Collaborate below has all the details."
    )
    # Gamemaster variant of the title-step horizon notice.
    SCHEDULING_HORIZON_TITLE_PATRON = (
        "That date is more than ${days} days ahead, the furthest your Gamemaster plan schedules. "
        "Send your meeting title again with an earlier date, or go Commissioner on Patreon to "
        "schedule as far out as you like. Collaborate below has all the details."
    )
    # Free owner trying to set a participant limit above the free-tier capacity. Gamemaster-tier
    # owners are uncapped, so there is no Gamemaster counterpart.
    PARTICIPANT_CAPACITY = (
        "Free meetings can host up to ${cap} participants. Send a lower number, or become a Mitup "
        "Host on Patreon to host bigger meetings. Collaborate below has all the details."
    )


# --- Registration and settings ---


class RegistrationMessages(MessageBase):
    TIMEZONE_PROMPT = (
        "Welcome to Mitup Bot ${first_name}!\n\n"
        "Let's start by setting your timezone. Send me the name of your city or, "
        f"for a more accurate result, your location by pressing on {Emojis.CLIP} and "
        "selecting Location.\n\n"
        "<b>Important</b>: we do not store your location and this information is only used to "
        "configure your timezone."
    )
    TIMEZONE_SUCCESS = "Your timezone is ${timezone}"
    TIMEZONE_FAIL = "I'm sorry, I couldn't set your timezone. Please, try again."
    # Fallback shown during registration when a non-text, non-location message is sent
    TIMEZONE_INVALID_INPUT = "Please send your city name as text or share your location."


class SettingsMessages(MessageBase):
    DESCRIPTION = "Configure MitUp."

    # Timezone settings
    TIMEZONE_PROMPT = (
        "Your timezone is set to <b>${timezone}</b>. \n"
        "Send me the name of your city or your location to set your "
        "timezone or touch in <b>Cancel</b> to go back."
    )
    TIMEZONE_SUCCESS = "Your timezone has been set to: <b>${timezone}</b> "
    # On-exit prompt shown when an unexpected message interrupts timezone edit
    TIMEZONE_ON_EXIT = (
        "You were in the middle of changing your timezone. "
        "Send me either the name of your city or your location to continue.  "
        "If you don't want to continue, tap Cancel to exit."
    )

    # Language settings
    LANGUAGE_PROMPT = "Current language: <b>${language}</b>.\n\nSelect a language."
    # NOTE: shares the English "Language set." with MeetingEditLanguageMessages.SUCCESS
    # by design — distinct namespace, do not de-dup in the catalogs.
    LANGUAGE_SUCCESS = "Language set."

    # Default meeting default options
    DEFAULT_OPTIONS_DESCRIPTION = (
        "Here you can configure the default options used when creating a meeting. "
        "Do you usually create public meetings? Set it here. "
        "Are you always allowing people to invite other people or to share the meeting in other chats? Do it here. "
        "All your meetings will inherit this configuration.\n\n"
        "You can configure different aspects of your meeting:\n\n"
        "<b>Waiting list</b>: allow users to join the meeting even when it is full. "
        "Users joining when it is full will be added to a waiting list and added to the participants "
        "list as soon as a spot is available in the order they joined.\n\n"
        "<b>Public</b>: activate this to allow everyone that receives the meeting to share it again. "
        "Perfect to reach more people.\n\n"
        "<b>Open invitations</b>: activate this option to allow users who have joined the meeting to add friends "
        "even if those friends are not in Telegram.\n\n"
        "<b>Incognito</b>: a meeting with incognito enabled won't show the people that joined the meeting when shared. "
        "Only the number of participants will be shown. You will still be able to see the participants.\n\n"
        "<b>Lock on start</b>: when enabled and a meeting has a duration set, "
        "participants cannot join or leave once the meeting starts. "
        "When disabled, participants can still join or leave freely."
    )

    # Timeout messages
    TIMEOUT_PROMPT = (
        "Timeout defines how long after a meeting starts it is kept active. "
        "Meetings without a set duration are deactivated once this time has passed. "
        "After deactivation, you can reactivate a meeting from your <b>Past meetings menu</b>.\n\n"
        "The current timeout is <b>${timeout} minutes</b>\n\n"
        "Send the timeout (in minutes) you would like to use or touch Cancel to go back."
    )
    TIMEOUT_SUCCESS = "Timeout set to: <b>${timeout} minutes</b>"

    # Notification settings
    NOTIFICATIONS_DESCRIPTION = (
        "Configure MeetUp notifications.\n\n"
        "Notifications: ${notifications_status}\n"
        "When notifications are enabled, MeetUp will notify "
        "you <b>${notifications_time} minutes</b> before a meeting starts."
    )
    NOTIFICATIONS_TIME_PROMPT = (
        "Send how long before a meeting starts (in minutes) you would like to be notified or touch Cancel to go back."
    )
    NOTIFICATIONS_TIME_SUCCESS = "Notification time set to <b>${notifications_time} minutes</b>."

    # Generic status labels used in notifications settings
    ENABLED = f"Enabled {Emojis.CHECK}"
    DISABLED = f"Disabled {Emojis.CANCEL}"


class PrivacyMessages(MessageBase):
    DESCRIPTION = (
        "<b>Privacy</b>\n\n"
        "Your data belongs to you. Read the privacy policy to see what Mitup stores and why, "
        "download a copy of your data, or request the permanent deletion of everything Mitup "
        "knows about you."
    )
    # Sent as the caption of the exported JSON document.
    EXPORT_CAPTION = (
        "Here is a copy of everything Mitup stores about you. "
        "Other people in your meetings appear by display name only."
    )
    DELETE_WARNING = (
        "<b>Delete all your data?</b>\n\n"
        "This will permanently remove everything linked to your account:\n\n"
        "- Meetings you created will disappear for everyone who joined them, not just for you.\n"
        "- You will be removed from every meeting you joined.\n"
        "- Your settings and your Patreon link will be erased.\n\n"
        "<b>There is no undo.</b> Deleted data cannot be recovered."
    )
    DELETE_LAST_CHANCE = (
        "<b>Last chance</b>\n\n"
        "If you confirm now, your account stops working immediately and the deletion cannot be stopped.\n\n"
        "Do you really want to permanently delete all your data?"
    )
    DELETION_MARKED = (
        "Your deletion request is confirmed and your account has stopped working.\n\n"
        "Everything will be permanently removed within a day, and you will receive one final message "
        "confirming the deletion. To use Mitup again after that, send the /start command."
    )
    # Shown as a plain callback-query alert (max 200 characters), so it must stay entity-free.
    PENDING_DELETION_ALERT = (
        "Your account is scheduled for deletion and no longer works. You will receive one final "
        "confirmation message. To use Mitup again after that, send the /start command."
    )
    DELETION_COMPLETE = (
        "<b>Your data is deleted</b>\n\n"
        "Everything linked to your account is permanently gone: the meetings you created, "
        "your spot in the meetings you joined, and your settings. Buttons on older messages "
        "in this chat no longer do anything.\n\n"
        "To use Mitup again, send the /start command. Mitup has no memory of you now and "
        "will set you up from scratch."
    )


# --- Meeting core: creation and display ---


class MeetingCreationMessages(MessageBase):
    PROMPT = (
        "Lets create a meeting. What is the title?\n\n"
        "<i>Tip: you can also use ${datetime_link} to include a date and time directly.</i>"
    )
    SUCCESS = (
        "Meeting created: <b>${title}</b>\n\n"
        "You can add more information to the meeting with the options below. "
        "The information which has not been added won't be shown when the meeting is shared.\n\n"
        f"When finished click on {Emojis.CHECK} Done"
    )
    INVALID_TITLE = (
        f"I did not recognize what you sent as a valid title {Emojis.THINK}.\n\n"
        "Send a message with the title of the meeting or click on <b>Cancel</b>."
    )
    INVALID_TITLE_ENTITY = (
        f"I only understand a title or a title with a date {Emojis.THINK}.\n\n"
        "Please send just the meeting title, or the title with a date formatted by Telegram. "
        "Click <b>Cancel</b> to exit."
    )
    # On-exit prompt shown when an unexpected message interrupts create-meeting
    ON_EXIT = (
        "You were in the middle of creating a meeting. Send the title of the meeting to continue. "
        "If you would not like to continue, tap Cancel to exit."
    )


class MeetingDisplayMessages(MessageBase):
    # Entity-type label for an EntityDateTime (both start & end), not a visible heading
    DATETIME_ENTITY_LABEL = "Meeting time"
    START_LABEL = "Starts"
    END_LABEL = "Ends"
    CREATED_BY = "Created by: ${owner}"
    # Owner-view placeholders
    DESCRIPTION_NOT_SET = f"{Emojis.PROHIB} No description defined {Emojis.PROHIB}"
    DATE_NOT_SET = f"{Emojis.PROHIB} No time defined {Emojis.PROHIB}"
    LOCATION_NOT_SET = f"{Emojis.PROHIB} No location defined {Emojis.PROHIB}"
    # Participant-count empty fragment
    PARTICIPANT_COUNT_EMPTY = "Empty"
    PARTICIPANT_LABEL = "Participant"
    PARTICIPANTS_LABEL = "Participants"
    MAX_PARTICIPANTS_LABEL = "(Max: ${max_participants})"
    INVITED_BY = "<i>invited by ${user}</i>"
    # Shared-view empty-state sentences
    DESCRIPTION_EMPTY = "<i>This meeting has no description yet</i>"
    # Standalone state banners shown on the meeting card
    IN_PROGRESS_BANNER = "▶️ This meeting is in progress ▶️"
    FINISHED_SUMMARY_BANNER = (
        "✅ This meeting has finished ✅\n\n"
        "<b>From:</b> ${start_datetime} · <b>To:</b> ${end_datetime} · "
        "<b>Attendees:</b> ${attendee_count}"
    )
    DELETED_BANNER = f"{Emojis.PROHIB} This meeting has been deleted {Emojis.PROHIB}"
    FINISHED_BANNER = f"{Emojis.CHECK} This meeting has finished {Emojis.CHECK}"


# --- Meeting join / leave / invite ---


class MeetingJoinMessages(MessageBase):
    JOIN_SUCCESS = "You joined the meeting!"
    JOIN_ALREADY_JOINED = "You have already joined this meeting"
    JOIN_FULL = "Sorry! The meeting is full"
    JOIN_FULL_WAITING_LIST = "The meeting is full. You have been added to the waiting list."
    JOIN_UNREGISTERED = (
        "You have joined the meeting, ${user}! "
        "It seems you have never used Mitup before, open a chat with @mitupbot to "
        "receive notifications and create new meetings!"
    )
    LEAVE_SUCCESS = "You have left the meeting"
    LEAVE_NOT_JOINED = "You cannot leave a meeting you have not joined"
    LEAVE_UNREGISTERED = (
        "You have left the meeting, ${user}! "
        "It seems you have never used Mitup before, open a chat with @mitupbot to "
        "receive notifications and create new meetings!"
    )
    PROMOTED_FROM_WAITING_LIST = (
        "There is an open spot in the meeting <b>${meeting_title}</b>."
        " You have now been promoted from the waiting list!"
    )
    # Locked-meeting join/leave alert
    JOIN_LOCKED = "This meeting has already started and its attendees list is locked."


class MeetingInviteMessages(MessageBase):
    ON_EXIT = (
        "Sorry, I was expecting the name of the person you want to invite. "
        "Would you like to send it? If not, tap Cancel to exit."
    )
    PROMPT = "<b>Add to Guest List</b>\n\nPlease reply with the name of the person you want to add."
    CONFIRMATION = (
        "<b>Confirm Addition</b>\n\nAre you sure you want to add <b>${name}</b> to the meeting <b>${meeting_title}</b>?"
    )
    OPEN_CHAT = "Start Private Chat\n\nTo add participants, you need to open a chat with me first."
    GO_PRIVATE = "Continue in Private\n\nPlease switch to our private chat to send me the name."
    SUCCESS = "<b>List Updated</b>\n\nUser <b>${name}</b> has been added to the meeting <b>${meeting_title}</b>."
    MEETING_FULL = (
        "<b>No Spots Left</b>\n\nThe guest list is currently full. You cannot add anyone else unless a spot opens up."
    )
    INVITES_DISABLED = (
        "<b>Guest List Closed</b>\n\n"
        "New additions are no longer allowed for this meeting. The organizer has closed the list."
    )
    MEETING_LOST_RETRY = (
        "<b>That Was Unexpected...</b>\n\n"
        "I suddenly lost track of the meeting details. This shouldn't happen.\n\n"
        "Please try sending the name again."
    )
    MEETING_NOT_FOUND = (
        "<b>Meeting Not Found</b>\n\nThe meeting you are trying to invite someone to does not exist anymore."
    )
    CANCELED = "The invitation process has been canceled."
    ADD_FAILED_RETRY = "Something unexpected happened while adding the invited user. Please try again."


# --- Meeting lifecycle and attachment ---


class MeetingLifecycleMessages(MessageBase):
    PAST_DESCRIPTION = "This meeting is no longer active. Reactivate it to share it again, or delete it permanently."
    REACTIVATE_SUCCESS = "Meeting reactivated. You can edit and share it again."
    DELETE_CONFIRMATION = "Are you sure you want to delete this meeting?"
    DELETE_SUCCESS = "Meeting deleted."
    DELETE_DECLINED = "The meeting won't be deleted"


class MeetingAttachMessages(MessageBase):
    FOOTNOTE_INACTIVE = f"{Emojis.SEARCH} Make this meeting searchable in this chat."
    FOOTNOTE_ACTIVE = f"{Emojis.CHECK} This meeting is now searchable in this chat."
    ENABLED_ALERT = (
        f"{Emojis.CHECK} Now Searchable!\n\n"
        "This meeting is now attached to this chat. It will be included in your search "
        "results when you look for meetings using the bot's inline mode."
    )
    ALREADY_ENABLED_ALERT = (
        f"{Emojis.CHECK} Already Searchable!\n\n"
        "This meeting was already attached to this chat from a previous share. "
        "It is already included in your search results."
    )


# --- Meeting edit: content (title / description) ---


class MeetingEditContentMessages(MessageBase):
    TITLE_PROMPT = "This is the current title of your meeting:\n<b>${title}</b>\n\n Send me the new one"
    TITLE_SUCCESS = "Title updated to: <b>${title}</b>"
    TITLE_ON_EXIT = (
        "Sorry, I was expecting the title of your meeting. Would you like to send it? If not, tap Cancel to exit."
    )
    DESCRIPTION_PROMPT = "This is the current description of your meeting:\n${description}\n\n Send me the new one"
    # The user-supplied description is intentionally wrapped in bold so the newly set value
    # is visually highlighted in the confirmation message.
    DESCRIPTION_SUCCESS = "Description updated to: <b>${description}</b>"
    DESCRIPTION_ON_EXIT = (
        "Sorry, I was expecting the description of your meeting. Would you like to send it? If not, tap Cancel to exit."
    )


# --- Meeting edit: location ---


class MeetingEditLocationMessages(MessageBase):
    DESCRIPTION = (
        "A meeting can have a location associated. "
        "You can just set the name of the place or you can also attach the location. "
        "Choose any of the two options."
    )
    NAME_PROMPT = "Send me the name of the place."
    COORDINATES_PROMPT = (
        f"<i>Only from the phone {Emojis.PHONE}</i>\n\n"
        f"Send the location of the meeting. Touch on the {Emojis.CLIP} icon and then choose location. "
        "You can send whatever location you want, not just your current location."
    )
    NAME_SUCCESS = "Location name set to: <b>${name}</b>"
    COORDINATES_SUCCESS = "Location saved."
    COORDINATES_INVALID = "Send me the location again. Remember to touch on the clip icon and choose location."
    NAME_ON_EXIT = (
        "Sorry, I was expecting the name of the location. Would you like to send it? If not, tap Cancel to exit."
    )
    COORDINATES_ON_EXIT = (
        "Sorry, I was expecting the location of your meeting. Would you like to send it? If not, tap Cancel to exit."
    )


# --- Meeting edit: participants ---


class MeetingEditParticipantsMessages(MessageBase):
    DESCRIPTION = (
        "Here you will be able to manage the participants of the meeting: you can set the "
        "maximum number of people that can attend the meeting as well as kick out any of the "
        "participants that joined the meeting."
    )
    MAX_PROMPT = (
        "Send me the maximum number of members allowed in the meeting (must be a number greater than 0) "
        "or press in <i>No limit</i> to allow an unlimited number of participants."
    )
    # Shown instead of MAX_PROMPT when the owner's plan caps participant capacity: promising
    # "unlimited" would be false, since the effective capacity clamps to the plan's cap.
    MAX_PROMPT_CAPPED = (
        "Send me the maximum number of members allowed in the meeting (must be a number greater than 0) "
        "or press <i>Max (${cap})</i> to allow your plan's maximum of ${cap} participants."
    )
    MAX_SUCCESS = "Max participants set to: <b>${max_participants}</b>"
    NO_LIMIT_LABEL = "No limit"
    MAX_INVALID = "The maximum number of participants must be a number greater than 0. Please, try again"
    KICK_OUT_DESCRIPTION = "These are the users that joined the meeting. Choose who you want to kick out."
    KICK_OUT_CONFIRMATION = (
        "Are you sure you want to kick out <b>${participant}</b> from the meeting <b>${meeting_title}</b>?"
    )
    KICK_OUT_NOT_IN_MEETING = "The participant you tried to kick out is no longer in the meeting."
    KICK_OUT_SUCCESS = "<b>${participant}</b> removed."
    KICK_OUT_SUCCESS_NO_MORE = "<b>${participant}</b> removed. No more participants to manage."
    MAX_ON_EXIT = (
        "Sorry, I was expecting the maximum number of participants. "
        "Would you like to send it? If not, tap Cancel to exit."
    )


# --- Meeting edit: language / settings ---


class MeetingEditLanguageMessages(MessageBase):
    DESCRIPTION = (
        "Choose the language of your meeting. This will change the language used when sharing the meeting.\n\n"
        "Current language: <b>${language}</b>."
    )
    # NOTE: shares the English "Language set." with SettingsMessages.LANGUAGE_SUCCESS
    # by design — distinct namespace, do not de-dup in the catalogs.
    SUCCESS = "Language set."


class MeetingEditSettingsMessages(MessageBase):
    DESCRIPTION = (
        "You can configure different aspects of your meeting:\n\n"
        "<b>Waiting list</b>: allow users to join the meeting even when it is full. "
        "Users joining when it is full will be added to a waiting list and added to the participants "
        "list as soon as a spot is available in the order they joined.\n\n"
        "<b>Public</b>: activate this to allow everyone that receives the meeting to share it again. "
        "Perfect to reach more people.\n\n"
        "<b>Open invitations</b>: activate this option to allow users who have joined the meeting to add friends "
        "even if those friends are not in Telegram.\n\n"
        "<b>Incognito</b>: a meeting with incognito enabled won't show the people that joined the meeting when shared. "
        "Only the number of participants will be shown. You will still be able to see the participants."
    )


# --- Meeting edit: date & time ---


class MeetingEditDateTimeMessages(MessageBase):
    DESCRIPTION = (
        "Choose what you want to set for your meeting.\n\n"
        "<i>Tip: you can also send a message using ${datetime_link} to set the date and time at once.</i>"
    )
    DATE_EDIT_PROMPT = (
        f"Select the new date. Press <b>{ButtonMessages.DELETE_DATE}</b> if you want to "
        "unset the date and time of the meeting."
    )
    DATE_ADD_PROMPT = "Select the date."
    # Shown alongside CommonMessages.TIME_PROMPT in the time-first flow
    TIME_DATE_DEFAULT_NOTE = "The date defaults to today, but you can set a different one."
    # Sets date AND prompts for time
    DATE_ADDED_TIME_PROMPT = (
        "The date has been set to <b>${datetime}</b>. "
        "The time defaults to 23:59, just before midnight.\n\n"
        "Send a different time in <i>HH:MM</i> format, or tap Done to keep 23:59."
    )
    DATE_UPDATED = "Date set to: ${datetime}"
    TIME_SUCCESS = "Time set to ${datetime}"
    ON_EXIT = (
        "Sorry, I was expecting the new time for your meeting. Would you like to send it? If not, tap Cancel to exit."
    )
    END_CLEARED_BY_START = "The new start time is after the end time, so the end time has been cleared."
    START_IN_PAST = "The start time must be in the future."


# --- Meeting edit: when (start/end times sub-screen) ---


class MeetingEditWhenMessages(MessageBase):
    DESCRIPTION_NO_TIMES = (
        "Set when your meeting happens. You can set a start time, an end time, or both. "
        "You can also lock the attendees list when the meeting starts, "
        "so no one can join or leave once it is underway."
    )
    DESCRIPTION_START_ONLY = (
        "Meeting starts at: <b>${start_datetime}</b>\n\n"
        "You can set an end time or lock the attendees list when the meeting starts, "
        "so no one can join or leave once it is underway."
    )
    DESCRIPTION_BOTH = (
        "Meeting starts at: <b>${start_datetime}</b>\n"
        "Meeting ends at: <b>${end_datetime}</b>\n\n"
        "You can change the times or lock the attendees list when the meeting starts, "
        "so no one can join or leave once it is underway."
    )
    CLEAR_CONFIRMATION = "Are you sure you want to clear the start and end times?"
    CLEAR_DECLINED = "The times won't be cleared."
    CLEAR_SUCCESS = "Times removed."


# --- Meeting edit: duration (end-time sub-screen) ---


class MeetingEditDurationMessages(MessageBase):
    END_PROMPT = (
        "Meeting starts at <b>${start_datetime}</b>. Set when it ends.\n\n"
        "<i>Tip: you can also send a message using ${datetime_link} to set the date and time at once.</i>"
    )
    END_EDIT_PROMPT = (
        "Meeting starts at <b>${start_datetime}</b>. The meeting currently ends at <b>${end_datetime}</b>.\n\n"
        "Set the new end time.\n\n"
        "<i>Tip: you can also send a message using ${datetime_link} to set the date and time at once.</i>"
    )
    END_BEFORE_START = "The end time must be after the start time."
    END_IN_PAST = "The end time must be in the future."
    # Sets end date AND prompts for time
    END_DATE_ADDED_TIME_PROMPT = (
        "The end date has been set to <b>${datetime}</b>. "
        "The time defaults to 23:59.\n\n"
        "Send the time in <i>HH:MM</i> format to change it, or tap Done to keep 23:59."
    )
    END_STALE_ALERT = "The start time was cleared. Set it again before choosing an end time."
    ON_EXIT = "Sorry, I was expecting the end time. Would you like to send it? If not, tap Cancel to exit."


# --- Inline query results ---


class InlineQueryMessages(MessageBase):
    CREATE_MEETING_BUTTON = f"{Emojis.NEW_MEETING} Create a new meeting"
    EXPLORE_BUTTON = f"{Emojis.ROCKET} Explore Mitup"
    CHAT_MEETINGS_TITLE = f"{Emojis.SEARCH} Meetings in this chat"
    CHAT_MEETINGS_DESCRIPTION = "Search for meetings shared in this chat"
    CHAT_MEETINGS_MESSAGE = "Tap the button below to load meetings shared in this chat."
    READY_TO_SEARCH_MESSAGE = "Tap the button below to search for meetings shared in this chat."
    NO_RESULTS_TITLE = "No meetings found"
    NO_RESULTS_DESCRIPTION = "No meetings have been shared in this chat yet"
    NO_RESULTS_MESSAGE = "<i>No meetings have been shared in this chat yet.</i>"
    MEETING_UNAVAILABLE_TITLE = f"{Emojis.CANCEL} Meeting no longer available"
    MEETING_UNAVAILABLE_DESCRIPTION = "This meeting has been cancelled or is no longer accessible"
    MEETING_UNAVAILABLE_MESSAGE = "<i>This meeting is no longer available.</i>"


# --- Notifications ---


class NotificationMessages(MessageBase):
    DELETION_WARNING = (
        "The meeting <b>${meeting_title}</b> will be permanently deleted in <b>${days_until_deletion} days</b>.\n\n"
        "To prevent this from happening, you can reactivate the meeting by selecting "
        "the <b>${reactivate_meeting_button}</b> button below.\n\n"
        "Remember that you can always reactivate any past meeting from the"
        " <b>${past_meetings_button}</b> button in the main menu.\n\n"
        "If you do not want to reactivate the meeting, you can ignore this message."
    )
    DELETED = "The meeting <b>${meeting_title}</b> has been permanently deleted."
    STARTING_SOON = "The meeting <b>${meeting_title}</b> is starting soon!"
    STARTED = "The meeting <b>${meeting_title}</b> has started!"


class CollaborateMessages(MessageBase):
    # Collaborate view, shown when Patreon is configured and the account is not linked yet.
    NOT_LINKED = (
        "<b>Support Mitup with Patreon</b>\n\n"
        "Mitup is free to use. If it helps you bring people together, you can back it on Patreon "
        "and unlock Host perks as a thank you.\n\n"
        "<b>As a Gamemaster:</b> run up to ${active_meetings} active meetings at once, "
        "schedule up to ${scheduling_days} days ahead, and invite unlimited participants per meeting.\n\n"
        "<b>As a Commissioner:</b> unlimited everything.\n\n"
        "Link your Patreon account to get started."
    )
    # Collaborate view, shown when the account is linked but not an active Host of the campaign.
    LINKED_NOT_PATRON = (
        "<b>Patreon account linked</b>\n\n"
        "You're all set. You're not a Host yet, so your Host perks are still off.\n\n"
        "<b>As a Gamemaster:</b> run up to ${active_meetings} active meetings at once, "
        "schedule up to ${scheduling_days} days ahead, and invite unlimited participants per meeting.\n\n"
        "<b>As a Commissioner:</b> unlimited everything.\n\n"
        "Become a Host and your perks turn on automatically, with no need to link again."
    )
    # Collaborate view, shown to a linked active Brewer.
    LINKED_PATRON_SUPPORTER = (
        f"<b>{Emojis.HOST_1} You're a Brewer</b>\n\n"
        "Thanks for backing Mitup. Your Brewer badge shows on your profile, and your support "
        "helps keep Mitup running for everyone. You can unlink your Patreon account whenever you want."
    )
    # Collaborate view, shown to a linked active Gamemaster.
    LINKED_PATRON_PATRON = (
        f"<b>{Emojis.HOST_2} You're a Gamemaster</b>\n\n"
        "Thanks for backing Mitup. Your Gamemaster badge is on, and you can run up to ${active_meetings} "
        "active meetings at once, schedule up to ${scheduling_days} days ahead, and invite unlimited "
        "participants per meeting. You can unlink your Patreon account whenever you want."
    )
    # Collaborate view, shown to a linked active Commissioner.
    LINKED_PATRON_ORGANIZER = (
        f"<b>{Emojis.HOST_3} You're a Commissioner</b>\n\n"
        "Thanks for backing Mitup. Your Commissioner badge is on, and every limit is off: "
        "run as many meetings as you want, schedule them as far ahead as you need, "
        "and invite as many people as you like to each one. You can unlink your Patreon account whenever you want."
    )
    # Collaborate view, shown when the bot runs without a [patreon] config section.
    UNAVAILABLE = "<b>Collaborate</b>\n\nPatreon support isn't available yet. Check back soon."
    # Context line edited onto the view right after the user unlinks.
    UNLINKED = "Your Patreon account has been unlinked."
    # Telegram message sent from the OAuth callback when the linked user is not a Host yet.
    LINK_CONFIRMED_NO_PATRON = (
        "<b>Patreon account linked</b>\n\n"
        "You're connected. Become a Host to unlock your Host perks. They'll switch on automatically "
        "once your pledge is active."
    )

    @classmethod
    def status_for(cls, level: SupporterLevel) -> CollaborateMessages:
        """The persistent status screen message for a linked, active paying tier.

        NONE never reaches here: a non-Host never sees the linked Host screen, so it raises
        rather than inventing copy for the free tier."""
        match level:
            case SupporterLevel.HOST_1:
                return cls.LINKED_PATRON_SUPPORTER
            case SupporterLevel.HOST_2:
                return cls.LINKED_PATRON_PATRON
            case SupporterLevel.HOST_3:
                return cls.LINKED_PATRON_ORGANIZER
            case SupporterLevel.NONE:
                raise ValueError("No status message exists for the NONE tier")
            case _ as unreachable:
                assert_never(unreachable)


class SupporterNotificationMessages(MessageBase):
    """Direct messages sent by the daily Host-validation job when a Host's status changes.

    Full messages (not callback-query alerts), so inline `<b>` tags are allowed. Each mirrors a
    transition in the grace/upgrade/revoke flow the job drives against Patreon."""

    # Support lapsed while we could not confirm an active pledge: perks stay on for one grace week.
    GRACE_STARTED = (
        "<b>Your Host perks are in a grace period</b>\n\n"
        "We couldn't confirm an active Patreon pledge on your account, so your Host perks are set "
        "to switch off in a week. If your pledge is still active, there's nothing to do and it will "
        "renew automatically. Otherwise, you can back Mitup again from the Collaborate menu."
    )
    # Webhook told us the user is no longer an active Host: perks stay on for a parameterized
    # grace period (the daily job revokes only if they're still lapsed when it ends).
    SUPPORT_ENDED_GRACE = (
        "<b>Your Host perks are in a grace period</b>\n\n"
        "Your Patreon support ended, and we're sad to see you go. Your Host perks stay on for "
        "${days} days in case you change your mind. To keep them, back Mitup again from the "
        "Collaborate menu."
    )
    # Grace week elapsed without a confirmed pledge: perks are now off.
    SUPPORT_LOST = (
        "<b>Your Host perks have ended</b>\n\n"
        "We still couldn't confirm an active Patreon pledge, so your Host perks are now off. "
        "Become a Host again from the Collaborate menu to turn them back on."
    )
    # Reached the Brewer tier: grants the badge and our thanks, but no raised limits, so the copy
    # stays on the badge and the support rather than promising bigger or more meetings.
    SUPPORTER_UNLOCKED = (
        f"<b>{Emojis.HOST_1} You're a Brewer</b>\n\n"
        "Thanks for backing Mitup. Your Brewer badge now shows on your profile, and your support "
        "helps keep Mitup running for everyone."
    )
    # Reached the Gamemaster tier: raised active-meeting cap, further scheduling horizon, and unlimited
    # participants per meeting, plus the badge.
    PATRON_UNLOCKED = (
        f"<b>{Emojis.HOST_2} You're a Gamemaster</b>\n\n"
        "Thanks for backing Mitup. Your Gamemaster badge is on, and your limits just went up: run more "
        "meetings at once, schedule them further ahead, and invite as many people as you like to each one."
    )
    # Reached the Commissioner tier: every limit lifted (active meetings, scheduling horizon,
    # participants), plus the badge.
    ORGANIZER_UNLOCKED = (
        f"<b>{Emojis.HOST_3} You're a Commissioner</b>\n\n"
        "Thanks for backing Mitup. Your Commissioner badge is on, and every limit is off: run as many "
        "meetings as you want, schedule them as far ahead as you need, and invite as many people as "
        "you like to each one."
    )

    # Settled at the Brewer tier after a between-tier change (still a paying Host, lower tier).
    # Neutral by design: it states the current tier and its standard limits without framing the move
    # as a loss or comparing it to the prior tier. Brewer carries the badge and our thanks, but no
    # raised limits, so the copy names the standard limits rather than promising bigger or more.
    SUPPORTER_TIER_SET = (
        f"<b>{Emojis.HOST_1} Your tier is now Brewer</b>\n\n"
        "Your Brewer badge stays on your profile, and the standard meeting limits apply: a set "
        "number of active meetings at once, the standard scheduling window, and the standard "
        "participant cap per meeting. Thanks for backing Mitup."
    )
    # Settled at the Gamemaster tier after a between-tier change. Neutral by design (see SUPPORTER_TIER_SET):
    # it states the current tier and what it includes without framing the move as a loss. Gamemaster carries
    # a raised active-meeting cap, a further scheduling horizon, and unlimited participants, plus the badge.
    PATRON_TIER_SET = (
        f"<b>{Emojis.HOST_2} Your tier is now Gamemaster</b>\n\n"
        "Your Gamemaster badge is on, and your Gamemaster limits apply: run more meetings at once, schedule "
        "them further ahead, and invite as many people as you like to each one. Thanks for backing Mitup."
    )

    # Support lapsed for a Host who was in the members-only group: the job bans them and sends this DM.
    HOSTS_GROUP_REMOVED = (
        "<b>Your Hosts-Only Group access has ended</b>\n\n"
        "Your Host support has come to an end, and we're grateful for the time you backed Mitup. "
        "We've removed you from the Hosts-Only Group for now. Whenever you become a Host again, "
        "you can rejoin it from the Collaborate menu."
    )
    # A previously removed member became a Host again: the job unbans them and sends this DM. They
    # aren't added back automatically, so the DM carries a direct join button that the copy points to.
    HOSTS_GROUP_READMITTED = (
        "<b>Your Host access is back</b>\n\n"
        "Welcome back. Your Host perks are on again, and you're welcome in the Hosts-Only Group. "
        "Tap below to rejoin."
    )

    @classmethod
    def unlocked_for(cls, level: SupporterLevel) -> SupporterNotificationMessages:
        """The per-tier unlock message for a newly active paying tier.

        NONE never reaches here: callers only announce an unlock once a paying tier is confirmed, so
        it raises rather than inventing copy for the free tier."""
        match level:
            case SupporterLevel.HOST_1:
                return cls.SUPPORTER_UNLOCKED
            case SupporterLevel.HOST_2:
                return cls.PATRON_UNLOCKED
            case SupporterLevel.HOST_3:
                return cls.ORGANIZER_UNLOCKED
            case SupporterLevel.NONE:
                raise ValueError("No unlock message exists for the NONE tier")
            case _ as unreachable:
                assert_never(unreachable)

    @classmethod
    def downgraded_to(cls, level: SupporterLevel) -> SupporterNotificationMessages:
        """The neutral tier-set message for a between-tier downgrade (still a paying Host).

        Only the lower paying tiers are valid targets: HOST_3 is the top tier so nothing downgrades
        *to* it, and a drop to NONE is a full loss handled by the grace/revoke messages, not here."""
        match level:
            case SupporterLevel.HOST_1:
                return cls.SUPPORTER_TIER_SET
            case SupporterLevel.HOST_2:
                return cls.PATRON_TIER_SET
            case SupporterLevel.HOST_3:
                raise ValueError("HOST_3 is the top tier, not a downgrade target")
            case SupporterLevel.NONE:
                raise ValueError("A drop to NONE is a loss, not a downgrade")
            case _ as unreachable:
                assert_never(unreachable)


class Weekday(MessageBase):
    MONDAY = "Mon"
    TUESDAY = "Tue"
    WEDNESDAY = "Wed"
    THURSDAY = "Thu"
    FRIDAY = "Fri"
    SATURDAY = "Sat"
    SUNDAY = "Sun"


class Month(MessageBase):
    JANUARY = "January"
    FEBRUARY = "February"
    MARCH = "March"
    APRIL = "April"
    MAY = "May"
    JUNE = "June"
    JULY = "July"
    AUGUST = "August"
    SEPTEMBER = "September"
    OCTOBER = "October"
    NOVEMBER = "November"
    DECEMBER = "December"


class MonthShort(MessageBase):
    JANUARY = "Jan"
    FEBRUARY = "Feb"
    MARCH = "Mar"
    APRIL = "Apr"
    MAY = "May"
    JUNE = "Jun"
    JULY = "Jul"
    AUGUST = "Aug"
    SEPTEMBER = "Sep"
    OCTOBER = "Oct"
    NOVEMBER = "Nov"
    DECEMBER = "Dec"


class Languages(MessageBase):
    SPANISH = "🇪🇸 Spanish"
    GALICIAN = "🇪🇸 Galician"
    ENGLISH = "🇺🇸 English"
    GERMAN = "🇩🇪 German"
    PORTUGUESE = "🇧🇷 Portuguese"
    ITALIAN = "🇮🇹 Italian"


MonthList = [
    Month.JANUARY,
    Month.FEBRUARY,
    Month.MARCH,
    Month.APRIL,
    Month.MAY,
    Month.JUNE,
    Month.JULY,
    Month.AUGUST,
    Month.SEPTEMBER,
    Month.OCTOBER,
    Month.NOVEMBER,
    Month.DECEMBER,
]
MonthShortList = [
    MonthShort.JANUARY,
    MonthShort.FEBRUARY,
    MonthShort.MARCH,
    MonthShort.APRIL,
    MonthShort.MAY,
    MonthShort.JUNE,
    MonthShort.JULY,
    MonthShort.AUGUST,
    MonthShort.SEPTEMBER,
    MonthShort.OCTOBER,
    MonthShort.NOVEMBER,
    MonthShort.DECEMBER,
]


# --- Broadcast (operator-only) ---
# Operator-facing strings for the mass-broadcast authoring flow and the sender's finalization DMs.
# English-only is acceptable: only allowlisted admins ever see them, and any missing locale falls
# back to English. Placeholders live in plain (non-f) string segments so `${...}` is never eaten by
# f-string interpolation; leading emojis go in adjacent f-string segments.
class BroadcastOperatorMessages(MessageBase):
    UPLOAD_PROMPT = (
        "<b>New broadcast</b>\n\n"
        "Upload a YAML file, or paste the YAML directly as a message.\n\n"
        "It must be a list of entries, each with a <code>language</code> code and an HTML "
        "<code>message</code>. English (<code>en</code>) is required as the fallback."
    )

    # Validation errors. Each keeps the operator on the upload step so they can fix and resend.
    ERROR_INVALID_YAML = (
        f"{Emojis.PROHIB} I could not parse that as YAML.\n\n<code>${{detail}}</code>\n\nFix it and send it again."
    )
    ERROR_NOT_A_LIST = f"{Emojis.PROHIB} The content must be a list of entries, each with a language and a message."
    ERROR_EMPTY_LIST = f"{Emojis.PROHIB} The list is empty. Add at least an English entry."
    ERROR_ENTRY_SHAPE = (
        f"{Emojis.PROHIB} "
        "Entry ${position} is not valid. Each entry needs exactly two fields: "
        "<code>language</code> and <code>message</code>, both text."
    )
    ERROR_DUPLICATE_LANGUAGE = (
        f"{Emojis.PROHIB} Language <b>${{language}}</b> appears more than once. Keep one entry per language."
    )
    ERROR_MISSING_ENGLISH = (
        f"{Emojis.PROHIB} "
        "English (<code>${language}</code>) is required as the fallback. Add an <code>en</code> entry."
    )
    ERROR_EMPTY_MESSAGE = f"{Emojis.PROHIB} The message for <b>${{language}}</b> is empty."
    ERROR_MESSAGE_TOO_LONG = (
        f"{Emojis.PROHIB} "
        "The message for <b>${language}</b> is ${length} characters, over the limit of ${limit}. "
        "Shorten it and resend."
    )
    ERROR_DOCUMENT_TOO_LARGE = f"{Emojis.PROHIB} That file is too large. The limit is ${{limit_kb}} KB."
    ERROR_DOCUMENT_DECODE = f"{Emojis.PROHIB} I could not read that file as UTF-8 text. Save it as UTF-8 and resend."

    # Preview and summary.
    # Header shown once, before the labelled per-language previews.
    PREVIEW_HEADER = "<b>Broadcast preview</b>\n\nHere is exactly what each language will receive."
    # Bold language name shown right before that language's rendered preview message.
    PREVIEW_LANGUAGE_LABEL = "<b>${language}</b>"
    PREVIEW_SUMMARY_HEADER = "<b>Summary</b>"
    PREVIEW_SUMMARY_LINE = "<b>${language}</b>: ${char_count} chars, ${recipient_count} recipients"
    PREVIEW_TOTAL_RECIPIENTS = "<b>Total recipients:</b> ${total}"
    PREVIEW_WARNINGS_HEADER = f"{Emojis.THINK} Skipped unknown languages:"
    PREVIEW_WARNING_LINE = "- ${language}"
    PREVIEW_FOOTER = "Confirm to queue this broadcast, or Cancel to discard it."

    # Buttons.
    BUTTON_CONFIRM = f"{Emojis.CHECK} Confirm"
    BUTTON_CANCEL = f"{Emojis.CANCEL} Cancel"

    # Outcomes.
    QUEUED_CONFIRMATION = (
        f"{Emojis.CHECK} Broadcast <b>${{name}}</b> (<code>#${{broadcast_id}}</code>) is queued. "
        "It will start sending shortly."
    )
    CANCELLED_CONFIRMATION = f"{Emojis.CANCEL} Broadcast discarded. Nothing was sent."
    DRAFT_NOT_FOUND = f"{Emojis.THINK} This draft is no longer available. Send /broadcast to start again."

    # Sender finalization DMs. Referenced by the sender phase, which does not edit this file.
    SENDER_COMPLETE_SUMMARY = (
        f"{Emojis.CHECK} "
        "Broadcast <b>${name}</b> (<code>#${broadcast_id}</code>) finished.\n\n"
        "<b>Total:</b> ${total} · <b>Sent:</b> ${sent} · <b>Failed:</b> ${failed} · "
        "<b>Skipped:</b> ${skipped}\n\n${breakdown}"
    )
    SENDER_BREAKDOWN_LINE = "<b>${language}</b>: ${sent} sent, ${failed} failed, ${skipped} skipped"
    SENDER_BREAKDOWN_LINE_WITH_ORPHANED = (
        "<b>${language}</b>: ${sent} sent, ${failed} failed, ${skipped} skipped, ${orphaned} orphaned"
    )
    # Shown once, appended below the summary/breakdown, only when at least one delivery was left
    # IN_PROGRESS by a worker that crashed between claiming it and recording its outcome.
    SENDER_ORPHANED_WARNING = (
        f"{Emojis.PROHIB} "
        "<b>${orphaned}</b> deliveries were interrupted by a worker crash and their outcome is "
        "unknown. They will not be retried."
    )
    SENDER_FAILED = (
        f"{Emojis.PROHIB} "
        "Broadcast <b>${name}</b> (<code>#${broadcast_id}</code>) failed after ${attempts} attempts.\n\n"
        "<b>Sent:</b> ${sent} · <b>Failed:</b> ${failed} · <b>Skipped:</b> ${skipped}\n\n"
        "Check the logs for details."
    )


# --- Admin (operator-only) ---
# Operator-facing strings for the admin menu. English-only is acceptable: only allowlisted
# admins ever see them, and any missing locale falls back to English.
class AdminMessages(MessageBase):
    MENU_DESCRIPTION = "<b>Admin</b>\n\nOperator actions for running the bot."
    BUTTON_ADMIN = f"{Emojis.TOOLS} Admin"
    BUTTON_BROADCAST = f"{Emojis.SHARE} Broadcast"
