from telegram import MessageEntity

from mitup_bot.utils.entities import FormattedText

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
