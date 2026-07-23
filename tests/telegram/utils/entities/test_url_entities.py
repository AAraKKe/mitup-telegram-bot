import pytest
from telegram import MessageEntity

from mitup_bot.utils.entities import FormattedText, strip_entity_from_text

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
    # Exercises the `if entity.url` branch in shift_entity.
    e = MessageEntity(type="text_link", offset=0, length=5, url="https://example.com")
    ft = FormattedText("Mitup", [e])
    result = ft.prepend("prefix ")
    assert len(result.entities) == 1
    assert result.entities[0].url == "https://example.com"
    assert result.entities[0].offset == 7  # "prefix " = 7 UTF-16 code units


def test_formatted_text_prepend_preserves_custom_emoji_id():
    e = MessageEntity(type="custom_emoji", offset=0, length=2, custom_emoji_id="777")
    ft = FormattedText("😀", [e])
    result = ft.prepend("hey ")
    assert len(result.entities) == 1
    assert result.entities[0].offset == 4  # "hey " = 4 UTF-16 code units
    assert result.entities[0].custom_emoji_id == "777"


def test_formatted_text_append_preserves_custom_emoji_id():
    suffix_entity = MessageEntity(type="custom_emoji", offset=0, length=2, custom_emoji_id="777")
    result = FormattedText("hey ").append(FormattedText("😀", [suffix_entity]))
    assert result.text == "hey 😀"
    assert len(result.entities) == 1
    assert result.entities[0].offset == 4
    assert result.entities[0].custom_emoji_id == "777"


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


def test_formatted_text_join_preserves_custom_emoji_id():
    emoji_entity = MessageEntity(type="custom_emoji", offset=0, length=2, custom_emoji_id="777")
    result = FormattedText.join(" ", [FormattedText("hi"), FormattedText("😀", [emoji_entity])])
    assert result.text == "hi 😀"
    assert len(result.entities) == 1
    assert result.entities[0].offset == 3  # "hi " = 3 UTF-16 code units
    assert result.entities[0].custom_emoji_id == "777"


# ---------------------------------------------------------------------------
# strip_entity_from_text()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, offset, length, expected",
    [
        # ASCII word at the start
        ("hello world", 0, 5, "world"),
        # ASCII word in the middle
        ("say hello there", 4, 5, "say there"),
        # ASCII word at the end
        ("see you soon", 8, 4, "see you"),
    ],
    ids=["ascii_start", "ascii_middle", "ascii_end"],
)
def test_strip_entity_from_text_ascii(text: str, offset: int, length: int, expected: str):
    entity = MessageEntity(type=MessageEntity.BOLD, offset=offset, length=length)
    assert strip_entity_from_text(text, entity) == expected


def test_strip_entity_from_text_emoji_prefix():
    # "🎉 " = 2 (emoji) + 1 (space) = 3 UTF-16 code units
    # Entity covers "Party" starting at offset 3
    text = "🎉 Party time"
    entity = MessageEntity(type="date_time", offset=3, length=5)  # "Party"
    result = strip_entity_from_text(text, entity)
    assert result == "🎉 time"  # stripped, leading/trailing whitespace removed


def test_strip_entity_from_text_emoji_is_the_entity():
    # The entity itself is the emoji (2 UTF-16 code units, length=2)
    text = "hello 🎉 world"
    entity = MessageEntity(type="date_time", offset=6, length=2)  # "🎉"
    result = strip_entity_from_text(text, entity)
    assert result == "hello world"  # surrounding spaces collapsed by strip()


def test_strip_entity_from_text_strips_surrounding_whitespace():
    # After removal the remaining text may have leading/trailing spaces — they must be stripped
    text = "  tomorrow  "
    entity = MessageEntity(type="date_time", offset=2, length=8)  # "tomorrow"
    result = strip_entity_from_text(text, entity)
    assert result == ""  # only spaces remain after removing "tomorrow"


def test_strip_entity_from_text_multi_codeunit_entity():
    # 🇪🇸 is 2 regional indicator symbols → 4 UTF-16 code units
    # Text: "Meeting 🇪🇸 now", entity covers "🇪🇸" at UTF-16 offset 8, length 4
    text = "Meeting 🇪🇸 now"
    entity = MessageEntity(type="date_time", offset=8, length=4)
    result = strip_entity_from_text(text, entity)
    assert result == "Meeting now"


# ---------------------------------------------------------------------------
# FormattedText.__eq__ — cross-type comparison
# ---------------------------------------------------------------------------


def test_formatted_text_eq_returns_not_implemented_for_non_formatted_text():
    # __eq__ must return NotImplemented (not False) when the other object is not a FormattedText.
    # This lets Python fall back to the reflected comparison on the other object.
    ft = FormattedText("x")
    result = ft.__eq__(42)
    assert result is NotImplemented


def test_formatted_text_ne_non_formatted_text_evaluates_to_true():
    # Confirming that the != operator also reflects correctly for non-FormattedText objects.
    ft = FormattedText("x")
    assert ft != 42
