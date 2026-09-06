from mitup_bot import docs_links, supporter
from mitup_bot.emojis import Emojis
from mitup_bot.keyboards import ButtonConfig, Keyboard
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, CollaborateMessages, SupporterNotificationMessages
from mitup_bot.utils.rich_message import RichContent, RichTag, horizontal_rule_content, table_content
from mitup_bot.utils.rich_template import render_rich
from mitup_bot.views import factory
from mitup_bot.views.context import RenderContext
from mitup_bot.views.mitup_view import MitupView
from mitup_bot.views.sections import card_section, chip_section

# The paying tiers in the order the tier table lists them, each with the badge titling its screen.
TIER_BADGES: dict[SupporterLevel, Emojis] = {
    SupporterLevel.HOST_1: Emojis.HOST_1,
    SupporterLevel.HOST_2: Emojis.HOST_2,
    SupporterLevel.HOST_3: Emojis.HOST_3,
}


def collaborate_button(lang: str) -> ButtonConfig:
    """Button opening the Collaborate screen, for upsell surfaces outside the main menu."""
    return ButtonConfig(text=ButtonMessages.COLLABORATE.text(lang=lang), callback_data=cb.COLLABORATE)


def supporter_upsell_view(text: RichContent, lang: str) -> MitupView:
    """Plan-limit notice sent as a message: the rejection text plus a Collaborate button, so the
    user can act on the upsell right where they hit the limit instead of navigating to the main
    menu first."""
    return MitupView(message=text, menu=[[collaborate_button(lang)]])


def link_confirmation_view(text: RichContent, lang: str) -> MitupView:
    """DM sent back to the user after a successful Patreon link: the confirmation copy plus a
    Main-menu button so the user is never stranded on a button-less message."""
    return MitupView(
        message=text,
        menu=[],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)


def grant_notification_view(text: RichContent, lang: str) -> MitupView:
    """DM sent when an operator changes a user's granted Host level: the per-tier gift or removal
    copy plus a Main-menu button so the user is never stranded on a button-less message."""
    return MitupView(
        message=text,
        menu=[],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)


def patreon_link_confirmation_view(
    ctx: RenderContext, *, patreon_name: str, code: str, current_level: SupporterLevel
) -> MitupView:
    """The prompt a pairing code opens: which Patreon account is about to be connected, and what
    confirming costs.

    ``current_level`` is the tier the user holds *right now*, read live at render time. It only
    selects and fills the warning: it never decides what the confirmation grants, which comes from
    the pending-link row alone. A user at NONE has nothing to give up, so they get the plain variant.

    The buttons are addressed by ``code`` rather than by the pending row's id: a row id is guessable,
    and a forged button naming another user's row is exactly the attack this prompt exists to stop.
    """
    if supporter.is_supporter(current_level):
        message = CollaborateMessages.LINK_CONFIRM_REPLACES.rich(
            lang=ctx.lang,
            patreon_name=patreon_name,
            current_tier=CollaborateMessages.tier_name_for(current_level).text(lang=ctx.lang),
        )
    else:
        message = CollaborateMessages.LINK_CONFIRM.rich(lang=ctx.lang, patreon_name=patreon_name)
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
        message = CollaborateMessages.UNLINK_CONFIRM_HOST.rich(
            lang=ctx.lang,
            current_tier=CollaborateMessages.tier_name_for(current_level).text(lang=ctx.lang),
        )
    else:
        message = CollaborateMessages.UNLINK_CONFIRM.rich(lang=ctx.lang)
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
        message=CollaborateMessages.LINK_DECLINED.rich(lang=lang),
        menu=[[collaborate_button(lang)]],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)


def patreon_link_needs_setup_view(lang: str) -> MitupView:
    """Reply when a pairing code arrives from someone who has not finished onboarding. Keyboard-less
    on purpose: the message asks for /start, and this user has no main menu to go back to yet."""
    return MitupView(message=CollaborateMessages.LINK_NEEDS_SETUP.rich(lang=lang), menu=[])


def patreon_link_code_not_valid_view(lang: str) -> MitupView:
    """Reply to a confirmation deep link whose code is expired, spent, or unknown: the explanation
    plus a Collaborate button, so the user can start a fresh link right where they landed."""
    return MitupView(
        message=CollaborateMessages.LINK_CODE_NOT_VALID.rich(lang=lang),
        menu=[[collaborate_button(lang)]],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)


def patreon_already_linked_elsewhere_view(lang: str) -> MitupView:
    """Reply when the redeemed Patreon account already backs another Mitup account: the explanation
    plus a Main-menu button, since there is nothing to retry from this account."""
    return MitupView(
        message=CollaborateMessages.LINK_ALREADY_LINKED_ELSEWHERE.rich(lang=lang),
        menu=[],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)


def hosts_group_readmitted_view(lang: str, invite_url: str | None) -> MitupView:
    """DM sent when a re-activated host's hosts-only group ban is lifted: the welcome-back copy, a
    Join button linking to the group invite, plus a Main-menu button so the host always has a way
    back into the bot.

    ``invite_url`` is the shared group invite link; when it is None the feature is unconfigured and
    the Join row is omitted, leaving the Main-menu button so the DM is never keyboard-less."""
    keyboard: Keyboard = []
    if invite_url is not None:
        keyboard.append([ButtonConfig(text=ButtonMessages.HOSTS_GROUP_JOIN.text(lang=lang), url=invite_url)])
    return MitupView(
        message=SupporterNotificationMessages.HOSTS_GROUP_READMITTED.rich(lang=lang),
        menu=keyboard,
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)


def hosts_group_removed_view(lang: str) -> MitupView:
    """DM sent when a lapsed host is removed from the hosts-only group: the access-ended copy plus a
    Main-menu button so the host always has a way back into the bot. No Join button, since they are no
    longer a Host and rejoin from the Collaborate menu once they back Mitup again."""
    return MitupView(
        message=SupporterNotificationMessages.HOSTS_GROUP_REMOVED.rich(lang=lang),
        menu=[],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)


def collaborate_heading(lang: str) -> RichContent:
    return ButtonMessages.COLLABORATE.rich(lang=lang).wrap(RichTag.H2)


def unlink_chip(lang: str) -> ButtonConfig:
    return ButtonConfig(text=ButtonMessages.UNLINK.text(lang=lang), callback_data=cb.UNLINK_PATREON, style="danger")


def ways_to_help_section(lang: str) -> RichContent:
    page = ButtonConfig(text=ButtonMessages.COLLABORATE_PAGE.text(lang=lang), url=docs_links.collaborate_url())
    return card_section(
        CollaborateMessages.WAYS_TO_HELP_TITLE,
        Emojis.HANDSHAKE,
        lang,
        CollaborateMessages.WAYS_TO_HELP.rich(lang=lang, button_collaborate_page=page),
    )


def tiers_table(lang: str) -> RichContent:
    header = [
        RichContent(),
        CollaborateMessages.TABLE_BADGE.rich(lang=lang),
        CollaborateMessages.TABLE_GROUP.rich(lang=lang),
        CollaborateMessages.TABLE_LIMITS.rich(lang=lang),
    ]
    granted = RichContent(str(Emojis.CHECK))
    rows = []
    for level, badge in TIER_BADGES.items():
        name = CollaborateMessages.tier_name_for(level).rich(lang=lang)
        limits = CollaborateMessages.tier_limits_for(level).rich(lang=lang)
        rows.append([render_rich(t"{badge} {name}"), granted, granted, limits])
    return table_content(header, rows)


def become_host_section(lang: str) -> RichContent:
    limits_page = ButtonConfig(text=ButtonMessages.LIMITS_PAGE.text(lang=lang), url=docs_links.limits_url())
    body = (
        CollaborateMessages.BECOME_HOST.rich(lang=lang)
        .append(tiers_table(lang))
        .append(CollaborateMessages.LIMITS_PAGE_LINE.rich(lang=lang, button_limits_page=limits_page))
    )
    return card_section(CollaborateMessages.BECOME_HOST_TITLE, Emojis.DONATE, lang, body)


def collaborate_not_linked_view(lang: str, authorization_url: str) -> MitupView:
    heading = collaborate_heading(lang).append(CollaborateMessages.PITCH.rich(lang=lang))
    body = RichContent.join(horizontal_rule_content(), [heading, ways_to_help_section(lang), become_host_section(lang)])
    link = ButtonConfig(text=ButtonMessages.LINK_PATREON.text(lang=lang), url=authorization_url, style="primary")
    return MitupView(message=body, menu=[[link]]).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)


def collaborate_linked_not_patron_view(lang: str, pledge_url: str) -> MitupView:
    status = chip_section(
        CollaborateMessages.LINKED_TITLE,
        Emojis.LINK,
        lang,
        unlink_chip(lang),
        CollaborateMessages.LINKED_NOT_HOST.rich(lang=lang),
    )
    body = RichContent.join(
        horizontal_rule_content(),
        [collaborate_heading(lang).append(status), ways_to_help_section(lang), become_host_section(lang)],
    )
    pledge = ButtonConfig(text=ButtonMessages.BECOME_PATRON.text(lang=lang), url=pledge_url, style="primary")
    return MitupView(message=body, menu=[[pledge]]).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)


def collaborate_linked_patron_view(
    lang: str,
    level: SupporterLevel,
    active_meetings: int,
    scheduling_days: int,
    hosts_group_url: str | None = None,
    in_group: bool = False,
) -> MitupView:
    """``hosts_group_url`` is the shared group invite link; when it is None the feature is
    unconfigured and the group section is omitted. ``in_group`` picks its chip: Open when the host
    is already in the group, Join otherwise."""
    status = chip_section(
        CollaborateMessages.status_title_for(level),
        TIER_BADGES[level],
        lang,
        unlink_chip(lang),
        CollaborateMessages.status_for(level).rich(
            lang=lang, active_meetings=active_meetings, scheduling_days=scheduling_days
        ),
    )
    blocks = [collaborate_heading(lang).append(status)]
    if hosts_group_url is not None:
        group_label = ButtonMessages.OPEN if in_group else ButtonMessages.JOIN_GROUP
        blocks.append(
            chip_section(
                CollaborateMessages.HOSTS_GROUP_TITLE,
                Emojis.PEOPLE,
                lang,
                ButtonConfig(text=group_label.text(lang=lang), url=hosts_group_url),
                CollaborateMessages.HOSTS_GROUP.rich(lang=lang),
            )
        )
    blocks.append(ways_to_help_section(lang))
    return MitupView(message=RichContent.join(horizontal_rule_content(), blocks)).with_back_button(
        ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU
    )
