import pytest
from telegram import MessageEntity

from mitup_bot.utils.entities import FormattedText, parse_format_tags

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
