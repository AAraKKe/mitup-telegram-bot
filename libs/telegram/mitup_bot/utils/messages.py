from __future__ import annotations

from enum import StrEnum
from typing import Protocol, assert_never

from mitup_bot.emojis import Emojis
from mitup_bot.supporter import SupporterLevel
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils.rich_message import RichContent
from mitup_bot.utils.rich_template import RichParams, render_rich_template


class FormattedMessageAsText(ValueError):
    def __init__(self, message_id: str):
        super().__init__(
            f"Message {message_id} renders with formatting, which plain text cannot carry. "
            "A caller that wants its words gives up the formatting at the call site, with `rich(...).text`."
        )


class TranslationEngineProtocol(Protocol):
    @classmethod
    def translate(cls, message_id: str, lang: str) -> str: ...


class MessageBase(StrEnum):
    def rich(self, *, lang: str = TranslationEngine.FALLBACK_LANG, **kwargs: RichParams) -> RichContent:
        """Return the translated message as rich-HTML content, with every `${var}` substituted.

        Strict about both defects a catalog string can carry: a placeholder given no value raises
        instead of reaching the reader as a literal `${var}`, and a formatting tag with no rich
        equivalent raises instead of being dropped along with the emphasis it carried.
        """
        return render_rich_template(self.to_lang(lang), kwargs)

    def text(self, *, lang: str = TranslationEngine.FALLBACK_LANG, **kwargs: RichParams) -> str:
        """Return the translated message as plain text, with every `${var}` substituted.

        For the surfaces Telegram gives no formatting at all: button labels, callback-query
        alerts, inline-picker titles. Substitution runs through `rich`, so a placeholder given no
        value raises here too rather than shipping the literal `${var}`. Content that renders with
        any formatting raises as well, since a plain string keeps none of it: a caller who wants
        the words of a formatted message gives up the formatting at the call site by asking for
        `rich(...).text`.
        """
        content = self.rich(lang=lang, **kwargs)
        if not content.is_plain:
            raise FormattedMessageAsText(self.id())
        return content.text

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
    ACTIVE_MEETINGS = f"{Emojis.LIST} Active meetings"
    PAST_MEETINGS = f"{Emojis.PAST} Past meetings"
    JOINED_MEETINGS = f"{Emojis.JOINED} Joined meetings"
    # Short forms for the main menu's three-chip row; the full labels serve every other screen.
    ACTIVE_MEETINGS_CHIP = f"{Emojis.LIST} Active"
    PAST_MEETINGS_CHIP = f"{Emojis.PAST} Past"
    JOINED_MEETINGS_CHIP = f"{Emojis.JOINED} Joined"
    # A template so a language can put the count before the label.
    COUNTED_CHIP = "${label} · ${count}"
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
    USER_GUIDE = f"{Emojis.BOOK} User guide"
    COMMUNITY_GROUP = f"{Emojis.PEOPLE} Community group"
    NEWS_CHANNEL = f"{Emojis.NEWS} News channel"
    # Bare verbs: each rides a section title that already names what it acts on.
    READ = "Read"
    EXPORT = "Export"
    OPEN = "Open"
    CHANGE = f"{Emojis.EDIT} Change"
    WAITING_LIST = "Waiting list"
    PUBLIC = "Public"
    OPEN_INVITATION = "Open invitations"
    INCOGNITO = "Incognito"
    SHOW_TIMEZONE = "Show timezone"
    CLOCK_24H = "24-hour clock"
    DATE_FORMAT = "Date format"
    # The date formats as chips on the Date format line, the one in force accented.
    DATE_FORMAT_DEFAULT = "Default"
    DATE_FORMAT_LONG = "Long"
    DATE_FORMAT_FULL = "Full"
    # Toggle-chip states: the label names the CURRENT state, tapping flips it
    ENABLED = "Enabled"
    DISABLED = "Disabled"

    # Meeting buttons
    # The back-button label every meeting sub-screen carries: it names the card the tap returns to.
    MEETING = "Meeting"
    CANCEL = f"{Emojis.CANCEL} Cancel"
    DATE_TIME = f"{Emojis.CALENDAR} Date & Time"
    END_DATE_TIME = f"{Emojis.CALENDAR} End Date & Time"
    JOIN = f"{Emojis.CHECK} Join"
    # Stands where Join would, on a meeting nobody can join. It reports the meeting's state rather
    # than naming an action, which is what the inert chip carrying it is for.
    FULL = f"{Emojis.PROHIB} Full"
    INVITE = f"{Emojis.FRIEND} Invite"
    LEAVE = f"{Emojis.CANCEL} Leave"
    OPEN_MEETING = f"{Emojis.LIST} Open meeting"
    # Rides the title line of a meeting in a list, where the title already names what opens.
    DELETE = f"{Emojis.DELETE} Delete"
    EDIT = f"{Emojis.EDIT} Edit"
    SHARE = f"{Emojis.SHARE} Share"
    # Redraws the card in place with the meeting's current state.
    REFRESH = f"{Emojis.ACTIVATE} Refresh"
    MEETING_KICK_OUT = "Kick out"

    # Meeting editor inline chips. A "+" marks a field still unset; the pencil marks one already
    # filled, whose chip re-opens the same editor.
    ADD_DESCRIPTION = "+ Add description"
    SET_DATETIME = "+ Set date & time"
    SELECT_DATE_FIRST = "Select a date first"
    REMOVE_DATETIME = "Remove date & time"
    REMOVE_END_DATETIME = "Remove end time"
    SET_END_DATETIME = "+ Set end time"
    SET_LOCATION_NAME = "+ Set name"
    SET_COORDINATES = "+ Set map pin"
    SET_PARTICIPANT_LIMIT = "Set limit"
    CHANGE_PARTICIPANT_LIMIT = "Change limit"
    REMOVE = "Remove"
    ADD_IMAGES = "+ Add images"
    EDIT_IMAGES = f"{Emojis.EDIT} Edit images"
    # Images screen chips: Replace acts on one photo, Remove all on every photo.
    REPLACE = "Replace"
    REMOVE_ALL = "Remove all"
    # The layout buttons on the Images screen; the current layout is highlighted.
    COLLAGE = "Collage"
    SLIDESHOW = "Slideshow"

    MAKE_SEARCHABLE = "Make it searchable"
    LOAD_CHAT_MEETINGS = f"{Emojis.SEARCH} Load meetings"
    SEARCH_CHAT_MEETINGS = f"{Emojis.SEARCH} Search meetings"

    LOCK_ON_START = "Lock on start"

    # Settings — Default Options buttons
    MEETING_DURATION = f"{Emojis.HOURGLASS} Meeting duration"

    # Notification buttons
    REACTIVATE_MEETING = "Reactivate meeting"

    # Collaborate / Patreon buttons
    LINK_PATREON = f"{Emojis.HEART} Link Patreon account"
    BECOME_PATRON = f"{Emojis.DONATE} Become a Host"
    # Chip on the section title that names the linked account.
    UNLINK = "Unlink"
    # Chips inside the Collaborate sentences, each opening a docs page.
    COLLABORATE_PAGE = f"{Emojis.BOOK} Ways to support"
    LIMITS_PAGE = f"{Emojis.CHART} Limits and perks"
    # The link-confirmation pair. The confirming label names the act rather than reading as the
    # flow's natural "next", so tapping it is a decision instead of a reflex, and the declining
    # label is a neutral way out rather than one that sounds like abandoning setup.
    CONFIRM_PATREON_LINK = "Connect this account"
    DECLINE_PATREON_LINK = "Not now"
    # The unlink-confirmation pair, in the same register as the link pair above: the confirming
    # label names the act so tapping it is a decision instead of a reflex, and the declining label
    # is the way back that keeps things exactly as they are.
    CONFIRM_PATREON_UNLINK = "Unlink this account"
    DECLINE_PATREON_UNLINK = "Keep it connected"
    HOSTS_GROUP_JOIN = f"{Emojis.PEOPLE} Join the Hosts-Only Group"
    # Chip on the Hosts-Only Group section; OPEN is its counterpart for a host already in it.
    JOIN_GROUP = "Join"

    def back(self, lang: str, **kwargs: str) -> str:
        return f"{self.GO_BACK} {self.text(lang=lang, **kwargs)}"


# --- Main menu and meeting lists ---


class MainMenuMessages(MessageBase):
    WELCOME = "Welcome to Mitup!"
    TAGLINE = "Create meetings and share them with your friends."
    MEETINGS_SECTION = "Meetings"
    ACCOUNT_SECTION = "Account"


class HelpMessages(MessageBase):
    INTRO = "Where to get help with Mitup:"
    # Each button placeholder is the channel's url chip, riding the sentence as its noun.
    USER_GUIDE = "The ${button_guide} walks through every screen, page by page. The best place to start."
    COMMUNITY_GROUP = "The ${button_group} is open to anyone who uses Mitup. Come in and ask anything."
    NEWS_CHANNEL = "The ${button_channel} announces every release and what changed. Follow it to hear first."
    # Plain text: Telegram rejects a mailto: URL on a button and clients ignore a mailto: anchor.
    EMAIL = f"{Emojis.MAIL} For anything private, write to support@mitup.social. A person reads it."


class MeetingListMessages(MessageBase):
    # Callback-query alerts, which Telegram caps at 200 characters and renders without formatting.
    ACTIVE_EMPTY_ALERT = "You have no active meetings. Tap New meeting to create one."
    JOINED_EMPTY_ALERT = "You have not joined any meeting yet."
    PAST_EMPTY_ALERT = "You have no past meetings yet."


# --- Cross-screen / shared messages ---


class CommonMessages(MessageBase):
    # Stale cancel button alert
    STALE_CANCEL_ALERT = "You've already answered this question"
    # Shown above the editor when a button no current screen renders brought the user here, so
    # the unfamiliar screen explains itself.
    EDITING_REVAMP_BANNER = f"{Emojis.SPARKLES} Editing got a revamp! Everything now happens right on the meeting card."
    # Positive-integer validation for flows without an upper bound (notification time)
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
    DATETIME_INPUT_INVALID = (
        "I could not read a date or time in that message. Pick a day from the calendar, send the time in "
        "<i>HH:MM</i> format, or send a message using ${datetime_link} to set the date and time at once."
    )
    # Raised from guards.py across access paths
    DELETED_MEETING_ALERT = "This meeting has been deleted"
    # Generic error when an internal guard check fails
    UNEXPECTED_ERROR = "Oops! Something unexpected happened. I've brought you back to the main menu. Please try again."
    # The same failure answered where no screen can be rebuilt: a card tapped in somebody else's
    # chat. It ships as a plain callback-query alert (max 200 characters), so it must stay
    # entity-free, and it promises nothing about where the caller is left. The address is written
    # bare because Telegram alerts render no links.
    UNEXPECTED_ERROR_ALERT = (
        "Something went wrong on our side. Please try again, and write to support@mitup.social if it keeps happening."
    )
    # Shown in the caller's own chat with the bot when their account row is gone: it replaces the
    # tapped screen, or arrives as a reply to a message. /start is written out so Telegram makes it
    # tappable, since no button the caller can press would work.
    ACCOUNT_NOT_FOUND = "You don't have a Mitup account right now. Send /start and I'll set you up."
    # Answers a tap from a caller who blocked the bot, as a plain callback-query alert
    # (max 200 characters), so it must stay entity-free.
    BOT_BLOCKED_ALERT = (
        "You have blocked me, so I can't send you messages. To use Mitup again, unblock me and send /start."
    )
    # Shown when in-memory conversation state was lost (e.g. after a rolling deploy mid-flow)
    CONTEXT_LOST = (
        "<b>Sorry, we lost our place!</b>\n\nThe bot had a quick internal update and forgot where you "
        "were in the process. Everything you saved is safe. Please start what you were doing again from "
        "the menu below."
    )
    # Answers a tap on a button rendered by the bot Mitup replaced. It is shown both as a screen
    # (with a main-menu button under it) and as a plain callback-query alert, so it must stay
    # entity-free, must not point at a keyboard that is not always there, and has to leave room for
    # translation inside the 200-character alert limit.
    OLD_VERSION_MESSAGE = (
        "This message is from an older version of Mitup, so its buttons no longer work. "
        "Your account and meetings are safe. Open the main menu to keep going."
    )
    # Takes over a conversation prompt whose flow already ended when one of its buttons is tapped:
    # rendered as the context line above the main menu that replaces the prompt.
    STALE_BUTTONS_NOTICE = (
        "These buttons are from an old message and I no longer remember what we were doing there. "
        "I've turned this message into the main menu, so you can keep going from here."
    )
    # Shown when a user sends a Telegram rich message where plain text is expected (title,
    # description, location name, timezone) or with no flow open. Rich messages are never
    # accepted as input.
    RICH_MESSAGE_NOT_SUPPORTED = (
        "<b>That's a rich message!</b>\n\nI can tell you sent one, but I can't work with rich messages, "
        "since meeting pages keep their own formatting. Send your text as a plain message and we'll keep "
        "going. Any <b>bold</b> or <i>italic</i> you type comes through fine."
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
    # owners are uncapped, so there is no Gamemaster counterpart. Shown only as a sent message,
    # never as an alert, so it can carry the Collaborate button inline where the upsell names it.
    PARTICIPANT_CAPACITY_EXCEEDED = (
        "Free meetings can host up to ${cap} participants. Send a lower number, or become a Mitup "
        "Host on Patreon to host bigger meetings: ${button_collaborate}"
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
    # Registration completion: IS the main-menu message (passed as `message=` to main_menu_view),
    # so the Collaborate mention points at a button visible right below and no second welcome or
    # menu prompt follows. `${user_guide}` receives a linked "user guide" label (USER_GUIDE_LABEL).
    REGISTRATION_COMPLETE = (
        "<b>You're all set, welcome to Mitup!</b> Your timezone is set to ${timezone} and the bot "
        "is ready for you.\n\n"
        "Everything you need to create meetings and join others is in the menu below, and the "
        "${user_guide} explains how it all works.\n\n"
        "If Mitup helps you bring people together, you can support it anytime through Collaborate."
    )
    # Label for the inline user-guide link embedded in REGISTRATION_COMPLETE.
    USER_GUIDE_LABEL = "user guide"
    TIMEZONE_FAIL = "I'm sorry, I couldn't set your timezone. Please, try again."
    # Fallback shown during registration when a non-text, non-location message is sent
    TIMEZONE_INVALID_INPUT = "Please send your city name as text or share your location."


class SettingsMessages(MessageBase):
    # Timezone settings
    TIMEZONE_PROMPT = (
        "Your timezone is set to <b>${timezone}</b>.\n"
        "Send me the name of your city or your location to set your "
        "timezone or tap <b>Cancel</b> to go back."
    )
    TIMEZONE_SUCCESS = "Your timezone has been set to: <b>${timezone}</b>"
    # On-exit prompt shown when an unexpected message interrupts timezone edit
    TIMEZONE_ON_EXIT = (
        "<i>You were in the middle of changing your timezone.</i>\n"
        "Send the name of your city or your location to continue, or ${button_cancel} to exit."
    )

    # Language settings
    LANGUAGE_SUCCESS = "Language set."

    # Default meeting default options
    DEFAULT_OPTIONS_LEAD = "Every meeting you create starts with these settings."
    TOGGLE_HINT = "Tap a setting's state to turn it on or off."

    # Timeout messages
    TIMEOUT_VALUE = "A meeting without an end time stays active ${minutes} minutes after it starts."
    TIMEOUT_PROMPT = "Send the new timeout in minutes, up to <b>${max_timeout}</b> (one day), or tap Cancel to go back."
    TIMEOUT_INVALID = (
        "That doesn't look like a valid timeout. Send a positive whole number of minutes, "
        "up to <b>${max_timeout}</b> (one day). No decimals allowed!"
    )
    TIMEOUT_SUCCESS = "Timeout set to: <b>${timeout} minutes</b>"

    # Notification settings
    NOTIFICATION_LEAD = "${minutes} minutes before a meeting starts ${button_change}"
    NOTIFICATIONS_TIME_PROMPT = (
        "Send how long before a meeting starts (in minutes) you would like to be notified or touch Cancel to go back."
    )
    NOTIFICATIONS_TIME_SUCCESS = "Notification time set to <b>${notifications_time} minutes</b>."

    # Card lines for the sections that only open another screen
    DEFAULT_OPTIONS_LINE = "What every meeting you create starts with."
    PRIVACY_LINE = "Your data, the policy, export and deletion."


class PrivacyMessages(MessageBase):
    INTRO = "Your data belongs to you."
    POLICY_TITLE = "Privacy policy"
    POLICY = "What Mitup stores about you, why, and for how long."
    EXPORT_TITLE = "Your data"
    EXPORT = (
        "Download a copy of everything Mitup stores about you, as a JSON file. "
        "Other people in your meetings appear by display name only."
    )
    EXPORT_ATTACHED = "Your copy is attached to this message."
    DELETE_TITLE = "Deletion"
    DELETE = (
        "Permanently remove everything linked to your account. Meetings you created disappear for "
        "everyone who joined them."
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
        "Let's create a meeting. What is the title?\n\n"
        "<i>Tip: you can also use ${datetime_link} to include a date and time directly.</i>"
    )
    CREATED = (
        "Meeting created: <b>${title}</b>\n\n"
        "You can complete the meeting with the options on its card below. "
        "The information which has not been added won't be shown when the meeting is shared."
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
        "<i>You were in the middle of creating a meeting.</i>\n"
        "Send the title of the meeting to continue, or ${button_cancel} to exit."
    )


class MeetingCardSectionMessages(MessageBase):
    """Section titles and section-local lines on a meeting card. The view renders the titles bold;
    the count rides the Participants title line, which is what lets it carry bare numbers.

    They live in their own class so a title is free to repeat a string held elsewhere: two members
    of one class holding the same string become a single member with two names, and the second
    never reaches the catalogs as a string a translator can inflect on its own.
    """

    DESCRIPTION = "Description"
    IMAGES = "Images"
    WHEN = "When"
    WHERE = "Where"
    PARTICIPANTS = "Participants"
    COUNT_OF_MAX = "${count} of ${max}"
    # The two rows of the owner card's When section; ${when} is the moment written out.
    START_TIME = "Start time: ${when}"
    END_TIME = "End time: ${when}"


class MeetingDisplayMessages(MessageBase):
    CREATED_BY = "Created by: ${owner}"
    DATE_NOT_SET = f"{Emojis.PROHIB} No time defined {Emojis.PROHIB}"
    # Participant-count empty fragment
    PARTICIPANT_COUNT_EMPTY = "Empty"
    MAX_PARTICIPANTS_LABEL = "(Max: ${max_participants})"
    # Closes a participants list the card had to cut short. The list can collapse with no name left
    # above it, so this line reads on its own rather than referring back to the names above, and its
    # count covers hidden waiting-list entries as well as confirmed participants, which is why it
    # says names: calling them participants would overstate what the number represents.
    PARTICIPANTS_TRUNCATED = "${count} names not shown"
    INVITED_BY = "<i>invited by ${user}</i>"
    # Says where the owner stands on their own meeting, and gives the join and invite chips beside
    # it something to attach to: hosting a meeting is not attending it, and a pair of chips under
    # the attendee list with no sentence to read them against looks like a stray control.
    NOT_PART_OF_MEETING = "<i>You are not part of this meeting.</i>"
    # Shared-view empty-state sentences
    DESCRIPTION_EMPTY = "<i>This meeting has no description yet</i>"
    # Stands in for the title of a meeting whose own title has no visible text.
    UNTITLED = "<i>Untitled meeting</i>"
    # Quiet status line closing the schedule block on a shared card while the meeting runs. A
    # state, not news: it reads the same on every redraw of a long-lived card.
    IN_PROGRESS_STATUS = "<i>This meeting is in progress.</i>"
    FINISHED_STATUS = "<i>This meeting has finished.</i>"
    # Standalone state banners replacing a card whose meeting can no longer be reached
    DELETED_BANNER = f"{Emojis.PROHIB} This meeting has been deleted {Emojis.PROHIB}"
    FINISHED_BANNER = f"{Emojis.CHECK} This meeting has finished {Emojis.CHECK}"
    # Closes a card shared outside the bot, where most readers have never opened it. The product
    # name is substituted rather than written into the string: it is a brand term no catalog
    # translates, and it carries the link that opens the bot.
    BUILT_WITH = "Built with ${mitup}"


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
    # Shown under the meeting's title after the user taps Leave in the private chat with the bot.
    # LEAVE_SUCCESS is the alert that answers the tap itself.
    LEFT_CONFIRMATION = "You're no longer in this meeting."
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
        "<i>You were in the middle of inviting someone.</i>\n"
        "Send the name of the person to continue, or ${button_cancel} to exit."
    )
    PROMPT = "<b>Add to Guest List</b>\n\nPlease reply with the name of the person you want to add."
    CONFIRMATION = (
        "<b>Confirm Addition</b>\n\nAre you sure you want to add <b>${name}</b> to the meeting <b>${meeting_title}</b>?"
    )
    OPEN_CHAT = "Start Private Chat\n\nTo add participants, you need to open a chat with me first."
    GO_PRIVATE = "Continue in Private\n\nPlease switch to our private chat to send me the name."
    SUCCESS = "<b>List Updated</b>\n\nUser <b>${name}</b> has been added to the meeting <b>${meeting_title}</b>."
    # The three rejections below are shown only as plain callback-query alerts (max 200 characters),
    # like OPEN_CHAT and GO_PRIVATE above, so they must stay entity-free: Telegram renders no
    # formatting in an alert, and an inline-formatting tag here reaches the caller as a fault instead
    # of the alert. Their headings are plain lines for that reason. Every catalog has to keep them
    # tag-free too, since the entities come from whichever translation is rendered.
    MEETING_FULL = (
        "No Spots Left\n\nThe guest list is currently full. You cannot add anyone else unless a spot opens up."
    )
    INVITES_DISABLED = (
        "Guest List Closed\n\nNew additions are no longer allowed for this meeting. The organizer has closed the list."
    )
    # Naming the action ("the meeting you are trying to invite someone to") is deliberate: the invite
    # flow is entered from a card that may sit in any chat and answered in the bot chat, so its copy
    # has to reconnect the reader to what they were doing. It is not interchangeable with the generic
    # deleted-meeting copy.
    MEETING_NOT_FOUND = "Meeting Not Found\n\nThe meeting you are trying to invite someone to does not exist anymore."
    # Appended to the rejection screen when the invite flow's mid-flow guard stops the caller, so the
    # screen that replaces their prompt says which flow it ended.
    FLOW_CONTEXT = "You were in the middle of inviting someone."
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
    # Where a shared card stands in the chat it sits in, as the muted line closing it. Both read as
    # a state rather than as news: a card is redrawn on every join, so an announcement would report
    # itself as fresh long after the tap that made it true. They share a glyph, so the two states
    # occupy the same slot and only the words tell them apart.
    STATE_NOT_SEARCHABLE = f"{Emojis.SEARCH} Not searchable in this chat yet"
    STATE_SEARCHABLE = f"{Emojis.SEARCH} Searchable in this chat"
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
    TITLE_PROMPT = "This is the current title of your meeting:\n<b>${title}</b>\n\nSend me the new one"
    TITLE_ON_EXIT = (
        "<i>You were in the middle of renaming your meeting.</i>\n"
        "Send the new title to continue, or ${button_cancel} to exit."
    )
    TITLE_TOO_LONG = "That title is ${length} characters, over the limit of ${limit}. Send me a shorter one."
    DESCRIPTION_PROMPT = "This is the current description of your meeting:\n${description}\n\nSend me the new one"
    DESCRIPTION_ON_EXIT = (
        "<i>You were in the middle of changing the description.</i>\n"
        "Send the new description to continue, or ${button_cancel} to exit."
    )
    DESCRIPTION_TOO_LONG = (
        "That description is ${length} characters, over the limit of ${limit}. Send me a shorter one."
    )
    REMOVE_DESCRIPTION_CONFIRMATION = (
        "Are you sure you want to remove the description of your meeting?\n\n${description}"
    )


# --- Meeting edit: location ---


class MeetingEditLocationMessages(MessageBase):
    NAME_PROMPT = "Send me the name of the place."
    COORDINATES_PROMPT = (
        f"<i>Only from the phone {Emojis.PHONE}</i>\n\n"
        f"Send the location of the meeting. Touch on the {Emojis.CLIP} icon and then choose location. "
        "You can send whatever location you want, not just your current location."
    )
    COORDINATES_INVALID = "Send me the location again. Remember to touch on the clip icon and choose location."
    NAME_ON_EXIT = (
        "<i>You were in the middle of naming the location.</i>\n"
        "Send the name of the location to continue, or ${button_cancel} to exit."
    )
    LOCATION_NAME_TOO_LONG = (
        "That place name is ${length} characters, over the limit of ${limit}. Send me a shorter one."
    )
    COORDINATES_ON_EXIT = (
        "<i>You were in the middle of setting the map pin.</i>\n"
        "Send the location of your meeting to continue, or ${button_cancel} to exit."
    )
    REMOVE_NAME_CONFIRMATION = "This removes the meeting's place name. Are you sure?"
    REMOVE_COORDINATES_CONFIRMATION = "This removes the meeting's map pin. Are you sure?"


# --- Meeting edit: participants ---


class MeetingEditParticipantsMessages(MessageBase):
    LIMIT_PROMPT = (
        "Send me the maximum number of participants allowed in the meeting (must be a number greater than 0)."
    )
    MAX_SUCCESS = "Max participants set to: <b>${max_participants}</b>"
    # Shown to capped owners only: their removed limit resolves to the plan's cap
    # (Meetup.effective_max_members), so removing it must not read as "unlimited".
    REMOVE_LIMIT_CONFIRMATION = (
        "Removing the limit sets it back to <b>${cap}</b>, the most participants your meetings can have. "
        "Do you want to continue?\n\n"
        "Need bigger meetings? Become a Mitup Host on Patreon to raise the ceiling: ${button_collaborate}"
    )
    NO_LIMIT_LABEL = "No limit"
    MAX_INVALID = "The maximum number of participants must be a number greater than 0. Please, try again"
    KICK_OUT_DESCRIPTION = "These are the users that joined the meeting. Choose who you want to kick out."
    KICK_OUT_CONFIRMATION = (
        "Are you sure you want to kick out <b>${participant}</b> from the meeting <b>${meeting_title}</b>?"
    )
    KICK_OUT_NOT_IN_MEETING = "The participant you tried to kick out is no longer in the meeting."
    MAX_ON_EXIT = (
        "<i>You were in the middle of setting the participant limit.</i>\n"
        "Send the maximum number of participants to continue, or ${button_cancel} to exit."
    )


# --- Meeting edit: settings ---


class MeetingEditSettingsMessages(MessageBase):
    WAITING_LIST_EXPLANATION = (
        "When the meeting is full, new joiners land on a waiting list and move into the meeting "
        "automatically as spots free up, in the order they joined."
    )
    PUBLIC_EXPLANATION = "Anyone who receives the meeting can share it again. Perfect to reach more people."
    OPEN_INVITATIONS_EXPLANATION = "Participants can bring friends along, even friends who are not on Telegram."
    INCOGNITO_EXPLANATION = (
        "The shared meeting shows only how many people joined, not who. As the host, you still see everyone."
    )
    LOCK_ON_START_EXPLANATION = (
        "The moment the meeting starts, the attendee list is locked: nobody can join or leave while it is "
        "in progress. Takes effect once the meeting has a start time."
    )
    BEHAVIOR_GROUP = "Behavior"
    BEHAVIOR_LINE = "How the meeting behaves: who can join and share it, invitations, privacy, and locking on start."
    TIME_FORMAT_GROUP = "Time format"
    TIME_FORMAT_LINE = "How the meeting's date and time are written for everyone who sees it."
    SHOW_TIMEZONE_EXPLANATION = (
        "Writes the meeting's timezone next to its times, so people elsewhere know which clock they are reading."
    )
    CLOCK_24H_EXPLANATION = "Shows the meeting's times as 22:45 instead of 10:45 PM."
    DATE_FORMAT_EXPLANATION = "How the meeting's dates are written, for everyone who sees them."


# --- Meeting images ---


class MeetingImagesMessages(MessageBase):
    """The Images screen, where the owner manages the photos a meeting card shows.

    `LOCKED` is the screen's whole body when the owner is not a Host, so it is written in the same
    tone as the plan-limit notices in `SupporterMessages`.
    """

    TITLE = "Images"
    LEAD = (
        "Send one or more photos to show them on the meeting card, in the order they should "
        "appear. A meeting holds up to ${limit} photos, and wide ones look best."
    )
    REPLACING = "Send the photo that replaces image ${number}."
    IMAGE_LABEL = "Image ${number}"
    LAYOUT = "Layout"
    LAYOUT_EXPLANATION = (
        "How several photos are drawn on the card: a collage shows them all at once, a slideshow shows one at a time."
    )
    LOCKED = (
        "Photos on the meeting card are one of the extras Hosts get. Become a Mitup Host on "
        "Patreon to bring them to your meetings. Collaborate below has all the details."
    )
    LIMIT_REACHED = "This meeting already holds its ${limit} photos. Remove one to make room for another."
    REMOVE_CONFIRMATION = "This removes image ${number} from the card. Are you sure?"
    REMOVE_ALL_CONFIRMATION = "This removes every photo from the card. Are you sure?"
    SEND_AS_PHOTO = "That arrived as a file. Send it as a photo to put it on the card."
    # Draft shown while the photos of an album are still arriving; the Images screen replaces it.
    ADDING_PHOTOS = "Adding your photos"
    ON_EXIT = (
        "<i>You were in the middle of changing your meeting's photos.</i>\n"
        "Send a photo to continue, or ${button_cancel} to exit."
    )


# --- Meeting edit: date & time ---


class MeetingEditDateTimeMessages(MessageBase):
    EXPLANATION = (
        "Select the date and time of your meeting from the calendar.\n\n"
        "<i>Tip: you can also send a message using ${datetime_link} to set the date and time at once.</i>"
    )
    CURRENT_TIME = f"{Emojis.CLOCK} Current time: <b>${{time}}</b> ${{button_edit}}"
    TIME_NEEDS_DATE = "Select a date first, then set the time."
    ON_EXIT = (
        "<i>You were in the middle of changing the start time.</i>\n"
        "Send the new time to continue, or ${button_cancel} to exit."
    )
    END_CLEARED_BY_START = "The new start time is after the end time, so the end time has been cleared."
    START_IN_PAST = "The start time must be in the future."


# --- Meeting edit: start/end time removal ---


class MeetingEditWhenMessages(MessageBase):
    # The start's remove chip clears the whole schedule: an end time cannot exist without a start,
    # so the confirmation states the full effect. The start lock is a standing setting and stays.
    REMOVE_TIMES_CONFIRMATION = "This removes the meeting's start time, and the end time along with it. Are you sure?"
    REMOVE_END_CONFIRMATION = "This removes the meeting's end time. Are you sure?"


# --- Meeting edit: duration (end-time sub-screen) ---


class MeetingEditDurationMessages(MessageBase):
    EXPLANATION = (
        "The meeting starts at <b>${start_datetime}</b>. Select the date and time it ends from the calendar.\n\n"
        "<i>Tip: you can also send a message using ${datetime_link} to set the date and time at once.</i>"
    )
    END_BEFORE_START = "The end time must be after the start time."
    END_IN_PAST = "The end time must be in the future."
    END_MAX_DURATION = "A meeting can last a week at most. Set an end within a week of the start."
    END_STALE_ALERT = "The start time was cleared. Set it again before choosing an end time."
    ON_EXIT = (
        "<i>You were in the middle of setting the end time.</i>\n"
        "Send the end time to continue, or ${button_cancel} to exit."
    )


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
    STARTING_SOON_HEADING = f"{Emojis.NOTIF} Starting soon"
    STARTED_HEADING = f"{Emojis.GREEN_CIRCLE} Started"
    CANNOT_MAKE_IT = "Can't make it? ${button_leave}"
    # The countdown line both reminders open on. `${when}` is how far the start is, written as
    # "in 25 minutes" or "5 minutes ago" in the reader's language.
    STARTS_IN = "Starts ${when}"
    STARTED_AGO = "Started ${when}"
    STARTED_JUST_NOW = "Started just now"


class CollaborateMessages(MessageBase):
    # Pitch under the Collaborate title, read by anyone who has not linked an account yet.
    PITCH = (
        "Mitup is built by a pair of brothers, not a big company, and it is free for everyone. "
        "Your support pays for the servers, the database and the late-night debugging."
    )
    # "Ways to help" section, on every Collaborate screen. `${button_collaborate_page}` is the chip
    # opening the docs page that lists every way to pitch in.
    WAYS_TO_HELP_TITLE = "Ways to help"
    WAYS_TO_HELP = (
        "Tell your friends, translate, report bugs, or back the project. "
        "The ${button_collaborate_page} page walks through every one."
    )
    # "Become a Host" section, shown to anyone who is not a Host yet: the lead sentence, then the
    # tier table, then a closing line whose `${button_limits_page}` chip opens the docs limits page.
    BECOME_HOST_TITLE = "Become a Host"
    BECOME_HOST = (
        "The most direct way to help. Hosts get a badge, a members-only group and raised limits depending on the tier."
    )
    LIMITS_PAGE_LINE = "The ${button_limits_page} page has the numbers."
    # Column headings of the tier table. The first column holds the tier names and has none.
    TABLE_BADGE = "Badge"
    TABLE_GROUP = "Group"
    TABLE_LIMITS = "Limits"
    # The Limits cell of the tier table, one per tier: free-tier limits, raised limits, no limits.
    TIER_LIMITS_FREE = "free"
    TIER_LIMITS_RAISED = "raised"
    TIER_LIMITS_NONE = "none"
    # Status section of the Collaborate screen for a linked account with no active pledge.
    LINKED_TITLE = "Patreon account linked"
    LINKED_NOT_HOST = (
        "You're all set, thanks for taking the first step! You're not a Host yet, so your Host "
        "perks are still off. Become a Host and they turn on automatically, with no need to link again."
    )
    # Status section of the Collaborate screen for a linked Host, one title and one body per tier.
    STATUS_TITLE_HOST_1 = "You're a Brewer"
    STATUS_HOST_1 = (
        "Thanks for backing Mitup. Your Brewer badge shows next to your name, and your support "
        "helps keep Mitup running for everyone."
    )
    STATUS_TITLE_HOST_2 = "You're a Gamemaster"
    STATUS_HOST_2 = (
        "Thanks for backing Mitup. Your Gamemaster badge is on, and you can run up to "
        "${active_meetings} active meetings at once, schedule up to ${scheduling_days} days ahead, "
        "and invite unlimited participants per meeting."
    )
    STATUS_TITLE_HOST_3 = "You're a Commissioner"
    STATUS_HOST_3 = (
        "Thanks for backing Mitup. Your Commissioner badge is on, and every limit is off: run as "
        "many meetings as you want, schedule them as far ahead as you need, and invite as many "
        "people as you like to each one."
    )
    # Hosts-Only Group section, shown to a linked Host once the group is configured.
    HOSTS_GROUP_TITLE = "Hosts-Only Group"
    HOSTS_GROUP = "The members-only group where Hosts meet the people behind Mitup and each other."
    # Context line edited onto the view right after the user unlinks.
    UNLINKED = "Your Patreon account has been unlinked."
    # Unlink-confirmation prompt for an account with no active Host perks. Unlinking only
    # disconnects the Patreon account for them, so there is nothing to warn about beyond that.
    UNLINK_CONFIRM = (
        "<b>Unlink your Patreon account?</b>\n\n"
        "This disconnects your Patreon account from Mitup. You are not using any Host perks right "
        "now, so you are not giving any up. You can link an account again any time from Collaborate."
    )
    # Same prompt for a current Host. This is the destructive case: the copy names the tier being
    # switched off and states that the Patreon pledge itself keeps running unless cancelled on
    # Patreon, because unlinking here changes nothing on Patreon's side.
    UNLINK_CONFIRM_HOST = (
        "<b>Unlink your Patreon account?</b>\n\n"
        "You are a ${current_tier} today. Unlinking switches off your ${current_tier} badge and "
        "raised limits, and your access to the Hosts-Only Group ends.\n\n"
        "Your pledge on Patreon does not stop by itself. If you want to stop paying, cancel it on "
        "Patreon. If you link your account again while your pledge is active, your Host perks come "
        "back."
    )
    # Telegram message sent after redemption when the linked user is not a Host yet.
    LINK_CONFIRMED_NO_PATRON = (
        "<b>Patreon account linked</b>\n\n"
        "You're connected. Become a Host to unlock your Host perks. They'll switch on automatically "
        "once your pledge is active."
    )
    # Link-confirmation prompt, shown when a pairing code is presented and the account has no
    # Patreon connected. The Patreon display name leads on its own line and in bold, because a
    # recognised name is what makes a genuine link confirmable at a glance.
    #
    # The name alone cannot carry the check, though: an attacker picks their own Patreon display
    # name, so "do you recognise this?" is a question they can answer for the reader. The sentence
    # about having just approved on Patreon is the one fact in the prompt they cannot forge -- in
    # the forwarded-link case the reader never went to Patreon at all, because the attacker did.
    #
    # Note the register throughout: "this Patreon account", never "your Patreon account" -- calling
    # it theirs pre-frames a stranger's account as the reader's own and undoes the whole check.
    LINK_CONFIRM = (
        "<b>${patreon_name}</b>\n\n"
        "That is the Patreon account this link would connect to Mitup.\n\n"
        "You should only be seeing this straight after approving Mitup on Patreon yourself. If you "
        "didn't just do that, somebody else's link reached this chat.\n\n"
        "So connect it only if you recognise the name, and tap Not now if it means nothing to you. "
        "Nothing changes either way until you confirm."
    )
    # Same prompt for an account that already has a different Patreon connected. This is the
    # destructive case: confirming replaces a real link, so the copy names the tier being given up
    # rather than describing the mechanism.
    LINK_CONFIRM_REPLACES = (
        "<b>${patreon_name}</b>\n\n"
        "That is the Patreon account this link would connect to Mitup, and a different Patreon "
        "account is connected right now.\n\n"
        "You are a ${current_tier} today. Connecting this one gives that up: your ${current_tier} "
        "badge and raised limits switch off, and the Patreon account you actually back Mitup with "
        "stops counting, even though you keep paying for it.\n\n"
        "You should only be seeing this straight after approving Mitup on Patreon yourself. If you "
        "didn't just do that, somebody else's link reached this chat, and confirming would cost you "
        "your ${current_tier} perks for nothing.\n\n"
        "So do this only if you recognise the name above as your own Patreon. Tap Not now and "
        "nothing changes."
    )
    # Shown after declining the prompt, so the outcome is stated rather than left to inference.
    LINK_DECLINED = (
        "<b>Nothing connected</b>\n\n"
        "No Patreon account was connected and nothing on your account changed. You can link your "
        "own Patreon account any time from Collaborate."
    )
    # Bare tier names, substituted into the replacement warning so a Host reads the name of the
    # thing they would be giving up. Brand terms: identical in every language, never translated.
    TIER_NAME_HOST_1 = "Brewer"
    TIER_NAME_HOST_2 = "Gamemaster"
    TIER_NAME_HOST_3 = "Commissioner"
    # Shown when a pairing code arrives from someone who has not finished setting Mitup up. The
    # pending link is left untouched, so the same link still works once they are set up.
    LINK_NEEDS_SETUP = (
        "<b>Finish setting up Mitup first</b>\n\n"
        "Send /start to set up your account, then tap the confirmation link again to connect your "
        "Patreon account. The link stays valid for a few more minutes."
    )
    # Telegram message sent when a confirmation link is tapped but its code cannot be redeemed:
    # expired, already used, or never issued. All three read the same on purpose, so guessing a code
    # learns nothing about whether it exists.
    LINK_CODE_NOT_VALID = (
        "<b>This confirmation link no longer works</b>\n\n"
        "Confirmation links can be used once and expire quickly, and this one is past that. Nothing "
        "changed on your account. Open Collaborate to link your Patreon account again and you'll get "
        "a fresh link."
    )
    # Telegram message sent when the Patreon account behind the confirmation link already backs a
    # different Mitup account.
    LINK_ALREADY_LINKED_ELSEWHERE = (
        "<b>That Patreon account is already connected</b>\n\n"
        "A Patreon account can back one Mitup account at a time, and this one is connected to another "
        "Mitup account, so nothing changed here. If that other account is also yours, open Collaborate "
        "there and unlink it first, then confirm again from this chat. If it isn't yours, get in touch "
        "and we'll sort it out with you."
    )

    @classmethod
    def status_for(cls, level: SupporterLevel) -> CollaborateMessages:
        """The status body on the Collaborate screen of a linked, active paying tier.

        NONE never reaches here: a non-Host never sees the linked Host screen, so it raises
        rather than inventing copy for the free tier."""
        match level:
            case SupporterLevel.HOST_1:
                return cls.STATUS_HOST_1
            case SupporterLevel.HOST_2:
                return cls.STATUS_HOST_2
            case SupporterLevel.HOST_3:
                return cls.STATUS_HOST_3
            case SupporterLevel.NONE:
                raise ValueError("No status message exists for the NONE tier")
            case _ as unreachable:
                assert_never(unreachable)

    @classmethod
    def status_title_for(cls, level: SupporterLevel) -> CollaborateMessages:
        """The title of the section `status_for` fills, raising on NONE for the same reason."""
        match level:
            case SupporterLevel.HOST_1:
                return cls.STATUS_TITLE_HOST_1
            case SupporterLevel.HOST_2:
                return cls.STATUS_TITLE_HOST_2
            case SupporterLevel.HOST_3:
                return cls.STATUS_TITLE_HOST_3
            case SupporterLevel.NONE:
                raise ValueError("No status title exists for the NONE tier")
            case _ as unreachable:
                assert_never(unreachable)

    @classmethod
    def tier_limits_for(cls, level: SupporterLevel) -> CollaborateMessages:
        """The Limits cell of the tier table, raising on NONE, which the table never lists."""
        match level:
            case SupporterLevel.HOST_1:
                return cls.TIER_LIMITS_FREE
            case SupporterLevel.HOST_2:
                return cls.TIER_LIMITS_RAISED
            case SupporterLevel.HOST_3:
                return cls.TIER_LIMITS_NONE
            case SupporterLevel.NONE:
                raise ValueError("No tier limits exist for the NONE tier")
            case _ as unreachable:
                assert_never(unreachable)

    @classmethod
    def tier_name_for(cls, level: SupporterLevel) -> CollaborateMessages:
        """The bare tier name, for substituting into copy that has to name a tier.

        NONE never reaches here: it is only used by the replacement warning, which is shown solely
        to a user who currently holds a paying tier."""
        match level:
            case SupporterLevel.HOST_1:
                return cls.TIER_NAME_HOST_1
            case SupporterLevel.HOST_2:
                return cls.TIER_NAME_HOST_2
            case SupporterLevel.HOST_3:
                return cls.TIER_NAME_HOST_3
            case SupporterLevel.NONE:
                raise ValueError("No tier name exists for the NONE tier")
            case _ as unreachable:
                assert_never(unreachable)


class SupporterNotificationMessages(MessageBase):
    """Direct messages sent when a Host's status changes: by the daily Host-validation job, the
    Patreon webhook, and the admin grant flow.

    Full messages (not callback-query alerts), so inline `<b>` tags are allowed. Each mirrors a
    transition in the grace/upgrade/revoke flow driven against Patreon, or a manual grant."""

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

    # A tier turned on by an operator grant: gift framing rather than Patreon thanks. Each mirrors
    # the perks description of the corresponding *_UNLOCKED message.
    HOST_1_GRANTED = (
        f"<b>{Emojis.HOST_1} You're a Brewer</b>\n\n"
        "You've been given the Brewer Host level as a gift. Your Brewer badge now shows on your "
        "profile."
    )
    HOST_2_GRANTED = (
        f"<b>{Emojis.HOST_2} You're a Gamemaster</b>\n\n"
        "You've been given the Gamemaster Host level as a gift. Your Gamemaster badge is on, and "
        "your limits just went up: run more meetings at once, schedule them further ahead, and "
        "invite as many people as you like to each one."
    )
    HOST_3_GRANTED = (
        f"<b>{Emojis.HOST_3} You're a Commissioner</b>\n\n"
        "You've been given the Commissioner Host level as a gift. Your Commissioner badge is on, "
        "and every limit is off: run as many meetings as you want, schedule them as far ahead as "
        "you need, and invite as many people as you like to each one."
    )
    # A gifted tier withdrawn by an operator, for a user with no active Patreon pledge to fall
    # back on.
    GRANT_REMOVED = (
        "<b>Your gifted Host perks have ended</b>\n\n"
        "The Host level you were given is no longer active. You can become a Host anytime from "
        "the Collaborate menu."
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
    def granted_for(cls, level: SupporterLevel) -> SupporterNotificationMessages:
        """The per-tier gift message for a manually granted paying tier.

        NONE never reaches here: a withdrawn grant is announced with GRANT_REMOVED, so it raises
        rather than inventing gift copy for the free tier."""
        match level:
            case SupporterLevel.HOST_1:
                return cls.HOST_1_GRANTED
            case SupporterLevel.HOST_2:
                return cls.HOST_2_GRANTED
            case SupporterLevel.HOST_3:
                return cls.HOST_3_GRANTED
            case SupporterLevel.NONE:
                raise ValueError("No gift message exists for the NONE tier")
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


class Languages(MessageBase):
    SPANISH = "🇪🇸 Spanish"
    GALICIAN = "🇪🇸 Galician"
    ENGLISH = "🇺🇸 English"
    GERMAN = "🇩🇪 German"
    PORTUGUESE = "🇧🇷 Portuguese"
    ITALIAN = "🇮🇹 Italian"


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
    # Shown when a preview could not be sent, which is where Telegram parses the body.
    ERROR_PREVIEW_REJECTED = (
        f"{Emojis.PROHIB} "
        "The <b>${language}</b> message could not be sent, so nothing was queued.\n\n"
        "<code>${reason}</code>\n\nFix it and send it again."
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
    BUTTON_SUPPORTER_GRANTS = f"{Emojis.GIFT} Host grants"


class GrantOperatorMessages(MessageBase):
    TARGET_PROMPT = (
        "<b>Host grants</b>\n\n"
        "Send the Telegram id or @username of the user to manage. "
        "Only registered members can be granted a Host level."
    )
    TARGET_NOT_FOUND = (
        f"{Emojis.PROHIB} No member matches <code>${{identifier}}</code>. Check the id or username and send it again."
    )
    # `${current_level}`/`${granted_level}` receive a level label (see `level_label`);
    # `${patreon_linked}` receives a boolean emoji.
    TARGET_SUMMARY = (
        "<b>${name}</b>\n"
        "Telegram id: <code>${tg_user_id}</code>\n"
        "Current level: ${current_level}\n"
        "Granted level: ${granted_level}\n"
        "Patreon linked: ${patreon_linked}\n\n"
        "Pick the Host level to grant. The granted level is a floor: Patreon changes never drop "
        "the user below it."
    )
    CONFIRM_PROMPT = "Set the granted Host level for <b>${name}</b> to ${level}?"
    APPLIED_CONFIRMATION = "The granted Host level for <b>${name}</b> is now ${level}."
    CANCELLED_CONFIRMATION = "Host grant flow abandoned. Nothing changed."

    # Level display labels, used in the summary and confirmation lines and on the tier picker
    # buttons. The picker's remove option gets its own label because "None" under-describes the act.
    LEVEL_NONE = "None"
    LEVEL_HOST_1 = f"{Emojis.HOST_1} Brewer"
    LEVEL_HOST_2 = f"{Emojis.HOST_2} Gamemaster"
    LEVEL_HOST_3 = f"{Emojis.HOST_3} Commissioner"
    BUTTON_REMOVE_GRANT = f"{Emojis.PROHIB} No grant"
    BUTTON_CANCEL = f"{Emojis.CANCEL} Cancel"

    @classmethod
    def level_label(cls, level: SupporterLevel) -> GrantOperatorMessages:
        """The display label for a tier on the operator's screens."""
        match level:
            case SupporterLevel.NONE:
                return cls.LEVEL_NONE
            case SupporterLevel.HOST_1:
                return cls.LEVEL_HOST_1
            case SupporterLevel.HOST_2:
                return cls.LEVEL_HOST_2
            case SupporterLevel.HOST_3:
                return cls.LEVEL_HOST_3
            case _ as unreachable:
                assert_never(unreachable)
