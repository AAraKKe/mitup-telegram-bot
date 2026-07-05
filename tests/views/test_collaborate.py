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


def test_unavailable_view_has_no_link_button(lang: str):
    view = collaborate_unavailable_view(lang)
    expected = MitupView(
        description=CollaborateMessages.UNAVAILABLE.get(lang=lang),
        keyboard=[],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)

    assert view == expected


def test_not_linked_view_offers_link_button(lang: str):
    view = collaborate_not_linked_view(lang, AUTH_URL)
    expected = MitupView(
        description=CollaborateMessages.NOT_LINKED.get(lang=lang),
        keyboard=[[ButtonConfig(text=ButtonMessages.LINK_PATREON.get(lang=lang), url=AUTH_URL)]],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)

    assert view == expected


def test_linked_not_patron_view_offers_pledge_and_unlink(lang: str):
    view = collaborate_linked_not_patron_view(lang, PLEDGE_URL)
    expected = MitupView(
        description=CollaborateMessages.LINKED_NOT_PATRON.get(lang=lang),
        keyboard=[
            [ButtonConfig(text=ButtonMessages.BECOME_PATRON.get(lang=lang), url=PLEDGE_URL)],
            [ButtonConfig(text=ButtonMessages.UNLINK_PATREON.get(lang=lang), callback_data=cb.UNLINK_PATREON)],
        ],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)

    assert view == expected


def test_linked_patron_view_offers_only_unlink(lang: str):
    view = collaborate_linked_patron_view(lang)
    expected = MitupView(
        description=CollaborateMessages.LINKED_PATRON.get(lang=lang),
        keyboard=[
            [ButtonConfig(text=ButtonMessages.UNLINK_PATREON.get(lang=lang), callback_data=cb.UNLINK_PATREON)],
        ],
    ).with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)

    assert view == expected
