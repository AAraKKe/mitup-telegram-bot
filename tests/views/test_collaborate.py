import pytest

from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, CollaborateMessages
from mitup_bot.views import ButtonConfig, MitupView
from mitup_bot.views.collaborate import (
    collaborate_linked_not_patron_view,
    collaborate_linked_patron_view,
    collaborate_not_linked_view,
    collaborate_unavailable_view,
)

AUTH_URL = "https://www.patreon.com/oauth2/authorize?state=abc"
PLEDGE_URL = "https://www.patreon.com/bePatron?c=12345"
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
        keyboard=[[ButtonConfig(text=ButtonMessages.LINK_PATREON.get(lang=lang), url=AUTH_URL)]],
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
            [ButtonConfig(text=ButtonMessages.BECOME_PATRON.get(lang=lang), url=PLEDGE_URL)],
            [ButtonConfig(text=ButtonMessages.UNLINK_PATREON.get(lang=lang), callback_data=cb.UNLINK_PATREON)],
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
        (SupporterLevel.SUPPORTER, CollaborateMessages.LINKED_PATRON_SUPPORTER),
        (SupporterLevel.PATRON, CollaborateMessages.LINKED_PATRON_PATRON),
        (SupporterLevel.ORGANIZER, CollaborateMessages.LINKED_PATRON_ORGANIZER),
    ],
)
def test_linked_patron_view_renders_tier_message_and_only_unlink(
    lang: str, level: SupporterLevel, expected_message: CollaborateMessages
):
    view = collaborate_linked_patron_view(lang, level, ACTIVE_MEETINGS, SCHEDULING_DAYS)
    expected = MitupView(
        description=expected_message.get(lang=lang, active_meetings=ACTIVE_MEETINGS, scheduling_days=SCHEDULING_DAYS),
        keyboard=[
            [ButtonConfig(text=ButtonMessages.UNLINK_PATREON.get(lang=lang), callback_data=cb.UNLINK_PATREON)],
        ],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)

    assert view == expected


def test_linked_patron_patron_view_renders_concrete_caps(lang: str):
    # The Patron tier message carries the ${active_meetings} / ${scheduling_days} placeholders; the
    # other two paying tiers have no vars, so only this one needs the substitution check.
    view = collaborate_linked_patron_view(lang, SupporterLevel.PATRON, ACTIVE_MEETINGS, SCHEDULING_DAYS)

    assert "${" not in view.description.text
    assert str(ACTIVE_MEETINGS) in view.description.text
    assert str(SCHEDULING_DAYS) in view.description.text


@pytest.mark.parametrize(
    "level,expected_message",
    [
        (SupporterLevel.SUPPORTER, CollaborateMessages.LINKED_PATRON_SUPPORTER),
        (SupporterLevel.PATRON, CollaborateMessages.LINKED_PATRON_PATRON),
        (SupporterLevel.ORGANIZER, CollaborateMessages.LINKED_PATRON_ORGANIZER),
    ],
)
def test_status_for_maps_each_paying_tier(level: SupporterLevel, expected_message: CollaborateMessages):
    assert CollaborateMessages.status_for(level) is expected_message


def test_status_for_rejects_none_tier():
    with pytest.raises(ValueError):
        CollaborateMessages.status_for(SupporterLevel.NONE)
