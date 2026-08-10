import datetime as dt
import logging
import re

import pytest

from mitup_bot.callback_data import (
    CallbackData,
    CodeCallbackData,
    DateCallbackData,
    GrantCallbackData,
    MeetingCallbackData,
    MeetingListSource,
    PaginatedCallbackData,
)
from mitup_bot.patreon.pairing import generate_pairing_code
from tests.helpers.logs import log_record


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
    # The declaration supplies the pattern to the handler and parses what it matched, as in production
    declaration = CallbackData(action=action, entity=entity) if action is not None else CallbackData(entity=entity)

    expected_callback_data = (
        CallbackData(action=action, entity=entity, id=id) if action is not None else CallbackData(entity=entity, id=id)
    )

    pattern = re.compile(declaration.pattern)

    from_match = declaration.parse(pattern.match(data))

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


# --- CodeCallbackData: addressed by an unguessable code instead of a record id ---

CODE_CALLBACK = CodeCallbackData(action="confirm", entity="pl")


def test_code_callback_data_str_carries_the_code_and_no_id():
    assert str(CODE_CALLBACK.with_code("xLd2-mQ7_a")) == "confirm;pl:;code:xLd2-mQ7_a"


def test_code_callback_data_round_trips_through_its_pattern():
    original = CODE_CALLBACK.with_code("xLd2-mQ7_a")

    parsed = CODE_CALLBACK.parse(re.match(CODE_CALLBACK.pattern, str(original)))

    assert parsed.code == "xLd2-mQ7_a"
    assert parsed.action == "confirm"
    assert parsed.entity == "pl"
    # A row id would be a small guessable integer, so the wire format never carries one.
    assert parsed.id is None


def test_code_callback_data_accepts_the_whole_base64url_alphabet():
    code = "AZaz09-_"
    parsed = CODE_CALLBACK.parse(re.match(CODE_CALLBACK.pattern, str(CODE_CALLBACK.with_code(code))))
    assert parsed.code == code


@pytest.mark.parametrize("code", [None, ""])
def test_code_callback_data_without_a_code_is_malformed(code: str | None):
    assert CodeCallbackData(action="confirm", entity="pl", code=code).is_malformed()


def test_code_callback_data_with_a_code_is_well_formed():
    assert not CODE_CALLBACK.with_code("xLd2").is_malformed()


def test_a_full_length_pairing_code_fits_telegrams_callback_budget():
    # Telegram caps callback data at 64 bytes, and the code alone spends 32 of them.
    largest = CODE_CALLBACK.with_code(generate_pairing_code())
    assert len(str(largest).encode()) <= 64


def test_grant_callback_data_pattern():
    cb = GrantCallbackData(entity="grant", action="set", id=42, level=2)
    assert cb.pattern == r"^(?P<action>set);(?P<entity>grant):(?P<id>\d*);lvl:(?P<level>\d*)$"


@pytest.mark.parametrize(
    "input_str, expected_id, expected_level",
    [
        ("set;grant:42;lvl:2", 42, 2),
        ("set;grant:42;lvl:0", 42, 0),
        ("set;grant:;lvl:2", None, 2),
        ("set;grant:42;lvl:", 42, None),
    ],
    ids=["full_content", "rank_zero", "missing_id", "missing_level"],
)
def test_grant_callback_data_matches(input_str: str, expected_id: int | None, expected_level: int | None):
    cb = GrantCallbackData(entity="grant", action="set")
    match = re.match(cb.pattern, input_str)
    assert cb.parse(match).id == expected_id
    assert cb.parse(match).level == expected_level


def test_grant_callback_data_with_level_round_trips():
    cb = GrantCallbackData(entity="grant", action="set").with_level(42, 3)
    assert str(cb) == "set;grant:42;lvl:3"
    parsed = cb.parse(re.match(cb.pattern, str(cb)))
    assert parsed.id == 42
    assert parsed.level == 3


@pytest.mark.parametrize(
    "id, level, malformed",
    [
        (42, 2, False),
        (42, 0, False),
        (None, 2, True),
        (42, None, True),
    ],
    ids=["well_formed", "rank_zero_well_formed", "missing_id", "missing_level"],
)
def test_grant_callback_data_malformed_shapes(id: int | None, level: int | None, malformed: bool):
    assert GrantCallbackData(entity="grant", action="set", id=id, level=level).is_malformed() is malformed


# --- Wire-form aliases: a renamed callback keeps answering its retired wire strings ---

ALIASED_CALLBACK = CallbackData(action="open", entity="meet_end", aliases=(("edit", "meet_duration"),))

# Spelled out here rather than imported from the module under test, so a reworded event name or a
# renamed field fails the assertion instead of travelling silently into it.
ALIAS_LOG_EVENT = "Aliased callback wire form used"
CALLBACK_DATA_LOGGER = "mitup_bot.callback_data"


def callback_data_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Only the records this module emitted, so an absence assertion cannot pass on a stray filter."""
    return [record for record in caplog.records if record.name == CALLBACK_DATA_LOGGER]


def test_a_callback_without_aliases_keeps_its_exact_pattern():
    """The pattern of a callback that declares no alias is a single alternative per part, byte for
    byte. Every registration that renames nothing routes on this exact string."""
    assert CallbackData(action="edit", entity="meeting").pattern == (
        r"^(?P<action>edit);(?P<entity>meeting):(?P<id>\d*)$"
    )


def test_an_aliased_pattern_alternates_each_part():
    assert ALIASED_CALLBACK.pattern == r"^(?P<action>open|edit);(?P<entity>meet_end|meet_duration):(?P<id>\d*)$"


@pytest.mark.parametrize(
    "wire",
    ["open;meet_end:42", "edit;meet_duration:42", "open;meet_duration:42", "edit;meet_end:42"],
    ids=["primary", "retired_form", "primary_action_retired_entity", "retired_action_primary_entity"],
)
def test_an_aliased_pattern_accepts_the_primary_the_alias_and_their_crossbreeds(wire: str):
    """The last two forms never rode any wire: alternating per part admits them as a side effect.
    Pinned so that a later change refusing them is a deliberate one — callback data is
    client-forgeable and trusted no further than the re-authorized id, and a crossbreed reaches the
    same handler as the forms that are real."""
    assert ALIASED_CALLBACK.parse(re.match(ALIASED_CALLBACK.pattern, wire)).id == 42


def test_an_aliased_callback_only_ever_renders_its_primary_form():
    """Aliases are accepted, never produced, so a freshly built keyboard carries no retired string
    and the alias table spends none of Telegram's 64-byte callback budget."""
    callback = CallbackData(action="open", entity="meet_end", aliases=(("edit", "meet_duration_in_minutes"),))

    assert str(callback) == "open;meet_end:"
    assert str(callback.with_id(42)) == "open;meet_end:42"
    assert len(str(callback.with_id(42)).encode()) <= 64


def test_an_alias_hit_logs_one_line_naming_both_wire_forms(caplog: pytest.LogCaptureFixture):
    """This line is the gauge for whether anyone still presses the retired form, so it has to say
    which form arrived and which one now replaces it. Both values come from the closed alias table,
    so neither can carry user text."""
    caplog.set_level(logging.INFO)

    ALIASED_CALLBACK.parse(re.match(ALIASED_CALLBACK.pattern, "edit;meet_duration:42"))

    record = log_record(caplog, ALIAS_LOG_EVENT)
    assert record.levelname == "INFO"
    assert record.__dict__["wire_form"] == "edit;meet_duration"
    assert record.__dict__["primary_form"] == "open;meet_end"
    assert len(callback_data_records(caplog)) == 1


def test_a_primary_form_match_logs_nothing(caplog: pytest.LogCaptureFixture):
    """Anchored on a line that exists: the alias parse proves the module logs at all under this
    capture, so the empty result for the primary form is a real silence and not a missed lookup."""
    caplog.set_level(logging.INFO)

    ALIASED_CALLBACK.parse(re.match(ALIASED_CALLBACK.pattern, "edit;meet_duration:42"))
    assert len(callback_data_records(caplog)) == 1

    caplog.clear()
    ALIASED_CALLBACK.parse(re.match(ALIASED_CALLBACK.pattern, "open;meet_end:42"))
    assert callback_data_records(caplog) == []


def test_the_unknown_path_logs_nothing(caplog: pytest.LogCaptureFixture):
    """`parse(None)` is the path where nothing matched, so there is no wire form to report at all."""
    caplog.set_level(logging.INFO)

    ALIASED_CALLBACK.parse(re.match(ALIASED_CALLBACK.pattern, "edit;meet_duration:42"))
    assert len(callback_data_records(caplog)) == 1

    caplog.clear()
    assert ALIASED_CALLBACK.parse(None).unknown()
    assert callback_data_records(caplog) == []


@pytest.mark.parametrize(
    "alias, expected_pattern, retired_wire",
    [
        (
            ("edit", "meet_duration"),
            r"^(?P<action>edit);(?P<entity>meet_end|meet_duration):(?P<id>\d*)$",
            "edit;meet_duration:42",
        ),
        (
            ("open", "meet_end"),
            r"^(?P<action>edit|open);(?P<entity>meet_end):(?P<id>\d*)$",
            "open;meet_end:42",
        ),
    ],
    ids=["alias_shares_the_action", "alias_shares_the_entity"],
)
def test_an_alias_sharing_a_part_with_the_primary_is_not_repeated_in_the_pattern(
    alias: tuple[str, str], expected_pattern: str, retired_wire: str
):
    """A rename usually touches one part and keeps the other, so the kept part would otherwise show
    up twice in its own alternation."""
    callback = CallbackData(action="edit", entity="meet_end", aliases=(alias,))

    assert callback.pattern == expected_pattern
    assert callback.parse(re.match(callback.pattern, retired_wire)).id == 42
    assert callback.parse(re.match(callback.pattern, "edit;meet_end:42")).id == 42


def test_a_callback_accepts_every_alias_it_declares():
    """A callback renamed more than once has to answer each form it ever shipped, not just the last."""
    callback = CallbackData(action="open", entity="meet_end", aliases=(("edit", "meet_duration"), ("show", "meet_len")))

    assert callback.pattern == (r"^(?P<action>open|edit|show);(?P<entity>meet_end|meet_duration|meet_len):(?P<id>\d*)$")

    wires = ["open;meet_end:42", "edit;meet_duration:42", "show;meet_len:42"]
    assert [callback.parse(re.match(callback.pattern, wire)).id for wire in wires] == [42, 42, 42]


@pytest.mark.parametrize(
    "wire",
    ["open;meet_end:42x", "edit;meet_duration:42x", "open;meet_end:42;extra"],
    ids=["primary_with_trailing_text", "retired_form_with_trailing_text", "primary_with_extra_suffix"],
)
def test_an_aliased_pattern_still_anchors_both_ends(wire: str):
    """Alternation happens inside the body; `pattern` still has to anchor the whole of it, or a
    forged suffix would route as a legitimate press."""
    assert re.match(ALIASED_CALLBACK.pattern, wire) is None


@pytest.mark.parametrize("wire", ["open;meet_end:", "edit;meet_duration:"], ids=["primary", "retired_form"])
def test_an_id_less_aliased_form_parses_with_no_id(wire: str):
    """An empty id is how a bare declaration renders, and it stays the malformed-but-matching case
    on the alias branch too."""
    parsed = ALIASED_CALLBACK.parse(re.match(ALIASED_CALLBACK.pattern, wire))

    assert parsed.id is None
    assert parsed.is_malformed()


def test_a_paginated_subclass_composes_its_suffixes_onto_the_aliased_parts():
    """Aliases sit on the base part of the wire format, so a subclass's own suffixes have to survive
    them, and parsing an instance has to hand back the subclass rather than the base."""
    callback = PaginatedCallbackData(action="show", entity="past_meeting", aliases=(("show", "old_meeting"),))

    assert callback.pattern == (
        r"^(?P<action>show);(?P<entity>past_meeting|old_meeting):(?P<id>\d*)"
        r"(?:;page:(?P<page>\d+))?(?:;src:(?P<source>[aj]))?$"
    )

    parsed = callback.parse(re.match(callback.pattern, "show;old_meeting:42;page:3;src:j"))

    assert isinstance(parsed, PaginatedCallbackData)
    assert (parsed.id, parsed.page, parsed.source) == (42, 3, MeetingListSource.JOINED)


def test_a_date_subclass_composes_its_mandatory_suffix_onto_the_aliased_parts():
    """Unlike the paginated suffixes the date is not optional, so the alternation must not swallow
    the separator that introduces it."""
    callback = DateCallbackData(action="set_date", entity="meet_end", aliases=(("edit", "meet_duration"),))

    parsed = callback.parse(re.match(callback.pattern, "edit;meet_duration:42;date:2024-07-15"))

    assert isinstance(parsed, DateCallbackData)
    assert parsed.date == dt.date(2024, 7, 15)
    assert parsed.id == 42
