from contextlib import nullcontext
from typing import cast

import pytest
from pydantic import ValidationError
from telegram import MessageEntity

from mitup_bot.callback_data import CallbackData
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.utils.entities import FormattedText


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


def test_url_and_callback_data_are_mutually_exclusive():
    with pytest.raises(ValidationError):
        ButtonConfig(text="both", url="https://example.com", callback_data="show;meeting:1")


def test_no_action_field_raises_validation_error():
    with pytest.raises(ValidationError):
        ButtonConfig(text="some text")


def test_formatted_text_without_entities_is_flattened_to_str():
    # cast: deliberately passing a non-str to exercise the duck-typed before-validator.
    text = cast("str", FormattedText("plain text"))

    button = ButtonConfig(text=text, callback_data="show;meeting:1")

    assert button.text == "plain text"


def test_formatted_text_with_entities_raises():
    text = cast("str", FormattedText("bold", [MessageEntity(type="bold", offset=0, length=4)]))

    with pytest.raises(ValidationError, match="ButtonConfig text should not contain entities"):
        ButtonConfig(text=text, callback_data="show;meeting:1")


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
        actions = [config.callback_data, config.switch_inline_query, config.switch_inline_query_current_chat]
        assert sum(action is not None for action in actions) == 1
