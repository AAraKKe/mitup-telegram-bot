import pytest
from pydantic import ValidationError
from telegram import InlineKeyboardButton

from mitup_bot.callback_data import CallbackData
from mitup_bot.views.mitup_view import ButtonConfig


@pytest.mark.parametrize(
    "action, entity, id",
    [
        ("a" * 65, "b", 1),
        ("a", "b" * 65, 1),
        ("act", "ent", -1),
    ],
    ids=["long_cb_data_action", "long_cb_data_entity", "negative_id"],
)
def test_callback_data_validate_errors(action: str, entity: str, id: int):
    with pytest.raises(ValidationError):
        callback_data = CallbackData(action=action, entity=entity, id=id)
        ButtonConfig(text="Something", callback_data=callback_data)


def test_callback_data_str_validate_errors():
    with pytest.raises(ValidationError):
        callback_data = "a" * 65
        ButtonConfig(text="Something", callback_data=callback_data)


def test_inline_keyboard_button():
    button = ButtonConfig(text="Some text", callback_data=CallbackData(action="show", entity="meeting", id=234)).button

    expected_inline_button = InlineKeyboardButton(
        text="Some text", callback_data="show;meeting:234", switch_inline_query=None
    )

    assert expected_inline_button == button


def test_button_with_none_callback_data():
    expected_button = InlineKeyboardButton("some text")
    actual_button = ButtonConfig(text="some text")

    assert expected_button == actual_button.button
