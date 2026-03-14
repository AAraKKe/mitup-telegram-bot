import pytest

from mitup_bot.utils.entities import (
    Bold,
    BoldItalic,
    DateTimeMessageEntity,
    EntityDateTime,
    FormattedText,
    Italic,
    Link,
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
# render() — nested Template
# ---------------------------------------------------------------------------


def test_render_nested_template_plain_text():
    inner = t"world"
    result = render(t"Hello {inner}!")
    assert result.text == "Hello world!"
    assert result.entities == []


def test_render_nested_template_plain_str_interpolation():
    name = "Alice"
    greeting = t"Hello {name}"
    result = render(t"{greeting}!")
    assert result.text == "Hello Alice!"
    assert result.entities == []


def test_render_nested_template_bold_at_outer_start():
    # Nested template is the very first interpolation — offset 0 is trivially correct.
    inner = t"{Bold('Hi')}"
    result = render(t"{inner} there")
    assert result.text == "Hi there"
    assert len(result.entities) == 1
    e = result.entities[0]
    assert e.type == "bold"
    assert e.offset == 0
    assert e.length == 2


def test_render_nested_template_bold_shifted_by_prefix():
    # Bold lives inside a nested template; the outer prefix must shift its offset.
    inner = t"{Bold('world')}"
    result = render(t"Hello {inner}")
    assert result.text == "Hello world"
    assert len(result.entities) == 1
    e = result.entities[0]
    assert e.type == "bold"
    assert e.offset == 6  # "Hello " = 6 code units
    assert e.length == 5


def test_render_nested_template_emoji_prefix_shifts_entity():
    # 🎉 occupies 2 UTF-16 code units; the offset must account for that.
    inner = t"{Bold('Party')}"
    result = render(t"🎉 {inner}")
    assert result.text == "🎉 Party"
    assert len(result.entities) == 1
    e = result.entities[0]
    assert e.offset == 3  # "🎉 ": 2 + 1 = 3 UTF-16 code units
    assert e.length == 5


def test_render_multiple_nested_templates_independent_entity_offsets():
    # Each nested template is shifted by the text accumulated before it, independently.
    first = t"{Bold('one')}"
    second = t"{Bold('two')}"
    result = render(t"{first} and {second}")
    assert result.text == "one and two"
    assert len(result.entities) == 2
    offsets = sorted(e.offset for e in result.entities)
    assert offsets == [0, 8]  # "one"→0, "two" after "one and "→8


def test_render_deeply_nested_template_entity_offset():
    # Entities shift through every nesting level, accumulating the full prefix length.
    innermost = t"{Bold('deep')}"
    middle = t"mid {innermost}"
    result = render(t"outer {middle} end")
    assert result.text == "outer mid deep end"
    assert len(result.entities) == 1
    e = result.entities[0]
    assert e.type == "bold"
    assert e.offset == 10  # "outer mid " = 10 code units
    assert e.length == 4


def test_render_nested_template_containing_formatted_text_shifts_entities():
    from telegram import MessageEntity

    # FormattedText inside a nested template must also be shifted by the outer prefix.
    ft = FormattedText("bold", [MessageEntity(type="bold", offset=0, length=4)])
    inner = t"{ft} suffix"
    result = render(t"prefix {inner}")
    assert result.text == "prefix bold suffix"
    assert len(result.entities) == 1
    e = result.entities[0]
    assert e.type == "bold"
    assert e.offset == 7  # "prefix " = 7 code units
    assert e.length == 4


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
