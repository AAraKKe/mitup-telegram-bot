import pytest
from telegram import MessageEntity

from mitup_bot.utils.entities import FormattedText, parse_format_tags

# ---------------------------------------------------------------------------
# parse_format_tags() — tag aliases to existing entity types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tag, entity_type",
    [
        ("strong", "bold"),
        ("em", "italic"),
        ("ins", "underline"),
        ("strike", "strikethrough"),
        ("del", "strikethrough"),
        ("tg-spoiler", "spoiler"),
        ("blockquote", "blockquote"),
    ],
    ids=["strong", "em", "ins", "strike", "del", "tg-spoiler", "blockquote"],
)
def test_parse_format_tags_alias_tag(tag: str, entity_type: str):
    result = parse_format_tags(f"<{tag}>text</{tag}>", {})
    assert result.text == "text"
    assert len(result.entities) == 1
    assert result.entities[0].type == entity_type
    assert result.entities[0].offset == 0
    assert result.entities[0].length == 4


def test_parse_format_tags_alias_matches_canonical_tag():
    # <strong> must produce exactly the same entity as <b>.
    alias = parse_format_tags("<strong>hi</strong>", {})
    canonical = parse_format_tags("<b>hi</b>", {})
    assert alias == canonical


def test_parse_format_tags_mismatched_alias_and_canonical_dropped():
    # An opening <b> closed by </strong> is unbalanced — both are stripped and
    # no entity is emitted (they are tracked under different tag names).
    result = parse_format_tags("<b>hi</strong>", {})
    assert result.text == "hi"
    assert result.entities == []


# ---------------------------------------------------------------------------
# parse_format_tags() — <a href="…"> links
# ---------------------------------------------------------------------------


def test_parse_format_tags_link_basic():
    result = parse_format_tags('<a href="https://example.com">click</a>', {})
    assert result.text == "click"
    assert len(result.entities) == 1
    entity = result.entities[0]
    assert entity.type == "text_link"
    assert entity.offset == 0
    assert entity.length == 5  # "click"
    assert entity.url == "https://example.com"


def test_parse_format_tags_link_with_prefix_offset():
    result = parse_format_tags('Go <a href="https://x.io">here</a> now', {})
    assert result.text == "Go here now"
    entity = result.entities[0]
    assert entity.type == "text_link"
    assert entity.offset == 3  # "Go " = 3 code units
    assert entity.length == 4  # "here"
    assert entity.url == "https://x.io"


def test_parse_format_tags_link_single_quoted_href():
    result = parse_format_tags("<a href='https://example.com'>x</a>", {})
    assert result.entities[0].url == "https://example.com"


def test_parse_format_tags_link_href_value_may_contain_gt():
    # A `>` inside the quoted attribute value must not terminate the tag early.
    result = parse_format_tags('<a href="https://x.io/?a=1&b=2>3">link</a>', {})
    assert result.text == "link"
    assert result.entities[0].url == "https://x.io/?a=1&b=2>3"


def test_parse_format_tags_link_without_href_dropped():
    # An <a> with no href carries no entity; the tag is stripped but text stays.
    result = parse_format_tags("<a>bare</a>", {})
    assert result.text == "bare"
    assert result.entities == []


def test_parse_format_tags_link_spans_variable():
    result = parse_format_tags('<a href="https://x.io">${label}</a>', {"label": "Open"})
    assert result.text == "Open"
    entity = result.entities[0]
    assert entity.type == "text_link"
    assert entity.offset == 0
    assert entity.length == 4
    assert entity.url == "https://x.io"


def test_parse_format_tags_unclosed_link_dropped():
    result = parse_format_tags('<a href="https://x.io">dangling', {})
    assert result.text == "dangling"
    assert result.entities == []


def test_parse_format_tags_link_astral_offset():
    # 🎉 is 2 UTF-16 code units; the link offset and length must be measured in
    # UTF-16 code units, not Unicode code points.
    result = parse_format_tags('🎉 <a href="https://x.io">🎉 go</a>', {})
    assert result.text == "🎉 🎉 go"
    entity = result.entities[0]
    assert entity.offset == 3  # "🎉 " = 2 + 1
    assert entity.length == 5  # "🎉 go" = 2 + 1 + 2
    assert entity.url == "https://x.io"


# ---------------------------------------------------------------------------
# parse_format_tags() — spoiler forms
# ---------------------------------------------------------------------------


def test_parse_format_tags_span_tg_spoiler_class():
    result = parse_format_tags('<span class="tg-spoiler">secret</span>', {})
    assert result.text == "secret"
    assert len(result.entities) == 1
    assert result.entities[0].type == "spoiler"
    assert result.entities[0].offset == 0
    assert result.entities[0].length == 6


def test_parse_format_tags_span_tg_spoiler_among_classes():
    result = parse_format_tags('<span class="foo tg-spoiler">secret</span>', {})
    assert len(result.entities) == 1
    assert result.entities[0].type == "spoiler"


def test_parse_format_tags_span_without_spoiler_class_dropped():
    result = parse_format_tags('<span class="highlight">plain</span>', {})
    assert result.text == "plain"
    assert result.entities == []


def test_parse_format_tags_bare_span_dropped():
    result = parse_format_tags("<span>plain</span>", {})
    assert result.text == "plain"
    assert result.entities == []


def test_parse_format_tags_tg_spoiler_matches_span_form():
    assert parse_format_tags("<tg-spoiler>x</tg-spoiler>", {}) == parse_format_tags(
        '<span class="tg-spoiler">x</span>', {}
    )


# ---------------------------------------------------------------------------
# parse_format_tags() — nesting with new tags
# ---------------------------------------------------------------------------


def test_parse_format_tags_bold_wrapping_link():
    result = parse_format_tags('<b>see <a href="https://x.io">this</a></b>', {})
    assert result.text == "see this"
    assert len(result.entities) == 2
    bold_entity = next(e for e in result.entities if e.type == "bold")
    link_entity = next(e for e in result.entities if e.type == "text_link")
    assert bold_entity.offset == 0
    assert bold_entity.length == 8  # "see this"
    assert link_entity.offset == 4  # "see " = 4
    assert link_entity.length == 4  # "this"
    assert link_entity.url == "https://x.io"


def test_parse_format_tags_blockquote_wrapping_bold():
    result = parse_format_tags("<blockquote>quote <b>bold</b></blockquote>", {})
    assert result.text == "quote bold"
    quote_entity = next(e for e in result.entities if e.type == "blockquote")
    bold_entity = next(e for e in result.entities if e.type == "bold")
    assert quote_entity.offset == 0
    assert quote_entity.length == 10
    assert bold_entity.offset == 6
    assert bold_entity.length == 4


# ---------------------------------------------------------------------------
# parse_format_tags() — backward-compatible literal preservation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "I <3 you",
        "a < b and c > d",
        "value < 10",
        "use ${unknown} here",
        "email <not a tag> stays",
        "protocol <https://example.com> link",
        "generic <3 heart <3",
    ],
    ids=["less-than-three", "bare-lt", "lt-space", "unmatched-var", "space-tag", "url-in-angles", "double-heart"],
)
def test_parse_format_tags_literal_text_preserved(text: str):
    result = parse_format_tags(text, {})
    assert result.text == text
    assert result.entities == []


def test_parse_format_tags_unmatched_variable_preserved_verbatim():
    result = parse_format_tags("Hello ${name}, welcome ${place}!", {"name": "Ann"})
    assert result.text == "Hello Ann, welcome ${place}!"
    assert result.entities == []


def test_parse_format_tags_attributes_do_not_leak_into_text():
    # The rendered text never contains the attribute markup.
    result = parse_format_tags('<a href="https://example.com">visit</a>', {})
    assert "href" not in result.text
    assert result.text == "visit"


def test_parse_format_tags_unknown_hyphenated_tag_dropped():
    # Hyphenated tag names are now recognised syntactically, but an unknown one
    # still produces no entity (only its markup is stripped).
    result = parse_format_tags("<foo-bar>text</foo-bar>", {})
    assert result.text == "text"
    assert result.entities == []


def test_parse_format_tags_formatted_text_substitution_inside_link():
    inner = FormattedText("Bob", [MessageEntity(type="italic", offset=0, length=3)])
    result = parse_format_tags('<a href="https://x.io">${name}</a>', {"name": inner})
    assert result.text == "Bob"
    link_entity = next(e for e in result.entities if e.type == "text_link")
    italic_entity = next(e for e in result.entities if e.type == "italic")
    assert link_entity.url == "https://x.io"
    assert link_entity.offset == 0
    assert link_entity.length == 3
    assert italic_entity.offset == 0
    assert italic_entity.length == 3


# ---------------------------------------------------------------------------
# parse_format_tags() — HTML character reference decoding on plain-text runs
# ---------------------------------------------------------------------------


def test_parse_format_tags_decodes_amp_reference():
    result = parse_format_tags("&amp;", {})
    assert result.text == "&"
    assert result.entities == []


def test_parse_format_tags_escaped_angle_brackets_render_literally():
    # `&lt;i&gt;` is escaped markup, not a real tag: it must render as the literal text "<i>"
    # with no entity, so an operator who types the characters sees them verbatim.
    result = parse_format_tags("&lt;i&gt;", {})
    assert result.text == "<i>"
    assert result.entities == []


def test_parse_format_tags_real_tag_beside_escaped_ampersand():
    # A real <b> tag still formats, while the escaped &amp; in the plain run decodes to "&".
    result = parse_format_tags("<b>Hi</b> &amp; bye", {})
    assert result.text == "Hi & bye"
    assert len(result.entities) == 1
    assert result.entities[0].type == "bold"
    assert result.entities[0].offset == 0
    assert result.entities[0].length == 2  # "Hi"


def test_parse_format_tags_decodes_numeric_reference():
    result = parse_format_tags("It&#39;s", {})
    assert result.text == "It's"
    assert result.entities == []


def test_parse_format_tags_plain_string_without_references_unchanged():
    result = parse_format_tags("plain text no refs", {})
    assert result.text == "plain text no refs"
    assert result.entities == []


def test_parse_format_tags_substitution_value_is_not_unescaped():
    # Substitution values are inserted verbatim: an `&amp;` in a value stays `&amp;`, never decoded.
    result = parse_format_tags("value ${payload}", {"payload": "a &amp; b"})
    assert result.text == "value a &amp; b"
    assert result.entities == []


# ---------------------------------------------------------------------------
# parse_format_tags() — <tg-emoji emoji-id="…"> custom emoji
# ---------------------------------------------------------------------------


def test_parse_format_tags_tg_emoji_basic():
    result = parse_format_tags('<tg-emoji emoji-id="5368324170671202286">👍</tg-emoji>', {})
    assert result.text == "👍"
    assert len(result.entities) == 1
    entity = result.entities[0]
    assert entity.type == "custom_emoji"
    assert entity.offset == 0
    assert entity.length == 2  # 👍 is outside the BMP → 2 UTF-16 code units
    assert entity.custom_emoji_id == "5368324170671202286"


def test_parse_format_tags_tg_emoji_nested_inside_bold():
    result = parse_format_tags('<b>hi <tg-emoji emoji-id="42">😀</tg-emoji></b>', {})
    assert result.text == "hi 😀"
    bold_entity = next(e for e in result.entities if e.type == "bold")
    emoji_entity = next(e for e in result.entities if e.type == "custom_emoji")
    assert bold_entity.offset == 0
    assert bold_entity.length == 5  # "hi " = 3, 😀 = 2
    assert emoji_entity.offset == 3
    assert emoji_entity.length == 2
    assert emoji_entity.custom_emoji_id == "42"


def test_parse_format_tags_tg_emoji_without_id_dropped():
    # A <tg-emoji> with no emoji-id carries no entity; the tag is stripped but text stays.
    result = parse_format_tags("<tg-emoji>😀</tg-emoji>", {})
    assert result.text == "😀"
    assert result.entities == []


def test_parse_format_tags_tg_emoji_empty_id_dropped():
    result = parse_format_tags('<tg-emoji emoji-id="">😀</tg-emoji>', {})
    assert result.text == "😀"
    assert result.entities == []
