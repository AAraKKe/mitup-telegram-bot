import datetime as dt
import re

import pytest

from mitup_bot.callback_data import CallbackData, DateCallbackData, MeetingCallbackData


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


def test_meeting_callback_data_pattern():
    cb = MeetingCallbackData(entity="meeting", action="edit", id=21)
    assert cb.pattern == r"^(?P<action>edit);(?P<entity>meeting):(?P<id>\d*):(?P<meeting_id>\d*)$"


@pytest.mark.parametrize(
    "input_str, expected_id, expected_meeting_id",
    [
        ("edit;meeting:21:10", 21, 10),
        ("edit;meeting:21:", 21, None),
        ("edit;meeting::10", None, 10),
        ("edit;meeting:0:0", 0, 0),
    ],
    ids=["full_content", "missing_meeting_id", "missing_id", "zero_values"],
)
def test_meeting_callback_data_matches(input_str: str, expected_id: int | None, expected_meeting_id: int | None):
    cb = MeetingCallbackData(entity="meeting", action="edit")
    match = re.match(cb.pattern, input_str)
    assert cb.parse(match).id == expected_id
    assert cb.parse(match).meeting_id == expected_meeting_id


def test_meeting_callback_data_with_ids():
    cb = MeetingCallbackData(entity="meeting", action="edit").with_ids(meeting_id=10, id=21)
    assert str(cb) == "edit;meeting:21:10"


def test_meeting_callback_data_with_id():
    cb = MeetingCallbackData(entity="meeting", action="edit").with_ids(meeting_id=10, id=21)
    cb = cb.with_id(10)
    assert str(cb) == "edit;meeting:10:10"


@pytest.mark.parametrize(
    "id, entity, expected",
    [
        (None, "meeting", True),  # id is None → malformed
        (42, "unknown", True),  # unknown entity → malformed
        (42, "meeting", False),  # valid id and known entity → not malformed
    ],
    ids=["none_id", "unknown_entity", "valid"],
)
def test_callback_data_is_malformed(id: int | None, entity: str, expected: bool):
    cb = CallbackData(action="show", entity=entity, id=id)
    assert cb.is_malformed() is expected
