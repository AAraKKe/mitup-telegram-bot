from telegram import InlineKeyboardButton

from mitup_bot.callback_data import CallbackData
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.views import to_inline_keyboard_button


def test_inline_keyboard_button():
    button = to_inline_keyboard_button(
        ButtonConfig(text="Some text", callback_data=CallbackData(action="show", entity="meeting", id=234))
    )

    expected_inline_button = InlineKeyboardButton(
        text="Some text", callback_data="show;meeting:234", switch_inline_query=None
    )

    assert expected_inline_button == button


def test_url_button_renders_url():
    button = to_inline_keyboard_button(
        ButtonConfig(text="Link Patreon", url="https://www.patreon.com/oauth2/authorize?state=x")
    )

    assert button == InlineKeyboardButton(text="Link Patreon", url="https://www.patreon.com/oauth2/authorize?state=x")


def test_switch_inline_query_button_renders_switch():
    button = to_inline_keyboard_button(ButtonConfig(text="Share", switch_inline_query="42"))

    assert button == InlineKeyboardButton(text="Share", switch_inline_query="42")


def test_switch_inline_query_current_chat_button_renders_switch():
    button = to_inline_keyboard_button(ButtonConfig(text="Search here", switch_inline_query_current_chat="42"))

    assert button == InlineKeyboardButton(text="Search here", switch_inline_query_current_chat="42")
