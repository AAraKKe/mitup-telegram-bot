from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import FormattedText
from mitup_bot.utils.messages import ButtonMessages, CollaborateMessages
from mitup_bot.views.mitup_view import ButtonConfig, MitupView


def link_confirmation_view(text: str | FormattedText, lang: str) -> MitupView:
    """DM sent back to the user after a successful Patreon link: the confirmation copy plus a
    Main-menu button so the user is never stranded on a button-less message."""
    return MitupView(
        description=text,
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
        keyboard=[[ButtonConfig(text=ButtonMessages.LINK_PATREON.get(lang=lang), url=authorization_url)]],
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
            [ButtonConfig(text=ButtonMessages.BECOME_PATRON.get(lang=lang), url=pledge_url)],
            [ButtonConfig(text=ButtonMessages.UNLINK_PATREON.get(lang=lang), callback_data=cb.UNLINK_PATREON)],
        ],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)


def collaborate_linked_patron_view(
    lang: str, level: SupporterLevel, active_meetings: int, scheduling_days: int
) -> MitupView:
    """Supporter screen for a linked, active patron: per-tier status plus the Unlink button."""
    status_message = CollaborateMessages.status_for(level)
    return MitupView(
        description=status_message.get(lang=lang, active_meetings=active_meetings, scheduling_days=scheduling_days),
        keyboard=[
            [ButtonConfig(text=ButtonMessages.UNLINK_PATREON.get(lang=lang), callback_data=cb.UNLINK_PATREON)],
        ],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)
