import re

import pytest

from mitup_bot.callback_data import CallbackData


@pytest.mark.parametrize(
    "action, entity, id, expected",
    [
        ("show", "meeting", 1, "show;meeting:1"),
        ("edit", "meeting", 99, "edit;meeting:99"),
        ("show", "main_menu", None, "show;main_menu:"),
    ],
    ids=["show_meeting", "edit_meeting", "show_main_menu"],
)
def test_callback_data_str(action: str, entity: str, id: int, expected: str):
    callback_data = CallbackData(entity=entity, action=action, id=id)

    assert str(callback_data) == expected


@pytest.mark.parametrize(
    "action, entity, id, data",
    [
        ("edit", "meeting", 21, "edit;meeting:21"),
        (None, "main_menu", None, "show;main_menu:"),
    ],
    ids=["valid_full_content", "valid_missing_fields"],
)
def test_callback_data_pattern_recognizes_inputs(action: str | None, entity: str, id: int | None, data: str):
    # This is the patter supplied to the handler
    calllback_pattern = (
        CallbackData(action=action, entity=entity).pattern
        if action is not None
        else CallbackData(entity=entity).pattern
    )

    expected_callback_data = (
        CallbackData(action=action, entity=entity, id=id) if action is not None else CallbackData(entity=entity, id=id)
    )

    pattern = re.compile(calllback_pattern)

    from_match = CallbackData.parse(pattern.match(data))

    assert expected_callback_data == from_match
