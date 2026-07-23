import pytest
from telegram import MessageEntity

from mitup_bot.utils.entities import FormattedText, parse_format_tags, serialize_entities, strip_tags

# ---------------------------------------------------------------------------
# serialize_entities() — literal text escaping
# ---------------------------------------------------------------------------


def test_serialize_entities_plain_text_unchanged():
    assert serialize_entities("plain text", []) == "plain text"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("I <3 you", "I &lt;3 you"),
        ("<b>not a tag</b>", "&lt;b&gt;not a tag&lt;/b&gt;"),
        ("<tg-emoji emoji-id='1'>x</tg-emoji>", "&lt;tg-emoji emoji-id=&#x27;1&#x27;&gt;x&lt;/tg-emoji&gt;"),
        ("a &amp; b", "a &amp;amp; b"),
        ("a & b", "a &amp; b"),
    ],
    ids=["less-than-three", "bold-lookalike", "tg-emoji-lookalike", "character-reference", "bare-ampersand"],
)
def test_serialize_entities_escapes_literal_text(text: str, expected: str):
    assert serialize_entities(text, []) == expected


@pytest.mark.parametrize(
    "text",
    ["I <3 you", "<b>user typed this</b>", "5 &lt; 6 &amp; 7", '<tg-emoji emoji-id="1">x</tg-emoji>'],
    ids=["less-than-three", "bold-lookalike", "escaped-references", "tg-emoji-lookalike"],
)
def test_serialize_entities_escaped_text_round_trips_as_literal(text: str):
    assert parse_format_tags(serialize_entities(text, []), {}) == FormattedText(text)


# ---------------------------------------------------------------------------
# serialize_entities() — entity wrapping and the whitelist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entity_type, tag",
    [
        (MessageEntity.BOLD, "b"),
        (MessageEntity.ITALIC, "i"),
        (MessageEntity.UNDERLINE, "u"),
        (MessageEntity.STRIKETHROUGH, "s"),
        (MessageEntity.SPOILER, "tg-spoiler"),
    ],
    ids=["bold", "italic", "underline", "strikethrough", "spoiler"],
)
def test_serialize_entities_wraps_span_in_tag(entity_type: str, tag: str):
    entity = MessageEntity(type=entity_type, offset=6, length=5)
    assert serialize_entities("hello world", [entity]) == f"hello <{tag}>world</{tag}>"


def test_serialize_entities_custom_emoji_tag_carries_emoji_id():
    entity = MessageEntity(type=MessageEntity.CUSTOM_EMOJI, offset=0, length=2, custom_emoji_id="5368324170671202286")
    assert serialize_entities("👍", [entity]) == '<tg-emoji emoji-id="5368324170671202286">👍</tg-emoji>'


def test_serialize_entities_custom_emoji_without_id_left_unstyled():
    entity = MessageEntity(type=MessageEntity.CUSTOM_EMOJI, offset=0, length=2)
    assert serialize_entities("👍", [entity]) == "👍"


def test_serialize_entities_escapes_attribute_value():
    entity = MessageEntity(type=MessageEntity.CUSTOM_EMOJI, offset=0, length=1, custom_emoji_id='1"2')
    assert serialize_entities("x", [entity]) == '<tg-emoji emoji-id="1&quot;2">x</tg-emoji>'


def test_serialize_entities_drops_non_whitelisted_entity():
    link = MessageEntity(type=MessageEntity.TEXT_LINK, offset=6, length=4, url="https://x.io")
    assert serialize_entities("click here", [link]) == "click here"


def test_serialize_entities_drops_non_whitelisted_but_keeps_whitelisted():
    entities = [
        MessageEntity(type=MessageEntity.CODE, offset=0, length=5),
        MessageEntity(type=MessageEntity.BOLD, offset=6, length=5),
    ]
    assert serialize_entities("first extra", entities) == "first <b>extra</b>"


# ---------------------------------------------------------------------------
# serialize_entities() — nesting, overlap, and UTF-16 offsets
# ---------------------------------------------------------------------------


def test_serialize_entities_nests_inner_entity():
    entities = [
        MessageEntity(type=MessageEntity.BOLD, offset=0, length=8),
        MessageEntity(type=MessageEntity.ITALIC, offset=4, length=4),
    ]
    assert serialize_entities("see this", entities) == "<b>see <i>this</i></b>"


def test_serialize_entities_same_span_entities_nest():
    entities = [
        MessageEntity(type=MessageEntity.BOLD, offset=0, length=2),
        MessageEntity(type=MessageEntity.ITALIC, offset=0, length=2),
    ]
    assert serialize_entities("hi", entities) == "<b><i>hi</i></b>"


def test_serialize_entities_unsorted_input_is_sorted():
    entities = [
        MessageEntity(type=MessageEntity.ITALIC, offset=4, length=4),
        MessageEntity(type=MessageEntity.BOLD, offset=0, length=8),
    ]
    assert serialize_entities("see this", entities) == "<b>see <i>this</i></b>"


def test_serialize_entities_partial_overlap_drops_later_entity():
    # Italic starts inside bold but crosses its end — the earlier-starting entity wins.
    entities = [
        MessageEntity(type=MessageEntity.BOLD, offset=0, length=4),
        MessageEntity(type=MessageEntity.ITALIC, offset=2, length=4),
    ]
    assert serialize_entities("abcdef", entities) == "<b>abcd</b>ef"


def test_serialize_entities_adjacent_entities_do_not_merge():
    entities = [
        MessageEntity(type=MessageEntity.BOLD, offset=0, length=2),
        MessageEntity(type=MessageEntity.ITALIC, offset=2, length=2),
    ]
    assert serialize_entities("abcd", entities) == "<b>ab</b><i>cd</i>"


def test_serialize_entities_offsets_are_utf16_units():
    # "🎉 " = 3 UTF-16 code units, so the bold span starts at offset 3.
    entity = MessageEntity(type=MessageEntity.BOLD, offset=3, length=2)
    assert serialize_entities("🎉 go", [entity]) == "🎉 <b>go</b>"


# ---------------------------------------------------------------------------
# serialize_entities() ∘ parse_format_tags() — round-trip invariant
# ---------------------------------------------------------------------------


def test_serialize_round_trip_bold_premium_emoji_astral_text():
    # 👍 is a premium (custom) emoji; 🎉 and 🚀 sit outside the BMP, so every
    # offset differs between code points and UTF-16 code units.
    text = "🎉 hello 👍 world 🚀"
    entities = [
        MessageEntity(type=MessageEntity.BOLD, offset=3, length=5),
        MessageEntity(type=MessageEntity.CUSTOM_EMOJI, offset=9, length=2, custom_emoji_id="5368324170671202286"),
    ]
    result = parse_format_tags(serialize_entities(text, entities), {})
    assert result == FormattedText(text, entities)
    # MessageEntity equality compares only type/offset/length, so pin the id explicitly.
    emoji_entity = next(e for e in result.entities if e.type == "custom_emoji")
    assert emoji_entity.custom_emoji_id == "5368324170671202286"


def test_serialize_round_trip_drops_non_whitelisted_entity():
    text = "read the docs now"
    bold = MessageEntity(type=MessageEntity.BOLD, offset=0, length=4)
    link = MessageEntity(type=MessageEntity.TEXT_LINK, offset=9, length=4, url="https://x.io")
    result = parse_format_tags(serialize_entities(text, [bold, link]), {})
    assert result == FormattedText(text, [bold])


def test_serialize_round_trip_escaped_markup_inside_entity():
    text = "<b>we</b> & &lt;3"
    bold = MessageEntity(type=MessageEntity.BOLD, offset=0, length=9)  # "<b>we</b>"
    result = parse_format_tags(serialize_entities(text, [bold]), {})
    assert result == FormattedText(text, [bold])


# ---------------------------------------------------------------------------
# strip_tags()
# ---------------------------------------------------------------------------


def test_strip_tags_returns_visible_text():
    assert strip_tags('<b>Hi</b> <a href="https://x.io">there</a>') == "Hi there"


def test_strip_tags_tg_emoji_keeps_fallback_emoji():
    assert strip_tags('<tg-emoji emoji-id="42">😀</tg-emoji> hi') == "😀 hi"


def test_strip_tags_plain_text_unchanged():
    assert strip_tags("no tags here") == "no tags here"


def test_strip_tags_decodes_character_references():
    assert strip_tags("fish &amp; chips") == "fish & chips"
