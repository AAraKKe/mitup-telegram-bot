from contextlib import nullcontext

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


def test_no_action_field_raises_validation_error():
    with pytest.raises(ValidationError):
        ButtonConfig(text="some text")


@pytest.mark.parametrize("use_callback_data", [True, False], ids=["with_callback_data", "without_callback_data"])
@pytest.mark.parametrize(
    "use_switch_inline_query", [True, False], ids=["with_switch_inline_query", "without_switch_inline_query"]
)
@pytest.mark.parametrize(
    "use_switch_inline_query_current_chat",
    [True, False],
    ids=["with_switch_inline_query_current_chat", "without_switch_inline_query_current_chat"],
)
def test_action_field_mutual_exclusivity(
    use_callback_data: bool,
    use_switch_inline_query: bool,
    use_switch_inline_query_current_chat: bool,
):
    callback_data = CallbackData(action="show", entity="meeting", id=1) if use_callback_data else None
    switch_inline_query = "query" if use_switch_inline_query else None
    switch_inline_query_current_chat = "query" if use_switch_inline_query_current_chat else None

    fields_set = sum([use_callback_data, use_switch_inline_query, use_switch_inline_query_current_chat])
    ctx = pytest.raises(ValidationError) if fields_set != 1 else nullcontext()

    with ctx:
        config = ButtonConfig(
            text="some text",
            callback_data=callback_data,
            switch_inline_query=switch_inline_query,
            switch_inline_query_current_chat=switch_inline_query_current_chat,
        )
        assert config.button is not None
