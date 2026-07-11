import datetime as dt
import re

import pytest

from mitup_bot.callback_data import (
    CallbackData,
    DateCallbackData,
    MeetingCallbackData,
    MeetingListSource,
    PaginatedCallbackData,
)


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


@pytest.mark.parametrize(
    "callback_data",
    [
        CallbackData(entity="meeting", action="edit"),
        DateCallbackData(entity="meeting", action="edit"),
        MeetingCallbackData(entity="meeting", action="edit"),
        PaginatedCallbackData(entity="meeting", action="show"),
    ],
    ids=lambda cb: type(cb).__name__,
)
def test_pattern_is_anchored_pattern_body(callback_data: CallbackData):
    """Every subclass composes its wire format through `pattern_body`; `pattern` adds the
    anchors exactly once, so no subclass can end up double-anchored or unanchored."""
    assert callback_data.pattern == f"^{callback_data.pattern_body}$"
    assert "^" not in callback_data.pattern_body
    assert "$" not in callback_data.pattern_body


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


def test_paginated_callback_data_pattern():
    cb = PaginatedCallbackData(entity="past_meeting", action="show")
    assert cb.pattern == (
        r"^(?P<action>show);(?P<entity>past_meeting):(?P<id>\d*)"
        r"(?:;page:(?P<page>\d+))?(?:;src:(?P<source>[aj]))?$"
    )


@pytest.mark.parametrize(
    "callback_data, expected",
    [
        (PaginatedCallbackData(entity="past_meeting", action="show").with_id(42), "show;past_meeting:42"),
        (PaginatedCallbackData(entity="past_meeting", action="show").with_page(42, 3), "show;past_meeting:42;page:3"),
        (
            PaginatedCallbackData(entity="meeting", action="show").with_page(42, 3, MeetingListSource.JOINED),
            "show;meeting:42;page:3;src:j",
        ),
        (PaginatedCallbackData(entity="past_meeting", action="show"), "show;past_meeting:"),
    ],
    ids=["with_id_omits_page", "with_page", "with_page_and_source", "empty"],
)
def test_paginated_callback_data_str(callback_data: PaginatedCallbackData, expected: str):
    assert str(callback_data) == expected


@pytest.mark.parametrize(
    "input_str, expected_id, expected_page, expected_source",
    [
        ("show;past_meeting:42;page:3", 42, 3, None),
        ("show;past_meeting:42;page:3;src:a", 42, 3, MeetingListSource.ACTIVE),
        ("show;past_meeting:42;page:3;src:j", 42, 3, MeetingListSource.JOINED),
        ("show;past_meeting:42", 42, None, None),
        ("show;past_meeting:", None, None, None),
    ],
    ids=["id_and_page", "active_source", "joined_source", "id_without_page", "empty"],
)
def test_paginated_callback_data_matches(
    input_str: str, expected_id: int | None, expected_page: int | None, expected_source: MeetingListSource | None
):
    cb = PaginatedCallbackData(entity="past_meeting", action="show")
    match = re.match(cb.pattern, input_str)
    parsed = cb.parse(match)
    assert parsed.id == expected_id
    assert parsed.page == expected_page
    assert parsed.source == expected_source


def test_paginated_callback_data_rejects_unknown_source():
    """A source outside the known list codes must not match the pattern at all."""
    cb = PaginatedCallbackData(entity="past_meeting", action="show")
    assert re.match(cb.pattern, "show;past_meeting:42;page:3;src:x") is None


def test_paginated_callback_data_with_id_keeps_source():
    """with_id must carry the source along, like it already does for the page."""
    cb = PaginatedCallbackData(entity="meeting", action="show", page=2, source=MeetingListSource.ACTIVE)
    assert str(cb.with_id(7)) == "show;meeting:7;page:2;src:a"


def test_paginated_callback_data_with_id_keeps_wire_format_of_plain_callback():
    """A page-less paginated callback must be indistinguishable on the wire from a plain CallbackData,
    so callbacks that never carried a page stay backward-compatible."""
    paginated = PaginatedCallbackData(entity="meeting", action="show").with_id(7)
    plain = CallbackData(entity="meeting", action="show").with_id(7)
    assert str(paginated) == str(plain)


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
