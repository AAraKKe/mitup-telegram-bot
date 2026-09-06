from contextlib import nullcontext
from typing import cast

import pytest
from pydantic import ValidationError

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


def test_a_label_that_is_not_a_string_raises():
    # cast: a label reaches a button as the plain string every producer renders it to, so anything
    # else is a caller mistake rather than a shape to unwrap.
    text = cast("str", FormattedText("plain text"))

    with pytest.raises(ValidationError):
        ButtonConfig(text=text, callback_data="show;meeting:1")


def test_a_disabled_button_needs_no_action():
    button = ButtonConfig(text="2/5", disabled=True)

    assert button.disabled
    assert button.callback_data is None


@pytest.mark.parametrize(
    "callback_data, url, switch_inline_query, switch_inline_query_current_chat",
    [
        ("show;meeting:1", None, None, None),
        (None, "https://mitup.social/", None, None),
        (None, None, "meeting:1", None),
        (None, None, None, "meeting:1"),
    ],
    ids=["callback_data", "url", "switch_inline_query", "switch_inline_query_current_chat"],
)
def test_a_disabled_button_carrying_an_action_is_rejected(
    callback_data: str | None,
    url: str | None,
    switch_inline_query: str | None,
    switch_inline_query_current_chat: str | None,
):
    with pytest.raises(ValidationError, match="A disabled button answers no tap"):
        ButtonConfig(
            text="2/5",
            disabled=True,
            callback_data=callback_data,
            url=url,
            switch_inline_query=switch_inline_query,
            switch_inline_query_current_chat=switch_inline_query_current_chat,
        )


def test_a_button_is_tappable_unless_it_says_otherwise():
    """`disabled` is additive on a persisted wire format: a stored row that names no such field
    still deserializes to the tappable button it was written as."""
    button = ButtonConfig.model_validate({"text": "Join", "callback_data": "join;meeting:1"})

    assert button.disabled is False


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
