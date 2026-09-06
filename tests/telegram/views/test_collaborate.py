from collections.abc import Callable

import pytest

from mitup_bot.emojis import Emojis
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, CollaborateMessages, SupporterNotificationMessages
from mitup_bot.utils.rich_message import RichContent, RichTag, button_content
from mitup_bot.views import MitupView
from mitup_bot.views.collaborate import (
    TIER_BADGES,
    collaborate_button,
    collaborate_linked_not_patron_view,
    collaborate_linked_patron_view,
    collaborate_not_linked_view,
    hosts_group_readmitted_view,
    hosts_group_removed_view,
    supporter_upsell_view,
)

AUTH_URL = "https://www.patreon.com/oauth2/authorize?state=abc"
PLEDGE_URL = "https://www.patreon.com/bePatron?c=12345"
GROUP_URL = "https://t.me/+hostsonlyinvite"
# The docs links are built independently of the views, pinning the production URLs.
COLLABORATE_PAGE_URL = "https://mitup.social/collaborate/donation/"
LIMITS_PAGE_URL = "https://mitup.social/user-guide/limits/"
ACTIVE_MEETINGS = 7
SCHEDULING_DAYS = 42

ScreenBuilder = Callable[[str], MitupView]

ALL_SCREENS: list[ScreenBuilder] = [
    lambda lang: collaborate_not_linked_view(lang, AUTH_URL),
    lambda lang: collaborate_linked_not_patron_view(lang, PLEDGE_URL),
    lambda lang: collaborate_linked_patron_view(lang, SupporterLevel.HOST_1, ACTIVE_MEETINGS, SCHEDULING_DAYS),
]
SCREEN_IDS = ["not_linked", "linked_not_host", "linked_host"]
# The two screens read by someone who is not a Host yet, which are the ones selling the tiers.
NO_PLEDGE_SCREENS = ALL_SCREENS[:2]
NO_PLEDGE_IDS = SCREEN_IDS[:2]


def back_row(lang: str) -> list[ButtonConfig]:
    return [ButtonConfig(text=ButtonMessages.MAIN_MENU.back(lang=lang), callback_data=cb.MAIN_MENU)]


def section_title(title: CollaborateMessages, glyph: Emojis, lang: str) -> str:
    return f"{glyph} {title.rich(lang=lang).wrap(RichTag.BOLD).html}"


def unlink_chip(lang: str) -> ButtonConfig:
    return ButtonConfig(text=ButtonMessages.UNLINK.text(lang=lang), callback_data=cb.UNLINK_PATREON, style="danger")


def test_collaborate_button_opens_collaborate(lang: str):
    button = collaborate_button(lang)

    assert button == ButtonConfig(text=ButtonMessages.COLLABORATE.text(lang=lang), callback_data=cb.COLLABORATE)


def test_supporter_upsell_view_carries_collaborate_button(lang: str):
    view = supporter_upsell_view(RichContent("You've hit a limit."), lang)
    expected = MitupView(
        message=RichContent("You've hit a limit."),
        menu=[[collaborate_button(lang)]],
    )

    assert view == expected


@pytest.mark.parametrize("build", ALL_SCREENS, ids=SCREEN_IDS)
def test_every_screen_heads_itself_with_the_collaborate_title(lang: str, build: ScreenBuilder):
    view = build(lang)

    assert view.message.html.startswith(ButtonMessages.COLLABORATE.rich(lang=lang).wrap(RichTag.H2).html)
    assert "${" not in view.message.text


@pytest.mark.parametrize("build", ALL_SCREENS, ids=SCREEN_IDS)
def test_every_screen_names_the_ways_to_help_page_on_a_chip(lang: str, build: ScreenBuilder):
    view = build(lang)

    chip = ButtonConfig(text=ButtonMessages.COLLABORATE_PAGE.text(lang=lang), url=COLLABORATE_PAGE_URL)
    assert section_title(CollaborateMessages.WAYS_TO_HELP_TITLE, Emojis.HANDSHAKE, lang) in view.message.html
    assert CollaborateMessages.WAYS_TO_HELP.rich(lang=lang, button_collaborate_page=chip).html in view.message.html


@pytest.mark.parametrize("build", ALL_SCREENS, ids=SCREEN_IDS)
def test_every_screen_ends_on_the_way_back_to_the_main_menu(lang: str, build: ScreenBuilder):
    view = build(lang)

    assert view.menu[-1] == back_row(lang)


@pytest.mark.parametrize("build", NO_PLEDGE_SCREENS, ids=NO_PLEDGE_IDS)
def test_a_screen_without_a_pledge_explains_becoming_a_host(lang: str, build: ScreenBuilder):
    view = build(lang)

    chip = ButtonConfig(text=ButtonMessages.LIMITS_PAGE.text(lang=lang), url=LIMITS_PAGE_URL)
    assert section_title(CollaborateMessages.BECOME_HOST_TITLE, Emojis.DONATE, lang) in view.message.html
    assert CollaborateMessages.BECOME_HOST.text(lang=lang) in view.message.text
    assert CollaborateMessages.LIMITS_PAGE_LINE.rich(lang=lang, button_limits_page=chip).html in view.message.html


@pytest.mark.parametrize("build", NO_PLEDGE_SCREENS, ids=NO_PLEDGE_IDS)
def test_the_tier_table_heads_its_columns(lang: str, build: ScreenBuilder):
    view = build(lang)

    headings = [CollaborateMessages.TABLE_BADGE, CollaborateMessages.TABLE_GROUP, CollaborateMessages.TABLE_LIMITS]
    expected = "<tr><th></th>" + "".join(f"<th>{heading.text(lang=lang)}</th>" for heading in headings) + "</tr>"
    assert expected in view.message.html


@pytest.mark.parametrize("build", NO_PLEDGE_SCREENS, ids=NO_PLEDGE_IDS)
@pytest.mark.parametrize("level", [SupporterLevel.HOST_1, SupporterLevel.HOST_2, SupporterLevel.HOST_3])
def test_the_tier_table_names_every_paying_tier_and_what_it_carries(
    lang: str, build: ScreenBuilder, level: SupporterLevel
):
    view = build(lang)

    name = CollaborateMessages.tier_name_for(level).text(lang=lang)
    limits = CollaborateMessages.tier_limits_for(level).text(lang=lang)
    assert f"<td>{TIER_BADGES[level]} {name}</td>" in view.message.html
    assert f"<td>{Emojis.CHECK}</td><td>{Emojis.CHECK}</td><td>{limits}</td>" in view.message.html


def test_not_linked_view_pitches_the_project_and_offers_the_link_button(lang: str):
    view = collaborate_not_linked_view(lang, AUTH_URL)

    assert CollaborateMessages.PITCH.text(lang=lang) in view.message.text
    link = ButtonConfig(text=ButtonMessages.LINK_PATREON.text(lang=lang), url=AUTH_URL, style="primary")
    assert view.menu == [[link], back_row(lang)]


def test_not_linked_view_offers_no_unlink_chip(lang: str):
    # Nothing is connected yet, so a chip disconnecting an account would act on nothing.
    view = collaborate_not_linked_view(lang, AUTH_URL)

    assert button_content(unlink_chip(lang)).html not in view.message.html


def test_linked_not_patron_view_states_the_link_and_offers_the_pledge_button(lang: str):
    view = collaborate_linked_not_patron_view(lang, PLEDGE_URL)

    title = section_title(CollaborateMessages.LINKED_TITLE, Emojis.LINK, lang)
    assert f"{title} {button_content(unlink_chip(lang)).html}" in view.message.html
    assert CollaborateMessages.LINKED_NOT_HOST.text(lang=lang) in view.message.text
    pledge = ButtonConfig(text=ButtonMessages.BECOME_PATRON.text(lang=lang), url=PLEDGE_URL, style="primary")
    assert view.menu == [[pledge], back_row(lang)]


@pytest.mark.parametrize("level", [SupporterLevel.HOST_1, SupporterLevel.HOST_2, SupporterLevel.HOST_3])
def test_linked_patron_view_titles_the_status_with_the_tier_and_its_badge(lang: str, level: SupporterLevel):
    view = collaborate_linked_patron_view(lang, level, ACTIVE_MEETINGS, SCHEDULING_DAYS)

    title = section_title(CollaborateMessages.status_title_for(level), TIER_BADGES[level], lang)
    status = CollaborateMessages.status_for(level).rich(
        lang=lang, active_meetings=ACTIVE_MEETINGS, scheduling_days=SCHEDULING_DAYS
    )
    assert f"{title} {button_content(unlink_chip(lang)).html}" in view.message.html
    assert status.html in view.message.html


def test_linked_patron_view_navigates_only_back_to_the_main_menu(lang: str):
    view = collaborate_linked_patron_view(lang, SupporterLevel.HOST_1, ACTIVE_MEETINGS, SCHEDULING_DAYS)

    assert view.menu == [back_row(lang)]


def test_linked_patron_view_omits_the_become_a_host_section(lang: str):
    # They already are one, so the tier sales pitch has nothing to tell them.
    view = collaborate_linked_patron_view(lang, SupporterLevel.HOST_1, ACTIVE_MEETINGS, SCHEDULING_DAYS)

    assert section_title(CollaborateMessages.BECOME_HOST_TITLE, Emojis.DONATE, lang) not in view.message.html


@pytest.mark.parametrize(
    "in_group, expected_label",
    [(False, ButtonMessages.JOIN_GROUP), (True, ButtonMessages.OPEN)],
    ids=["join", "open"],
)
def test_linked_patron_view_opens_the_hosts_group_from_its_section_title(
    lang: str, in_group: bool, expected_label: ButtonMessages
):
    view = collaborate_linked_patron_view(
        lang, SupporterLevel.HOST_1, ACTIVE_MEETINGS, SCHEDULING_DAYS, GROUP_URL, in_group=in_group
    )

    title = section_title(CollaborateMessages.HOSTS_GROUP_TITLE, Emojis.PEOPLE, lang)
    chip = ButtonConfig(text=expected_label.text(lang=lang), url=GROUP_URL)
    assert f"{title} {button_content(chip).html}" in view.message.html
    assert CollaborateMessages.HOSTS_GROUP.text(lang=lang) in view.message.text


def test_linked_patron_view_omits_the_group_section_when_the_feature_is_unconfigured(lang: str):
    # No invite url means the feature is disabled; the section must not render even if in_group is
    # somehow True, since there would be nothing for its chip to open.
    view = collaborate_linked_patron_view(
        lang, SupporterLevel.HOST_1, ACTIVE_MEETINGS, SCHEDULING_DAYS, None, in_group=True
    )

    assert section_title(CollaborateMessages.HOSTS_GROUP_TITLE, Emojis.PEOPLE, lang) not in view.message.html
    assert CollaborateMessages.HOSTS_GROUP.text(lang=lang) not in view.message.text


def test_gamemaster_status_renders_concrete_caps(lang: str):
    # The Gamemaster tier is the one carrying the ${active_meetings} and ${scheduling_days}
    # placeholders; the other two paying tiers have no vars.
    view = collaborate_linked_patron_view(lang, SupporterLevel.HOST_2, ACTIVE_MEETINGS, SCHEDULING_DAYS)

    assert "${" not in view.message.text
    assert str(ACTIVE_MEETINGS) in view.message.text
    assert str(SCHEDULING_DAYS) in view.message.text


@pytest.mark.parametrize(
    "level, expected_message",
    [
        (SupporterLevel.HOST_1, CollaborateMessages.STATUS_HOST_1),
        (SupporterLevel.HOST_2, CollaborateMessages.STATUS_HOST_2),
        (SupporterLevel.HOST_3, CollaborateMessages.STATUS_HOST_3),
    ],
)
def test_status_for_maps_each_paying_tier(level: SupporterLevel, expected_message: CollaborateMessages):
    assert CollaborateMessages.status_for(level) is expected_message


@pytest.mark.parametrize(
    "level, expected_title",
    [
        (SupporterLevel.HOST_1, CollaborateMessages.STATUS_TITLE_HOST_1),
        (SupporterLevel.HOST_2, CollaborateMessages.STATUS_TITLE_HOST_2),
        (SupporterLevel.HOST_3, CollaborateMessages.STATUS_TITLE_HOST_3),
    ],
)
def test_status_title_for_maps_each_paying_tier(level: SupporterLevel, expected_title: CollaborateMessages):
    assert CollaborateMessages.status_title_for(level) is expected_title


@pytest.mark.parametrize(
    "level, expected_limits",
    [
        (SupporterLevel.HOST_1, CollaborateMessages.TIER_LIMITS_FREE),
        (SupporterLevel.HOST_2, CollaborateMessages.TIER_LIMITS_RAISED),
        (SupporterLevel.HOST_3, CollaborateMessages.TIER_LIMITS_NONE),
    ],
)
def test_tier_limits_for_maps_each_paying_tier(level: SupporterLevel, expected_limits: CollaborateMessages):
    assert CollaborateMessages.tier_limits_for(level) is expected_limits


@pytest.mark.parametrize(
    "lookup",
    [CollaborateMessages.status_for, CollaborateMessages.status_title_for, CollaborateMessages.tier_limits_for],
    ids=["status", "status_title", "tier_limits"],
)
def test_the_tier_lookups_reject_the_none_tier(lookup: Callable[[SupporterLevel], CollaborateMessages]):
    with pytest.raises(ValueError):
        lookup(SupporterLevel.NONE)


def test_readmitted_view_offers_join_and_main_menu(lang: str):
    view = hosts_group_readmitted_view(lang, GROUP_URL)
    expected = MitupView(
        message=SupporterNotificationMessages.HOSTS_GROUP_READMITTED.rich(lang=lang),
        menu=[[ButtonConfig(text=ButtonMessages.HOSTS_GROUP_JOIN.text(lang=lang), url=GROUP_URL)]],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)

    assert view == expected


def test_readmitted_view_keeps_main_menu_when_invite_url_missing(lang: str):
    # A misconfigured feature (no invite url) must still leave the host a way back: the Join row drops
    # but the Main-menu button remains, so the DM is never keyboard-less.
    view = hosts_group_readmitted_view(lang, None)
    expected = MitupView(
        message=SupporterNotificationMessages.HOSTS_GROUP_READMITTED.rich(lang=lang),
        menu=[],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)

    assert view == expected


def test_removed_view_offers_only_main_menu(lang: str):
    view = hosts_group_removed_view(lang)
    expected = MitupView(
        message=SupporterNotificationMessages.HOSTS_GROUP_REMOVED.rich(lang=lang),
        menu=[],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)

    assert view == expected
