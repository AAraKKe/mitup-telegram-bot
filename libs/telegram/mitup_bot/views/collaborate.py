from mitup_bot.keyboards import ButtonConfig, Keyboard
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import FormattedText
from mitup_bot.utils.messages import ButtonMessages, CollaborateMessages, SupporterNotificationMessages
from mitup_bot.views.mitup_view import MitupView


def link_confirmation_view(text: str | FormattedText, lang: str) -> MitupView:
    """DM sent back to the user after a successful Patreon link: the confirmation copy plus a
    Main-menu button so the user is never stranded on a button-less message."""
    return MitupView(
        description=text,
        keyboard=[],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)


def hosts_group_readmitted_view(lang: str, invite_url: str | None) -> MitupView:
    """DM sent when a re-activated host's hosts-only group ban is lifted: the welcome-back copy, a
    Join button linking to the group invite, plus a Main-menu button so the host always has a way
    back into the bot.

    ``invite_url`` is the shared group invite link; when it is None the feature is unconfigured and
    the Join row is omitted, leaving the Main-menu button so the DM is never keyboard-less."""
    keyboard: Keyboard = []
    if invite_url is not None:
        keyboard.append([ButtonConfig(text=ButtonMessages.HOSTS_GROUP_JOIN.get_text(lang=lang), url=invite_url)])
    return MitupView(
        description=SupporterNotificationMessages.HOSTS_GROUP_READMITTED.get(lang=lang),
        keyboard=keyboard,
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)


def hosts_group_removed_view(lang: str) -> MitupView:
    """DM sent when a lapsed host is removed from the hosts-only group: the access-ended copy plus a
    Main-menu button so the host always has a way back into the bot. No Join button, since they are no
    longer a Host and rejoin from the Collaborate menu once they back Mitup again."""
    return MitupView(
        description=SupporterNotificationMessages.HOSTS_GROUP_REMOVED.get(lang=lang),
        keyboard=[],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)


def collaborate_unavailable_view(lang: str) -> MitupView:
    """Degraded screen shown when the bot runs without a ``[patreon]`` config section."""
    return MitupView(
        description=CollaborateMessages.UNAVAILABLE.get(lang=lang),
        keyboard=[],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)


def collaborate_not_linked_view(
    lang: str, authorization_url: str, active_meetings: int, scheduling_days: int
) -> MitupView:
    """Not-linked screen: supporter pitch plus the Patreon OAuth link button (a URL button)."""
    return MitupView(
        description=CollaborateMessages.NOT_LINKED.get(
            lang=lang, active_meetings=active_meetings, scheduling_days=scheduling_days
        ),
        keyboard=[[ButtonConfig(text=ButtonMessages.LINK_PATREON.get_text(lang=lang), url=authorization_url)]],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)


def collaborate_linked_not_patron_view(
    lang: str, pledge_url: str, active_meetings: int, scheduling_days: int
) -> MitupView:
    """Linked-but-not-patron screen: become-a-patron link plus the Unlink button."""
    return MitupView(
        description=CollaborateMessages.LINKED_NOT_PATRON.get(
            lang=lang, active_meetings=active_meetings, scheduling_days=scheduling_days
        ),
        keyboard=[
            [ButtonConfig(text=ButtonMessages.BECOME_PATRON.get_text(lang=lang), url=pledge_url)],
            [ButtonConfig(text=ButtonMessages.UNLINK_PATREON.get_text(lang=lang), callback_data=cb.UNLINK_PATREON)],
        ],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)


def collaborate_linked_patron_view(
    lang: str,
    level: SupporterLevel,
    active_meetings: int,
    scheduling_days: int,
    hosts_group_url: str | None = None,
    in_group: bool = False,
) -> MitupView:
    """Supporter screen for a linked, active patron: per-tier status, the Hosts-Only Group access
    button when the feature is configured, plus the Unlink button.

    ``hosts_group_url`` is the shared group invite link; when it is None the feature is unconfigured
    and the group row is omitted entirely. ``in_group`` picks the label: Open when the host is already
    in the group, Join otherwise. Both labels link to the same invite URL.
    """
    status_message = CollaborateMessages.status_for(level)
    keyboard: Keyboard = []
    if hosts_group_url is not None:
        group_label = ButtonMessages.HOSTS_GROUP_OPEN if in_group else ButtonMessages.HOSTS_GROUP_JOIN
        keyboard.append([ButtonConfig(text=group_label.get_text(lang=lang), url=hosts_group_url)])
    keyboard.append(
        [ButtonConfig(text=ButtonMessages.UNLINK_PATREON.get_text(lang=lang), callback_data=cb.UNLINK_PATREON)]
    )
    return MitupView(
        description=status_message.get(lang=lang, active_meetings=active_meetings, scheduling_days=scheduling_days),
        keyboard=keyboard,
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)
