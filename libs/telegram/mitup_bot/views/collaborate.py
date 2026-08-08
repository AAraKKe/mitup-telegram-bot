from mitup_bot import docs_links, supporter
from mitup_bot.keyboards import ButtonConfig, Keyboard
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import FormattedText, Link, render
from mitup_bot.utils.messages import ButtonMessages, CollaborateMessages, SupporterNotificationMessages
from mitup_bot.views import factory
from mitup_bot.views.context import RenderContext
from mitup_bot.views.mitup_view import MitupView


def collaborate_button(lang: str) -> ButtonConfig:
    """Button opening the Collaborate screen, for upsell surfaces outside the main menu."""
    return ButtonConfig(text=ButtonMessages.COLLABORATE.get_text(lang=lang), callback_data=cb.COLLABORATE)


def supporter_upsell_view(text: str | FormattedText, lang: str) -> MitupView:
    """Plan-limit notice sent as a message: the rejection text plus a Collaborate button, so the
    user can act on the upsell right where they hit the limit instead of navigating to the main
    menu first."""
    return MitupView(description=text, keyboard=[[collaborate_button(lang)]])


def link_confirmation_view(text: str | FormattedText, lang: str) -> MitupView:
    """DM sent back to the user after a successful Patreon link: the confirmation copy plus a
    Main-menu button so the user is never stranded on a button-less message."""
    return MitupView(
        description=text,
        keyboard=[],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)


def grant_notification_view(text: str | FormattedText, lang: str) -> MitupView:
    """DM sent when an operator changes a user's granted Host level: the per-tier gift or removal
    copy plus a Main-menu button so the user is never stranded on a button-less message."""
    return MitupView(
        description=text,
        keyboard=[],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)


def patreon_link_confirmation_view(
    ctx: RenderContext, *, patreon_name: str, code: str, current_level: SupporterLevel
) -> MitupView:
    """The prompt a pairing code opens: which Patreon account is about to be connected, and what
    confirming costs.

    ``current_level`` is the tier the user holds *right now*, read live at render time. It only
    selects and fills the warning — it never decides what the confirmation grants, which comes from
    the pending-link row alone. A user at NONE has nothing to give up, so they get the plain variant.

    The buttons are addressed by ``code`` rather than by the pending row's id: a row id is guessable,
    and a forged button naming another user's row is exactly the attack this prompt exists to stop.
    """
    if supporter.is_supporter(current_level):
        message = CollaborateMessages.LINK_CONFIRM_REPLACES.get(
            lang=ctx.lang,
            patreon_name=patreon_name,
            current_tier=CollaborateMessages.tier_name_for(current_level).get_text(lang=ctx.lang),
        )
    else:
        message = CollaborateMessages.LINK_CONFIRM.get(lang=ctx.lang, patreon_name=patreon_name)
    return factory.confirmation_view(
        ctx,
        message=message,
        confirm_callback_data=cb.CONFIRM_PATREON_LINK.with_code(code),
        decline_callback_data=cb.DECLINE_PATREON_LINK.with_code(code),
        confirm_label=ButtonMessages.CONFIRM_PATREON_LINK,
        decline_label=ButtonMessages.DECLINE_PATREON_LINK,
    )


def patreon_unlink_confirmation_view(ctx: RenderContext, *, current_level: SupporterLevel) -> MitupView:
    """The prompt the Unlink button opens: what disconnecting costs, before anything is written.

    ``current_level`` picks the variant: a Host reads which tier gets switched off and that their
    group access ends, everyone else reads that only the connection itself goes away. Confirming is
    what deletes the subscription; this screen writes nothing.
    """
    if supporter.is_supporter(current_level):
        message = CollaborateMessages.UNLINK_CONFIRM_HOST.get(
            lang=ctx.lang,
            current_tier=CollaborateMessages.tier_name_for(current_level).get_text(lang=ctx.lang),
        )
    else:
        message = CollaborateMessages.UNLINK_CONFIRM.get(lang=ctx.lang)
    return factory.confirmation_view(
        ctx,
        message=message,
        confirm_callback_data=cb.CONFIRM_PATREON_UNLINK,
        decline_callback_data=cb.DECLINE_PATREON_UNLINK,
        confirm_label=ButtonMessages.CONFIRM_PATREON_UNLINK,
        decline_label=ButtonMessages.DECLINE_PATREON_UNLINK,
    )


def patreon_link_declined_view(lang: str) -> MitupView:
    """Reply to declining the confirmation prompt: nothing was connected, and the way back in."""
    return MitupView(
        description=CollaborateMessages.LINK_DECLINED.get(lang=lang),
        keyboard=[[collaborate_button(lang)]],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)


def patreon_link_needs_setup_view(lang: str) -> MitupView:
    """Reply when a pairing code arrives from someone who has not finished onboarding. Keyboard-less
    on purpose: the message asks for /start, and this user has no main menu to go back to yet."""
    return MitupView(description=CollaborateMessages.LINK_NEEDS_SETUP.get(lang=lang), keyboard=[])


def patreon_link_code_not_valid_view(lang: str) -> MitupView:
    """Reply to a confirmation deep link whose code is expired, spent, or unknown: the explanation
    plus a Collaborate button, so the user can start a fresh link right where they landed."""
    return MitupView(
        description=CollaborateMessages.LINK_CODE_NOT_VALID.get(lang=lang),
        keyboard=[[collaborate_button(lang)]],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)


def patreon_already_linked_elsewhere_view(lang: str) -> MitupView:
    """Reply when the redeemed Patreon account already backs another Mitup account: the explanation
    plus a Main-menu button, since there is nothing to retry from this account."""
    return MitupView(
        description=CollaborateMessages.LINK_ALREADY_LINKED_ELSEWHERE.get(lang=lang),
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


def limits_page_link(lang: str) -> FormattedText:
    """Inline link to the docs limits page, which owns the per-tier perk details so tier changes
    never require a chat-copy sweep."""
    return render(t"{Link(CollaborateMessages.LIMITS_PAGE_LABEL.get_text(lang=lang), docs_links.limits_url())}")


def collaborate_not_linked_view(lang: str, authorization_url: str) -> MitupView:
    """Not-linked screen: the support pitch, inline links to the docs ways-to-support and limits
    pages, plus the Patreon OAuth link button (a URL button)."""
    collaborate_page = render(
        t"{Link(CollaborateMessages.COLLABORATE_PAGE_LABEL.get_text(lang=lang), docs_links.collaborate_url())}"
    )
    return MitupView(
        description=CollaborateMessages.NOT_LINKED.get(
            lang=lang,
            collaborate_page=collaborate_page,
            limits_page=limits_page_link(lang),
        ),
        keyboard=[[ButtonConfig(text=ButtonMessages.LINK_PATREON.get_text(lang=lang), url=authorization_url)]],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)


def collaborate_linked_not_patron_view(lang: str, pledge_url: str) -> MitupView:
    """Linked-but-not-patron screen: become-a-patron link plus the Unlink button."""
    return MitupView(
        description=CollaborateMessages.LINKED_NOT_PATRON.get(lang=lang, limits_page=limits_page_link(lang)),
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
