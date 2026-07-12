import pytest

from mitup_bot.keyboards import ButtonConfig
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, CollaborateMessages, SupporterNotificationMessages
from mitup_bot.views import MitupView
from mitup_bot.views.collaborate import (
    collaborate_linked_not_patron_view,
    collaborate_linked_patron_view,
    collaborate_not_linked_view,
    collaborate_unavailable_view,
    hosts_group_readmitted_view,
    hosts_group_removed_view,
)

AUTH_URL = "https://www.patreon.com/oauth2/authorize?state=abc"
PLEDGE_URL = "https://www.patreon.com/bePatron?c=12345"
GROUP_URL = "https://t.me/+hostsonlyinvite"
ACTIVE_MEETINGS = 7
SCHEDULING_DAYS = 42


def test_unavailable_view_has_no_link_button(lang: str):
    view = collaborate_unavailable_view(lang)
    expected = MitupView(
        description=CollaborateMessages.UNAVAILABLE.get(lang=lang),
        keyboard=[],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)

    assert view == expected


def test_not_linked_view_offers_link_button(lang: str):
    view = collaborate_not_linked_view(lang, AUTH_URL, ACTIVE_MEETINGS, SCHEDULING_DAYS)
    expected = MitupView(
        description=CollaborateMessages.NOT_LINKED.get(
            lang=lang, active_meetings=ACTIVE_MEETINGS, scheduling_days=SCHEDULING_DAYS
        ),
        keyboard=[[ButtonConfig(text=ButtonMessages.LINK_PATREON.get_text(lang=lang), url=AUTH_URL)]],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)

    assert view == expected


def test_not_linked_view_substitutes_patron_caps(lang: str):
    view = collaborate_not_linked_view(lang, AUTH_URL, ACTIVE_MEETINGS, SCHEDULING_DAYS)

    assert "${" not in view.description.text
    assert str(ACTIVE_MEETINGS) in view.description.text
    assert str(SCHEDULING_DAYS) in view.description.text


def test_linked_not_patron_view_offers_pledge_and_unlink(lang: str):
    view = collaborate_linked_not_patron_view(lang, PLEDGE_URL, ACTIVE_MEETINGS, SCHEDULING_DAYS)
    expected = MitupView(
        description=CollaborateMessages.LINKED_NOT_PATRON.get(
            lang=lang, active_meetings=ACTIVE_MEETINGS, scheduling_days=SCHEDULING_DAYS
        ),
        keyboard=[
            [ButtonConfig(text=ButtonMessages.BECOME_PATRON.get_text(lang=lang), url=PLEDGE_URL)],
            [ButtonConfig(text=ButtonMessages.UNLINK_PATREON.get_text(lang=lang), callback_data=cb.UNLINK_PATREON)],
        ],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)

    assert view == expected


def test_linked_not_patron_view_substitutes_patron_caps(lang: str):
    view = collaborate_linked_not_patron_view(lang, PLEDGE_URL, ACTIVE_MEETINGS, SCHEDULING_DAYS)

    assert "${" not in view.description.text
    assert str(ACTIVE_MEETINGS) in view.description.text
    assert str(SCHEDULING_DAYS) in view.description.text


@pytest.mark.parametrize(
    "level,expected_message",
    [
        (SupporterLevel.HOST_1, CollaborateMessages.LINKED_PATRON_SUPPORTER),
        (SupporterLevel.HOST_2, CollaborateMessages.LINKED_PATRON_PATRON),
        (SupporterLevel.HOST_3, CollaborateMessages.LINKED_PATRON_ORGANIZER),
    ],
)
def test_linked_patron_view_renders_tier_message_and_only_unlink(
    lang: str, level: SupporterLevel, expected_message: CollaborateMessages
):
    view = collaborate_linked_patron_view(lang, level, ACTIVE_MEETINGS, SCHEDULING_DAYS)
    expected = MitupView(
        description=expected_message.get(lang=lang, active_meetings=ACTIVE_MEETINGS, scheduling_days=SCHEDULING_DAYS),
        keyboard=[
            [ButtonConfig(text=ButtonMessages.UNLINK_PATREON.get_text(lang=lang), callback_data=cb.UNLINK_PATREON)],
        ],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)

    assert view == expected


def test_linked_patron_view_shows_join_group_button_when_not_in_group(lang: str):
    view = collaborate_linked_patron_view(
        lang, SupporterLevel.HOST_1, ACTIVE_MEETINGS, SCHEDULING_DAYS, GROUP_URL, in_group=False
    )
    expected = MitupView(
        description=CollaborateMessages.LINKED_PATRON_SUPPORTER.get(
            lang=lang, active_meetings=ACTIVE_MEETINGS, scheduling_days=SCHEDULING_DAYS
        ),
        keyboard=[
            [ButtonConfig(text=ButtonMessages.HOSTS_GROUP_JOIN.get_text(lang=lang), url=GROUP_URL)],
            [ButtonConfig(text=ButtonMessages.UNLINK_PATREON.get_text(lang=lang), callback_data=cb.UNLINK_PATREON)],
        ],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)

    assert view == expected


def test_linked_patron_view_shows_open_group_button_when_in_group(lang: str):
    view = collaborate_linked_patron_view(
        lang, SupporterLevel.HOST_1, ACTIVE_MEETINGS, SCHEDULING_DAYS, GROUP_URL, in_group=True
    )
    expected = MitupView(
        description=CollaborateMessages.LINKED_PATRON_SUPPORTER.get(
            lang=lang, active_meetings=ACTIVE_MEETINGS, scheduling_days=SCHEDULING_DAYS
        ),
        keyboard=[
            [ButtonConfig(text=ButtonMessages.HOSTS_GROUP_OPEN.get_text(lang=lang), url=GROUP_URL)],
            [ButtonConfig(text=ButtonMessages.UNLINK_PATREON.get_text(lang=lang), callback_data=cb.UNLINK_PATREON)],
        ],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)

    assert view == expected


def test_linked_patron_view_omits_group_row_when_feature_unconfigured(lang: str):
    # No invite url means the feature is disabled; the group row must not render even if in_group is
    # somehow True, and the Unlink button stays the only keyboard action above Back.
    view = collaborate_linked_patron_view(
        lang, SupporterLevel.HOST_1, ACTIVE_MEETINGS, SCHEDULING_DAYS, None, in_group=True
    )
    expected = MitupView(
        description=CollaborateMessages.LINKED_PATRON_SUPPORTER.get(
            lang=lang, active_meetings=ACTIVE_MEETINGS, scheduling_days=SCHEDULING_DAYS
        ),
        keyboard=[
            [ButtonConfig(text=ButtonMessages.UNLINK_PATREON.get_text(lang=lang), callback_data=cb.UNLINK_PATREON)],
        ],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)

    assert view == expected


def test_linked_patron_patron_view_renders_concrete_caps(lang: str):
    # The Patron tier message carries the ${active_meetings} / ${scheduling_days} placeholders; the
    # other two paying tiers have no vars, so only this one needs the substitution check.
    view = collaborate_linked_patron_view(lang, SupporterLevel.HOST_2, ACTIVE_MEETINGS, SCHEDULING_DAYS)

    assert "${" not in view.description.text
    assert str(ACTIVE_MEETINGS) in view.description.text
    assert str(SCHEDULING_DAYS) in view.description.text


@pytest.mark.parametrize(
    "level,expected_message",
    [
        (SupporterLevel.HOST_1, CollaborateMessages.LINKED_PATRON_SUPPORTER),
        (SupporterLevel.HOST_2, CollaborateMessages.LINKED_PATRON_PATRON),
        (SupporterLevel.HOST_3, CollaborateMessages.LINKED_PATRON_ORGANIZER),
    ],
)
def test_status_for_maps_each_paying_tier(level: SupporterLevel, expected_message: CollaborateMessages):
    assert CollaborateMessages.status_for(level) is expected_message


def test_status_for_rejects_none_tier():
    with pytest.raises(ValueError):
        CollaborateMessages.status_for(SupporterLevel.NONE)


def test_readmitted_view_offers_join_and_main_menu(lang: str):
    view = hosts_group_readmitted_view(lang, GROUP_URL)
    expected = MitupView(
        description=SupporterNotificationMessages.HOSTS_GROUP_READMITTED.get(lang=lang),
        keyboard=[[ButtonConfig(text=ButtonMessages.HOSTS_GROUP_JOIN.get_text(lang=lang), url=GROUP_URL)]],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)

    assert view == expected


def test_readmitted_view_keeps_main_menu_when_invite_url_missing(lang: str):
    # A misconfigured feature (no invite url) must still leave the host a way back: the Join row drops
    # but the Main-menu button remains, so the DM is never keyboard-less.
    view = hosts_group_readmitted_view(lang, None)
    expected = MitupView(
        description=SupporterNotificationMessages.HOSTS_GROUP_READMITTED.get(lang=lang),
        keyboard=[],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)

    assert view == expected


def test_removed_view_offers_only_main_menu(lang: str):
    view = hosts_group_removed_view(lang)
    expected = MitupView(
        description=SupporterNotificationMessages.HOSTS_GROUP_REMOVED.get(lang=lang),
        keyboard=[],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)

    assert view == expected
