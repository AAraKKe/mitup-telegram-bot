import pytest
from telegram import MessageEntity

from mitup_bot.utils.entities import (
    Bold,
    BoldItalic,
    DateTimeMessageEntity,
    EntityDateTime,
    FormattedText,
    Italic,
    Link,
    _nearest_utf16,
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
    result = render(t"")
    assert result.text == ""
    assert result.entities == []


def test_render_plain_string_literal():
    result = render(t"Hello, world!")
    assert result.text == "Hello, world!"
    assert result.entities == []


def test_render_plain_str_interpolation():
    name = "Alice"
    result = render(t"Hello {name}!")
    assert result.text == "Hello Alice!"
    assert result.entities == []


@pytest.mark.parametrize(
    "t_string, expected_text, expected_type, expected_offset, expected_length",
    [
        (t"Say {Bold('hello')}!", "Say hello!", "bold", 4, 5),
        (t"{Italic('slanted')}", "slanted", "italic", 0, 7),
    ],
    ids=["bold_ascii", "italic"],
)
def test_render_single_entity(t_string, expected_text, expected_type, expected_offset, expected_length):
    result = render(t_string)
    assert result.text == expected_text
    assert len(result.entities) == 1
    e = result.entities[0]
    assert e.type == expected_type
    assert e.offset == expected_offset
    assert e.length == expected_length


def test_render_bold_emoji_offset():
    # 🎉 is 2 UTF-16 code units, so "🎉 " is 3 — offset of "Party" must be 3
    result = render(t"🎉 {Bold('Party')}!")
    assert result.text == "🎉 Party!"
    assert len(result.entities) == 1
    e = result.entities[0]
    assert e.type == "bold"
    assert e.offset == 3
    assert e.length == 5


def test_render_bold_italic_produces_two_entities_at_same_span():
    result = render(t"{BoldItalic('both')}")
    assert result.text == "both"
    assert len(result.entities) == 2
    assert {e.type for e in result.entities} == {"bold", "italic"}
    for e in result.entities:
        assert e.offset == 0
        assert e.length == 4


def test_render_link():
    result = render(t"{Link('Mitup', 'https://example.com')}")
    assert result.text == "Mitup"
    assert len(result.entities) == 1
    e = result.entities[0]
    assert e.type == "text_link"
    assert e.url == "https://example.com"
    assert e.offset == 0
    assert e.length == 5


def test_render_entity_datetime():
    result = render(t"{EntityDateTime('tomorrow', unix_time=9999999)}")
    assert result.text == "tomorrow"
    assert len(result.entities) == 1
    e = result.entities[0]
    assert isinstance(e, DateTimeMessageEntity)
    assert e.offset == 0
    assert e.length == 8
    assert e.unix_time == 9999999


def test_render_entity_datetime_with_format():
    result = render(t"{EntityDateTime('now', unix_time=1, date_time_format='date')}")
    assert result.text == "now"
    e = result.entities[0]
    assert isinstance(e, DateTimeMessageEntity)
    assert e.date_time_format == "date"


def test_render_multiple_non_overlapping_entities():
    result = render(t"Hello {Bold('world')} and {Italic('there')}")
    assert result.text == "Hello world and there"
    assert len(result.entities) == 2
    bold_e = next(e for e in result.entities if e.type == "bold")
    italic_e = next(e for e in result.entities if e.type == "italic")
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
    result = parse_md_markers(text, {})
    assert result.text == expected_text
    assert len(result.entities) == 1
    assert result.entities[0].type == expected_type
    assert result.entities[0].offset == expected_offset
    assert result.entities[0].length == expected_length


@pytest.mark.parametrize(
    "text, expected_text",
    [
        ("Hello world", "Hello world"),
        ("", ""),
    ],
    ids=["plain_text", "empty_string"],
)
def test_parse_md_markers_no_entities(text: str, expected_text: str):
    result = parse_md_markers(text, {})
    assert result.text == expected_text
    assert result.entities == []


def test_parse_md_markers_bold_and_italic_non_overlapping():
    result = parse_md_markers("*bold* and _italic_", {})
    assert result.text == "bold and italic"
    assert len(result.entities) == 2
    bold_e = next(e for e in result.entities if e.type == "bold")
    italic_e = next(e for e in result.entities if e.type == "italic")
    assert bold_e.offset == 0
    assert bold_e.length == 4  # "bold"
    assert italic_e.offset == 9  # "bold and " = 9 code units
    assert italic_e.length == 6  # "italic"


def test_parse_md_markers_bold_italic_nested_same_span():
    # _*both*_ must produce two overlapping entities at the same offset/length
    result = parse_md_markers("_*both*_", {})
    assert result.text == "both"
    assert len(result.entities) == 2
    assert {e.type for e in result.entities} == {"bold", "italic"}
    for e in result.entities:
        assert e.offset == 0
        assert e.length == 4


def test_parse_md_markers_variable_substitution():
    result = parse_md_markers("Hello ${name}!", {"name": "Juan"})
    assert result.text == "Hello Juan!"
    assert result.entities == []


def test_parse_md_markers_bold_with_variable_correct_offset():
    # Entity offset must reflect the substituted value's length, not the placeholder length
    result = parse_md_markers("Hello *${name}*", {"name": "Juan"})
    assert result.text == "Hello Juan"
    assert len(result.entities) == 1
    e = result.entities[0]
    assert e.type == "bold"
    assert e.offset == 6  # "Hello " = 6 code units
    assert e.length == 4  # "Juan"


def test_parse_md_markers_backslash_escape_strips_backslash():
    # \( → ( and \) → ) — backslashes must be stripped from output
    result = parse_md_markers("\\(foo\\)", {})
    assert result.text == "(foo)"
    assert result.entities == []


def test_parse_md_markers_escaped_marker_chars_do_not_create_entities():
    # \* → * and \_ → _ — escaped marker characters are unescaped but do not create entities
    result = parse_md_markers("\\*not bold\\*", {})
    assert result.text == "*not bold*"
    assert result.entities == []


def test_parse_md_markers_meeting_starting_nesting():
    # The MEETING_STARTING message uses _*${meeting_title}*_ — bold-italic nesting
    template = "The meeting _*${meeting_title}*_ is starting soon!"
    result = parse_md_markers(template, {"meeting_title": "Board meeting"})
    assert "Board meeting" in result.text
    assert len(result.entities) == 2
    assert {e.type for e in result.entities} == {"bold", "italic"}
    offsets = {e.offset for e in result.entities}
    lengths = {e.length for e in result.entities}
    assert len(offsets) == 1
    assert len(lengths) == 1
    assert offsets.pop() == 12  # "The meeting " = 12 code units
    assert lengths.pop() == 13  # "Board meeting" = 13 code units


def test_parse_md_markers_unpaired_star_produces_no_entity():
    # A lone * that is not paired must not produce an entity
    result = parse_md_markers("Price: 5*3 = 15", {})
    assert result.text == "Price: 5*3 = 15"
    assert result.entities == []


# ---------------------------------------------------------------------------
# Emoji / user content — UTF-16 offset correctness
# ---------------------------------------------------------------------------


def test_render_bold_with_emoji_in_entity_text():
    # Entity length must be in UTF-16 units, not Unicode code points
    result = render(t"Join {Bold('🎉 Party')}")
    assert result.text == "Join 🎉 Party"
    e = result.entities[0]
    assert e.offset == 5  # "Join " = 5 code units
    assert e.length == 8  # "🎉 Party": emoji=2 + space+5 chars = 8 code units


def test_parse_md_markers_emoji_prefix_shifts_entity_offset():
    # Emoji appearing as plain text before a marker — offset must count UTF-16 units
    result = parse_md_markers("🎉 *bold*", {})
    assert result.text == "🎉 bold"
    e = result.entities[0]
    assert e.offset == 3  # "🎉 ": emoji=2 + space=1, not 2 (code points)
    assert e.length == 4


def test_parse_md_markers_emoji_in_variable_value_entity_length():
    # Entity spanning a variable whose substituted value contains emoji — length in UTF-16 units
    result = parse_md_markers("Meeting: *${title}*", {"title": "🎉 Kickoff"})
    assert result.text == "Meeting: 🎉 Kickoff"
    e = result.entities[0]
    assert e.offset == 9  # "Meeting: " = 9 code units
    assert e.length == 10  # "🎉 Kickoff": emoji=2 + space+7 chars = 10 code units


def test_parse_md_markers_emoji_variable_shifts_subsequent_entity_offset():
    # A user-supplied flag emoji shifts the offset of a subsequent formatting entity.
    # 🇪🇸 is 2 regional indicator symbols → 4 UTF-16 code units, so "🇪🇸 " = 5.
    result = parse_md_markers("${name} *is ready*", {"name": "🇪🇸"})
    assert result.text == "🇪🇸 is ready"
    e = result.entities[0]
    assert e.offset == 5  # "🇪🇸 ": 2 regional indicators × 2 units each + space = 5, not 2
    assert e.length == 8  # "is ready" = 8 code units


# ---------------------------------------------------------------------------
# FormattedText — offset manipulation
# ---------------------------------------------------------------------------


def test_formatted_text_append_returns_new_instance_with_same_entities():
    e = MessageEntity(type="bold", offset=0, length=4)
    ft = FormattedText("word", [e])
    result = ft.append(" more text")
    assert result.entities == [e]
    assert result is not ft
    assert result.text == "word more text"


def test_formatted_text_prepend_shifts_existing_entities():
    e = MessageEntity(type="bold", offset=0, length=5)
    ft = FormattedText("world", [e])
    result = ft.prepend("Hello ")
    assert result.text == "Hello world"
    assert len(result.entities) == 1
    assert result.entities[0].offset == 6  # "Hello " = 6 UTF-16 code units


def test_formatted_text_prepend_preserves_url_on_link_entity():
    # Exercises the `if entity.url` branch in _shift_entity.
    e = MessageEntity(type="text_link", offset=0, length=5, url="https://example.com")
    ft = FormattedText("Mitup", [e])
    result = ft.prepend("prefix ")
    assert len(result.entities) == 1
    assert result.entities[0].url == "https://example.com"
    assert result.entities[0].offset == 7  # "prefix " = 7 UTF-16 code units


def test_formatted_text_prepend_no_entities():
    ft = FormattedText("hello")
    result = ft.prepend("Say: ")
    assert result.text == "Say: hello"
    assert result.entities == []


# ---------------------------------------------------------------------------
# _nearest_utf16 — fallback branch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mapping, pos, expected",
    [
        ({0: 0, 1: 2, 5: 10}, 3, 2),  # falls back to mapping[1]
        ({5: 10}, 3, 0),  # no smaller key → default 0
    ],
    ids=["fallback_to_nearest", "fallback_to_zero"],
)
def test_nearest_utf16_fallback(mapping: dict, pos: int, expected: int):
    assert _nearest_utf16(mapping, pos) == expected


# ---------------------------------------------------------------------------
# Zero-length span — skipped in _spans_to_entities
# ---------------------------------------------------------------------------


def test_parse_md_markers_empty_variable_in_bold_produces_no_entity():
    # An empty substitution collapses the bold span to zero length; must be skipped.
    result = parse_md_markers("*${title}*", {"title": ""})
    assert result.text == ""
    assert result.entities == []


# ---------------------------------------------------------------------------
# Escaped italic markers — skipped in _collect_marker_spans
# ---------------------------------------------------------------------------


def test_parse_md_markers_escaped_italic_chars_do_not_create_entities():
    # \_ → _ — escaped marker characters are unescaped but do not create entities
    result = parse_md_markers("\\_not italic\\_", {})
    assert result.text == "_not italic_"
    assert result.entities == []


def test_parse_md_markers_escaped_closing_bold_marker_produces_no_entity():
    # The closing \* is escaped; the * pair is incomplete, so no bold entity is created.
    result = parse_md_markers("*almost bold\\*", {})
    assert result.text == "*almost bold*"
    assert result.entities == []
