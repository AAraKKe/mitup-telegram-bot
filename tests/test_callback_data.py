import datetime as dt
import re

import pytest

from mitup_bot.callback_data import CallbackData, DateCallbackData


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


def test_callback_data_match():
    callback_data = CallbackData(entity="meeting", action="edit", id=21)

    assert callback_data.match().groupdict() == {"action": "edit", "entity": "meeting", "id": "21"}


def test_date_callback_data_pattern():
    cb = DateCallbackData(entity="meeting", action="edit", id=21)
    assert cb.pattern == r"^(?P<action>edit);(?P<entity>meeting):(?P<id>\d*);date:(?P<date>\d{4}-\d{2}-\d{2})$"


def test_date_callback_data_matches():
    input_str = "edit;meeting:21;date:2024-07-15"
    # Should behave as a normal cb data adding the id later
    cb = DateCallbackData(entity="meeting", action="edit").with_id(21)
    match = re.match(cb.pattern, input_str)

    assert cb.parse(match).date == dt.date(2024, 7, 15)


def test_date_callback_data_with_date():
    cb = DateCallbackData(entity="meeting", action="edit", id=21).with_date(dt.date(2024, 7, 15))
    assert str(cb) == "edit;meeting:21;date:2024-07-15"
