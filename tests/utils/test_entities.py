import pytest

from mitup_bot.utils.entities import (
    Bold,
    BoldItalic,
    DateTimeMessageEntity,
    EntityDateTime,
    Italic,
    Link,
    parse_md_markers,
    render,
    utf16_len,
)

# ---------------------------------------------------------------------------
# utf16_len
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("", 0),
        ("Party", 5),
        ("🎉", 2),  # outside BMP → 2 UTF-16 code units
        ("🎉 ", 3),  # emoji + space
    ],
    ids=["empty", "ascii", "emoji", "emoji_plus_space"],
)
def test_utf16_len(text: str, expected: int):
    assert utf16_len(text) == expected


# ---------------------------------------------------------------------------
# render()
# ---------------------------------------------------------------------------


def test_render_empty_template():
    text, entities = render(t"")
    assert text == ""
    assert entities == []


def test_render_plain_string_literal():
    text, entities = render(t"Hello, world!")
    assert text == "Hello, world!"
    assert entities == []


def test_render_plain_str_interpolation():
    name = "Alice"
    text, entities = render(t"Hello {name}!")
    assert text == "Hello Alice!"
    assert entities == []


@pytest.mark.parametrize(
    "t_string, expected_text, expected_type, expected_offset, expected_length",
    [
        (t"Say {Bold('hello')}!", "Say hello!", "bold", 4, 5),
        (t"{Italic('slanted')}", "slanted", "italic", 0, 7),
    ],
    ids=["bold_ascii", "italic"],
)
def test_render_single_entity(t_string, expected_text, expected_type, expected_offset, expected_length):
    text, entities = render(t_string)
    assert text == expected_text
    assert len(entities) == 1
    e = entities[0]
    assert e.type == expected_type
    assert e.offset == expected_offset
    assert e.length == expected_length


def test_render_bold_emoji_offset():
    # 🎉 is 2 UTF-16 code units, so "🎉 " is 3 — offset of "Party" must be 3
    text, entities = render(t"🎉 {Bold('Party')}!")
    assert text == "🎉 Party!"
    assert len(entities) == 1
    e = entities[0]
    assert e.type == "bold"
    assert e.offset == 3
    assert e.length == 5


def test_render_bold_italic_produces_two_entities_at_same_span():
    text, entities = render(t"{BoldItalic('both')}")
    assert text == "both"
    assert len(entities) == 2
    assert {e.type for e in entities} == {"bold", "italic"}
    for e in entities:
        assert e.offset == 0
        assert e.length == 4


def test_render_link():
    text, entities = render(t"{Link('Mitup', 'https://example.com')}")
    assert text == "Mitup"
    assert len(entities) == 1
    e = entities[0]
    assert e.type == "text_link"
    assert e.url == "https://example.com"
    assert e.offset == 0
    assert e.length == 5


def test_render_entity_datetime():
    text, entities = render(t"{EntityDateTime('tomorrow', unix_time=9999999)}")
    assert text == "tomorrow"
    assert len(entities) == 1
    e = entities[0]
    assert isinstance(e, DateTimeMessageEntity)
    assert e.offset == 0
    assert e.length == 8
    assert e.unix_time == 9999999


def test_render_entity_datetime_with_format():
    text, entities = render(t"{EntityDateTime('now', unix_time=1, date_time_format='date')}")
    assert text == "now"
    e = entities[0]
    assert isinstance(e, DateTimeMessageEntity)
    assert e.date_time_format == "date"


def test_render_multiple_non_overlapping_entities():
    text, entities = render(t"Hello {Bold('world')} and {Italic('there')}")
    assert text == "Hello world and there"
    assert len(entities) == 2
    bold_e = next(e for e in entities if e.type == "bold")
    italic_e = next(e for e in entities if e.type == "italic")
    assert bold_e.offset == 6  # "Hello " = 6 code units
    assert bold_e.length == 5  # "world"
    assert italic_e.offset == 16  # "Hello world and " = 16 code units
    assert italic_e.length == 5  # "there"


# ---------------------------------------------------------------------------
# DateTimeMessageEntity.to_dict()
# ---------------------------------------------------------------------------


def test_date_time_message_entity_to_dict_basic():
    e = DateTimeMessageEntity(offset=0, length=5, unix_time=1234567890)
    d = e.to_dict()
    assert d.get("type") == "date_time"
    assert d["unix_time"] == 1234567890


@pytest.mark.parametrize(
    "date_time_format, expect_key",
    [
        ("time", True),
        (None, False),
    ],
    ids=["format_present", "format_absent"],
)
def test_date_time_message_entity_to_dict_format(date_time_format: str | None, expect_key: bool):
    e = DateTimeMessageEntity(offset=0, length=5, unix_time=42, date_time_format=date_time_format)
    d = e.to_dict()
    assert ("date_time_format" in d) == expect_key
    if expect_key:
        assert d["date_time_format"] == date_time_format


# ---------------------------------------------------------------------------
# parse_md_markers()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected_text, expected_type, expected_offset, expected_length",
    [
        ("*bold*", "bold", "bold", 0, 4),
        ("_italic_", "italic", "italic", 0, 6),
    ],
    ids=["bold", "italic"],
)
def test_parse_md_markers_single_entity(text, expected_text, expected_type, expected_offset, expected_length):
    result_text, entities = parse_md_markers(text, {})
    assert result_text == expected_text
    assert len(entities) == 1
    assert entities[0].type == expected_type
    assert entities[0].offset == expected_offset
    assert entities[0].length == expected_length


@pytest.mark.parametrize(
    "text, expected_text",
    [
        ("Hello world", "Hello world"),
        ("", ""),
    ],
    ids=["plain_text", "empty_string"],
)
def test_parse_md_markers_no_entities(text: str, expected_text: str):
    result_text, entities = parse_md_markers(text, {})
    assert result_text == expected_text
    assert entities == []


def test_parse_md_markers_bold_and_italic_non_overlapping():
    text, entities = parse_md_markers("*bold* and _italic_", {})
    assert text == "bold and italic"
    assert len(entities) == 2
    bold_e = next(e for e in entities if e.type == "bold")
    italic_e = next(e for e in entities if e.type == "italic")
    assert bold_e.offset == 0
    assert bold_e.length == 4  # "bold"
    assert italic_e.offset == 9  # "bold and " = 9 code units
    assert italic_e.length == 6  # "italic"


def test_parse_md_markers_bold_italic_nested_same_span():
    # _*both*_ must produce two overlapping entities at the same offset/length
    text, entities = parse_md_markers("_*both*_", {})
    assert text == "both"
    assert len(entities) == 2
    assert {e.type for e in entities} == {"bold", "italic"}
    for e in entities:
        assert e.offset == 0
        assert e.length == 4


def test_parse_md_markers_variable_substitution():
    text, entities = parse_md_markers("Hello ${name}!", {"name": "Juan"})
    assert text == "Hello Juan!"
    assert entities == []


def test_parse_md_markers_bold_with_variable_correct_offset():
    # Entity offset must reflect the substituted value's length, not the placeholder length
    text, entities = parse_md_markers("Hello *${name}*", {"name": "Juan"})
    assert text == "Hello Juan"
    assert len(entities) == 1
    e = entities[0]
    assert e.type == "bold"
    assert e.offset == 6  # "Hello " = 6 code units
    assert e.length == 4  # "Juan"


def test_parse_md_markers_backslash_escape_strips_backslash():
    # \( → ( and \) → ) — backslashes must be stripped from output
    text, entities = parse_md_markers("\\(foo\\)", {})
    assert text == "(foo)"
    assert entities == []


def test_parse_md_markers_escaped_marker_chars_do_not_create_entities():
    # \* → * and \_ → _ — escaped marker characters are unescaped but do not create entities
    text, entities = parse_md_markers("\\*not bold\\*", {})
    assert text == "*not bold*"
    assert entities == []


def test_parse_md_markers_meeting_starting_nesting():
    # The MEETING_STARTING message uses _*${meeting_title}*_ — bold-italic nesting
    template = "The meeting _*${meeting_title}*_ is starting soon!"
    text, entities = parse_md_markers(template, {"meeting_title": "Board meeting"})
    assert "Board meeting" in text
    assert len(entities) == 2
    assert {e.type for e in entities} == {"bold", "italic"}
    offsets = {e.offset for e in entities}
    lengths = {e.length for e in entities}
    assert len(offsets) == 1
    assert len(lengths) == 1
    assert offsets.pop() == 12  # "The meeting " = 12 code units
    assert lengths.pop() == 13  # "Board meeting" = 13 code units


def test_parse_md_markers_unpaired_star_produces_no_entity():
    # A lone * that is not paired must not produce an entity
    text, entities = parse_md_markers("Price: 5*3 = 15", {})
    assert text == "Price: 5*3 = 15"
    assert entities == []


# ---------------------------------------------------------------------------
# Emoji / user content — UTF-16 offset correctness
# ---------------------------------------------------------------------------


def test_render_bold_with_emoji_in_entity_text():
    # Entity length must be in UTF-16 units, not Unicode code points
    text, entities = render(t"Join {Bold('🎉 Party')}")
    assert text == "Join 🎉 Party"
    e = entities[0]
    assert e.offset == 5  # "Join " = 5 code units
    assert e.length == 8  # "🎉 Party": emoji=2 + space+5 chars = 8 code units


def test_parse_md_markers_emoji_prefix_shifts_entity_offset():
    # Emoji appearing as plain text before a marker — offset must count UTF-16 units
    text, entities = parse_md_markers("🎉 *bold*", {})
    assert text == "🎉 bold"
    e = entities[0]
    assert e.offset == 3  # "🎉 ": emoji=2 + space=1, not 2 (code points)
    assert e.length == 4


def test_parse_md_markers_emoji_in_variable_value_entity_length():
    # Entity spanning a variable whose substituted value contains emoji — length in UTF-16 units
    text, entities = parse_md_markers("Meeting: *${title}*", {"title": "🎉 Kickoff"})
    assert text == "Meeting: 🎉 Kickoff"
    e = entities[0]
    assert e.offset == 9  # "Meeting: " = 9 code units
    assert e.length == 10  # "🎉 Kickoff": emoji=2 + space+7 chars = 10 code units


def test_parse_md_markers_emoji_variable_shifts_subsequent_entity_offset():
    # A user-supplied flag emoji shifts the offset of a subsequent formatting entity.
    # 🇪🇸 is 2 regional indicator symbols → 4 UTF-16 code units, so "🇪🇸 " = 5.
    text, entities = parse_md_markers("${name} *is ready*", {"name": "🇪🇸"})
    assert text == "🇪🇸 is ready"
    e = entities[0]
    assert e.offset == 5  # "🇪🇸 ": 2 regional indicators × 2 units each + space = 5, not 2
    assert e.length == 8  # "is ready" = 8 code units
