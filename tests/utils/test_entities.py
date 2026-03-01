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
    parse_format_tags,
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


# ---------------------------------------------------------------------------
# parse_format_tags() — basic
# ---------------------------------------------------------------------------


def test_parse_format_tags_empty_string():
    result = parse_format_tags("", {})
    assert result.text == ""
    assert result.entities == []


def test_parse_format_tags_plain_text_no_tags():
    result = parse_format_tags("Hello, world!", {})
    assert result.text == "Hello, world!"
    assert result.entities == []


@pytest.mark.parametrize(
    "tag, entity_type",
    [
        ("b", "bold"),
        ("i", "italic"),
        ("u", "underline"),
        ("s", "strikethrough"),
        ("code", "code"),
        ("pre", "pre"),
        ("spoiler", "spoiler"),
    ],
    ids=["bold", "italic", "underline", "strikethrough", "code", "pre", "spoiler"],
)
def test_parse_format_tags_each_registered_style(tag: str, entity_type: str):
    result = parse_format_tags(f"<{tag}>text</{tag}>", {})
    assert result.text == "text"
    assert len(result.entities) == 1
    assert result.entities[0].type == entity_type
    assert result.entities[0].offset == 0
    assert result.entities[0].length == 4


def test_parse_format_tags_bold_with_ascii_prefix():
    result = parse_format_tags("Say <b>hello</b>!", {})
    assert result.text == "Say hello!"
    assert len(result.entities) == 1
    e = result.entities[0]
    assert e.type == "bold"
    assert e.offset == 4  # "Say " = 4 code units
    assert e.length == 5  # "hello"


def test_parse_format_tags_multiple_non_overlapping():
    result = parse_format_tags("<b>bold</b> and <i>italic</i>", {})
    assert result.text == "bold and italic"
    assert len(result.entities) == 2
    bold_e = next(e for e in result.entities if e.type == "bold")
    italic_e = next(e for e in result.entities if e.type == "italic")
    assert bold_e.offset == 0
    assert bold_e.length == 4  # "bold"
    assert italic_e.offset == 9  # "bold and " = 9
    assert italic_e.length == 6  # "italic"


# ---------------------------------------------------------------------------
# parse_format_tags() — nesting and overlap
# ---------------------------------------------------------------------------


def test_parse_format_tags_nested_bold_italic_same_span():
    # <b><i>…</i></b> — both entities must cover exactly the same span.
    result = parse_format_tags("<b><i>both</i></b>", {})
    assert result.text == "both"
    assert len(result.entities) == 2
    assert {e.type for e in result.entities} == {"bold", "italic"}
    for e in result.entities:
        assert e.offset == 0
        assert e.length == 4


def test_parse_format_tags_partial_overlap():
    # Bold covers the full span; italic covers only the inner portion.
    result = parse_format_tags("<b>only bold <i>both</i> only bold</b>", {})
    assert result.text == "only bold both only bold"
    assert len(result.entities) == 2
    bold_e = next(e for e in result.entities if e.type == "bold")
    italic_e = next(e for e in result.entities if e.type == "italic")
    assert bold_e.offset == 0
    assert bold_e.length == 24  # full text
    assert italic_e.offset == 10  # "only bold " = 10
    assert italic_e.length == 4  # "both"


def test_parse_format_tags_three_nested_styles():
    result = parse_format_tags("<b><i><u>all three</u></i></b>", {})
    assert result.text == "all three"
    assert len(result.entities) == 3
    assert {e.type for e in result.entities} == {"bold", "italic", "underline"}
    for e in result.entities:
        assert e.offset == 0
        assert e.length == 9  # "all three"


# ---------------------------------------------------------------------------
# parse_format_tags() — variable substitution
# ---------------------------------------------------------------------------


def test_parse_format_tags_variable_substitution_plain():
    result = parse_format_tags("Hello, ${name}!", {"name": "Alice"})
    assert result.text == "Hello, Alice!"
    assert result.entities == []


def test_parse_format_tags_missing_variable_stays_as_is():
    result = parse_format_tags("Hello, ${name}!", {})
    assert result.text == "Hello, ${name}!"
    assert result.entities == []


def test_parse_format_tags_bold_spans_variable():
    result = parse_format_tags("Meeting: <b>${title}</b>", {"title": "Board meeting"})
    assert result.text == "Meeting: Board meeting"
    assert len(result.entities) == 1
    e = result.entities[0]
    assert e.type == "bold"
    assert e.offset == 9  # "Meeting: " = 9
    assert e.length == 13  # "Board meeting"


def test_parse_format_tags_variable_before_tag_shifts_offset():
    result = parse_format_tags("${prefix} <b>bold</b>", {"prefix": "Hi"})
    assert result.text == "Hi bold"
    assert len(result.entities) == 1
    e = result.entities[0]
    assert e.offset == 3  # "Hi " = 3
    assert e.length == 4


def test_parse_format_tags_variable_value_not_interpreted_as_tags():
    # Variable values are flushed as literal text — user-supplied content cannot
    # produce spurious entities even if it looks like a tag.
    result = parse_format_tags("${user} joined", {"user": "<b>evil</b>"})
    assert result.text == "<b>evil</b> joined"
    assert result.entities == []


# ---------------------------------------------------------------------------
# parse_format_tags() — FormattedText substitution values
# ---------------------------------------------------------------------------


def test_parse_format_tags_formatted_text_substitution_preserves_entities():
    # Substituting a FormattedText with an italic entity into a plain template.
    inner = FormattedText("Alice", [MessageEntity(type="italic", offset=0, length=5)])
    result = parse_format_tags("Hello, ${name}!", {"name": inner})
    assert result.text == "Hello, Alice!"
    assert len(result.entities) == 1
    e = result.entities[0]
    assert e.type == "italic"
    assert e.offset == 7  # "Hello, " = 7
    assert e.length == 5  # "Alice"


def test_parse_format_tags_formatted_text_substitution_at_start():
    inner = FormattedText("Bob", [MessageEntity(type="bold", offset=0, length=3)])
    result = parse_format_tags("${name} joined", {"name": inner})
    assert result.text == "Bob joined"
    assert len(result.entities) == 1
    e = result.entities[0]
    assert e.type == "bold"
    assert e.offset == 0
    assert e.length == 3


def test_parse_format_tags_tag_and_formatted_text_substitution_combine():
    # The outer <b> tag spans the whole string including the substituted value.
    # The substituted FormattedText also carries its own italic entity.
    inner = FormattedText("Alice", [MessageEntity(type="italic", offset=0, length=5)])
    result = parse_format_tags("<b>User ${name} joined</b>", {"name": inner})
    assert result.text == "User Alice joined"
    assert len(result.entities) == 2
    bold_e = next(e for e in result.entities if e.type == "bold")
    italic_e = next(e for e in result.entities if e.type == "italic")
    assert bold_e.offset == 0
    assert bold_e.length == 17  # full "User Alice joined"
    assert italic_e.offset == 5  # "User " = 5
    assert italic_e.length == 5  # "Alice"


def test_parse_format_tags_formatted_text_with_emoji_shifts_correctly():
    # 🎉 is 2 UTF-16 code units; the entity offset must account for that.
    inner = FormattedText("🎉", [])  # no inner entities, but emoji affects offset
    bold_inner = FormattedText("bold", [MessageEntity(type="bold", offset=0, length=4)])
    result = parse_format_tags("${emoji} ${word}!", {"emoji": inner, "word": bold_inner})
    assert result.text == "🎉 bold!"
    assert len(result.entities) == 1
    e = result.entities[0]
    assert e.type == "bold"
    assert e.offset == 3  # "🎉 " = 2 + 1 = 3 UTF-16 code units
    assert e.length == 4  # "bold"


def test_parse_format_tags_plain_string_substitution_still_works():
    # Existing plain-string substitutions must be unaffected by the change.
    result = parse_format_tags("<b>${title}</b>", {"title": "Board meeting"})
    assert result.text == "Board meeting"
    assert len(result.entities) == 1
    assert result.entities[0].type == "bold"


def test_parse_format_tags_formatted_text_substitution_no_entities():
    # A FormattedText with no entities behaves identically to a plain string.
    inner = FormattedText("quiet")
    result = parse_format_tags("Say: ${msg}", {"msg": inner})
    assert result.text == "Say: quiet"
    assert result.entities == []


# ---------------------------------------------------------------------------
# parse_format_tags() — UTF-16 offset correctness with emoji
# ---------------------------------------------------------------------------


def test_parse_format_tags_emoji_prefix_shifts_entity_offset():
    # 🎉 is 2 UTF-16 code units; the bold offset must account for that.
    result = parse_format_tags("🎉 <b>Party</b>", {})
    assert result.text == "🎉 Party"
    assert len(result.entities) == 1
    e = result.entities[0]
    assert e.offset == 3  # "🎉 ": emoji=2 + space=1
    assert e.length == 5  # "Party"


def test_parse_format_tags_emoji_inside_bold_span_correct_length():
    # Entity length must be measured in UTF-16 code units, not Unicode code points.
    result = parse_format_tags("<b>🎉 Party</b>", {})
    assert result.text == "🎉 Party"
    e = result.entities[0]
    assert e.offset == 0
    assert e.length == 8  # "🎉 Party": emoji=2 + space+5 chars = 8


def test_parse_format_tags_emoji_variable_shifts_subsequent_offset():
    # 🇪🇸 is 2 regional indicator symbols → 4 UTF-16 code units, so "🇪🇸 " = 5.
    result = parse_format_tags("${name} <b>is ready</b>", {"name": "🇪🇸"})
    assert result.text == "🇪🇸 is ready"
    e = result.entities[0]
    assert e.offset == 5  # "🇪🇸 ": 2 × 2 units + space = 5
    assert e.length == 8  # "is ready"


# ---------------------------------------------------------------------------
# parse_format_tags() — edge cases
# ---------------------------------------------------------------------------


def test_parse_format_tags_unclosed_tag_is_silently_dropped():
    result = parse_format_tags("<b>unclosed", {})
    assert result.text == "unclosed"
    assert result.entities == []


def test_parse_format_tags_unregistered_tag_stripped_no_entity():
    # Tags not present in STYLE_MAP are stripped from the output text but
    # produce no entity — adding a new style only requires a STYLE_MAP entry.
    result = parse_format_tags("<xyz>text</xyz>", {})
    assert result.text == "text"
    assert result.entities == []


def test_parse_format_tags_zero_length_span_produces_no_entity():
    # A tag pair with no content between them must not emit an entity.
    result = parse_format_tags("<b></b>rest", {})
    assert result.text == "rest"
    assert result.entities == []


def test_parse_format_tags_zero_length_variable_produces_no_entity():
    # A bold span whose variable substitution is empty collapses to zero length.
    result = parse_format_tags("<b>${title}</b>", {"title": ""})
    assert result.text == ""
    assert result.entities == []


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


def test_formatted_text_append_formatted_text_merges_entities():
    base_entity = MessageEntity(type="bold", offset=0, length=4)
    suffix_entity = MessageEntity(type="italic", offset=0, length=5)
    base = FormattedText("word", [base_entity])
    suffix = FormattedText("extra", [suffix_entity])
    result = base.append(suffix)
    assert result.text == "wordextra"
    assert len(result.entities) == 2
    assert result.entities[0] == base_entity  # unchanged
    assert result.entities[1].offset == 4  # shifted by utf16_len("word")
    assert result.entities[1].type == "italic"


def test_formatted_text_append_formatted_text_with_emoji_prefix_shifts_correctly():
    # 🎉 is 2 UTF-16 code units, so the suffix entity should be shifted by 2
    base = FormattedText("🎉")
    suffix_entity = MessageEntity(type="bold", offset=0, length=5)
    suffix = FormattedText("hello", [suffix_entity])
    result = base.append(suffix)
    assert result.text == "🎉hello"
    assert len(result.entities) == 1
    assert result.entities[0].offset == 2


def test_formatted_text_append_plain_string_no_new_entities():
    e = MessageEntity(type="bold", offset=0, length=4)
    ft = FormattedText("word", [e])
    result = ft.append(" suffix")
    assert result.text == "word suffix"
    assert result.entities == [e]


def test_formatted_text_prepend_shifts_existing_entities():
    e = MessageEntity(type="bold", offset=0, length=5)
    ft = FormattedText("world", [e])
    result = ft.prepend("Hello ")
    assert result.text == "Hello world"
    assert len(result.entities) == 1
    assert result.entities[0].offset == 6  # "Hello " = 6 UTF-16 code units


def test_formatted_text_prepend_formatted_text_merges_entities():
    prefix_entity = MessageEntity(type="bold", offset=0, length=5)
    body_entity = MessageEntity(type="italic", offset=0, length=5)
    prefix = FormattedText("hello", [prefix_entity])
    body = FormattedText("world", [body_entity])
    result = body.prepend(prefix)
    assert result.text == "helloworld"
    assert len(result.entities) == 2
    assert result.entities[0] == prefix_entity  # prefix entities are not shifted
    assert result.entities[1].offset == 5  # body entity shifted by utf16_len("hello")
    assert result.entities[1].type == "italic"


def test_formatted_text_prepend_formatted_text_with_emoji_shifts_correctly():
    # 🎉 is 2 UTF-16 code units, so the body entity should be shifted by 2
    prefix = FormattedText("🎉")
    body_entity = MessageEntity(type="bold", offset=0, length=5)
    body = FormattedText("hello", [body_entity])
    result = body.prepend(prefix)
    assert result.text == "🎉hello"
    assert len(result.entities) == 1
    assert result.entities[0].offset == 2


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
# FormattedText.join
# ---------------------------------------------------------------------------


def test_formatted_text_join_plain_strings():
    result = FormattedText.join(", ", [FormattedText("a"), FormattedText("b"), FormattedText("c")])
    assert result.text == "a, b, c"
    assert result.entities == []


def test_formatted_text_join_empty_sequence_returns_empty():
    result = FormattedText.join(", ", [])
    assert result.text == ""
    assert result.entities == []


def test_formatted_text_join_single_part():
    e = MessageEntity(type="bold", offset=0, length=5)
    result = FormattedText.join(", ", [FormattedText("hello", [e])])
    assert result.text == "hello"
    assert result.entities == [e]


def test_formatted_text_join_preserves_and_shifts_entities():
    # "Alice" with a bold entity, "Bob" with an italic entity.
    # Joined by "\n  " (3 UTF-16 code units).
    e_alice = MessageEntity(type="bold", offset=0, length=5)
    e_bob = MessageEntity(type="italic", offset=0, length=3)
    result = FormattedText.join("\n  ", [FormattedText("Alice", [e_alice]), FormattedText("Bob", [e_bob])])
    assert result.text == "Alice\n  Bob"
    assert len(result.entities) == 2
    assert result.entities[0] == e_alice  # offset 0, unchanged
    assert result.entities[1].offset == 8  # "Alice\n  " = 5 + 3 = 8 UTF-16 code units
    assert result.entities[1].type == "italic"


def test_formatted_text_join_three_parts_shift_accumulates():
    e_b = MessageEntity(type="bold", offset=0, length=1)
    parts = [FormattedText("A", [e_b]), FormattedText("B"), FormattedText("C")]
    result = FormattedText.join("-", parts)
    assert result.text == "A-B-C"
    # First part entity at offset 0 is unchanged.
    assert result.entities[0].offset == 0


def test_formatted_text_join_accepts_plain_strings():
    result = FormattedText.join(" | ", ["foo", "bar"])
    assert result.text == "foo | bar"
    assert result.entities == []
