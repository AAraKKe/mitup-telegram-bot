import pytest
from telegram import MessageEntity, User

from mitup_bot.format_tags import strip_format_tags
from mitup_bot.utils.entities import FormattedText, parse_format_tags, serialize_entities, utf16_len
from mitup_bot.utils.rich_message import anchor_markup
from mitup_bot.utils.rich_template import render_rich_tags

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


def test_serialize_entities_text_link_becomes_an_anchor_to_its_url():
    link = MessageEntity(type=MessageEntity.TEXT_LINK, offset=6, length=4, url="https://x.io")
    assert serialize_entities("click here", [link]) == 'click <a href="https://x.io">here</a>'


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


def test_serialize_round_trip_keeps_a_text_link_beside_bold():
    text = "read the docs now"
    bold = MessageEntity(type=MessageEntity.BOLD, offset=0, length=4)
    link = MessageEntity(type=MessageEntity.TEXT_LINK, offset=9, length=4, url="https://x.io")
    result = parse_format_tags(serialize_entities(text, [bold, link]), {})
    assert result == FormattedText(text, [bold, link])
    assert result.entities[1].url == "https://x.io"


def test_serialize_round_trip_drops_non_whitelisted_entity():
    text = "read the docs now"
    bold = MessageEntity(type=MessageEntity.BOLD, offset=0, length=4)
    hashtag = MessageEntity(type=MessageEntity.HASHTAG, offset=9, length=4)
    result = parse_format_tags(serialize_entities(text, [bold, hashtag]), {})
    assert result == FormattedText(text, [bold])


def test_serialize_round_trip_escaped_markup_inside_entity():
    text = "<b>we</b> & &lt;3"
    bold = MessageEntity(type=MessageEntity.BOLD, offset=0, length=9)  # "<b>we</b>"
    result = parse_format_tags(serialize_entities(text, [bold]), {})
    assert result == FormattedText(text, [bold])


# ---------------------------------------------------------------------------
# serialize_entities(): links, read back by every reader of the stored value
# ---------------------------------------------------------------------------

AWKWARD_URLS = [
    "https://x.io/?a=1&b=2",
    'https://x.io/say-"hi"',
    "https://x.io/it's",
    "https://x.io/<tag>",
    "https://café.fr/ñandú?q=é",
]
AWKWARD_URL_IDS = ["ampersand", "double-quote", "single-quote", "less-than", "non-ascii"]


def hidden_link(url: str) -> str:
    return serialize_entities("join us", [MessageEntity(type=MessageEntity.TEXT_LINK, offset=0, length=7, url=url)])


@pytest.mark.parametrize("url", AWKWARD_URLS, ids=AWKWARD_URL_IDS)
def test_text_link_url_round_trips_through_the_entity_parser(url: str):
    parsed = parse_format_tags(hidden_link(url), {})

    assert parsed.text == "join us"
    assert [(entity.type, entity.offset, entity.length, entity.url) for entity in parsed.entities] == [
        (MessageEntity.TEXT_LINK, 0, 7, url)
    ]


@pytest.mark.parametrize("url", AWKWARD_URLS, ids=AWKWARD_URL_IDS)
def test_text_link_url_round_trips_through_the_rich_renderer(url: str):
    """The stored attribute is escaped once, so reading it back must not escape it a second time."""
    opening, closing = anchor_markup(url)
    assert render_rich_tags(hidden_link(url), field="description").html == f"{opening}join us{closing}"


@pytest.mark.parametrize("url", AWKWARD_URLS, ids=AWKWARD_URL_IDS)
def test_text_link_leaves_only_its_visible_text_on_plain_surfaces(url: str):
    assert strip_format_tags(hidden_link(url)) == "join us"


@pytest.mark.parametrize(
    "address, href",
    [
        ("meet.google.com/abc-defg-hij", "https://meet.google.com/abc-defg-hij"),
        ("https://meet.google.com/abc-defg-hij", "https://meet.google.com/abc-defg-hij"),
        ("HTTP://Example.com", "HTTP://Example.com"),
        ("example.com/go?next=https://y.io", "https://example.com/go?next=https://y.io"),
        ("example.com:8443/room", "https://example.com:8443/room"),
        ("192.168.1.10:8080/stream", "https://192.168.1.10:8080/stream"),
        ("http://10.0.0.5:8080/live", "http://10.0.0.5:8080/live"),
    ],
    ids=[
        "without-scheme",
        "with-scheme",
        "upper-case-scheme",
        "scheme-only-in-the-query",
        "port",
        "ip-address-and-port",
        "ip-address-with-scheme",
    ],
)
def test_url_entity_becomes_an_anchor_to_the_text_it_covers(address: str, href: str):
    url = MessageEntity(type=MessageEntity.URL, offset=5, length=utf16_len(address))
    assert serialize_entities(f"Call {address}", [url]) == f'Call <a href="{href}">{address}</a>'


def test_scheme_less_url_reaches_the_card_as_a_link():
    """A rich message is sent with entity detection off, so a bare address is tappable only as an anchor."""
    text = "Call meet.google.com/abc-defg-hij"
    tagged = serialize_entities(text, [MessageEntity(type=MessageEntity.URL, offset=5, length=28)])

    assert render_rich_tags(tagged, field="description").html == (
        'Call <a href="https://meet.google.com/abc-defg-hij">meet.google.com/abc-defg-hij</a>'
    )
    assert parse_format_tags(tagged, {}).entities[0].url == "https://meet.google.com/abc-defg-hij"
    assert strip_format_tags(tagged) == text


def test_mention_is_dropped_with_its_text_kept():
    """An `@name` in a description may be a handle on another network or no account at all."""
    mention = MessageEntity(type=MessageEntity.MENTION, offset=4, length=9)
    assert serialize_entities("ask @mitupbot", [mention]) == "ask @mitupbot"


def test_email_becomes_a_mailto_anchor():
    email = MessageEntity(type=MessageEntity.EMAIL, offset=7, length=11)
    assert serialize_entities("write: ann@mail.io", [email]) == 'write: <a href="mailto:ann@mail.io">ann@mail.io</a>'


def test_link_nests_with_the_styles_around_it():
    entities = [
        MessageEntity(type=MessageEntity.BOLD, offset=0, length=8),
        MessageEntity(type=MessageEntity.TEXT_LINK, offset=4, length=4, url="https://x.io"),
    ]
    assert serialize_entities("see this", entities) == '<b>see <a href="https://x.io">this</a></b>'


@pytest.mark.parametrize(
    "inner",
    [
        MessageEntity(type=MessageEntity.URL, offset=5, length=4),
        MessageEntity(type=MessageEntity.TEXT_LINK, offset=0, length=9, url="https://y.io"),
    ],
    ids=["inside", "same-span"],
)
def test_link_inside_another_link_is_dropped(inner: MessageEntity):
    outer = MessageEntity(type=MessageEntity.TEXT_LINK, offset=0, length=9, url="https://x.io")
    assert serialize_entities("open x.io", [outer, inner]) == '<a href="https://x.io">open x.io</a>'


ASTRAL_TEXT = "🎉 Party at 🏠 home"
ASTRAL_LINK = MessageEntity(type=MessageEntity.TEXT_LINK, offset=12, length=7, url="https://x.io/home")


def test_link_after_and_around_astral_emoji_is_placed_on_utf16_offsets():
    """🎉 and 🏠 each take two UTF-16 units, so code-point offsets would land the anchor one character off."""
    tagged = serialize_entities(ASTRAL_TEXT, [ASTRAL_LINK])

    assert tagged == '🎉 Party at <a href="https://x.io/home">🏠 home</a>'
    parsed = parse_format_tags(tagged, {})
    assert parsed == FormattedText(ASTRAL_TEXT, [ASTRAL_LINK])
    assert parsed.entities[0].url == "https://x.io/home"
    assert render_rich_tags(tagged, field="title").html == '🎉 Party at <a href="https://x.io/home">🏠 home</a>'
    assert strip_format_tags(tagged) == ASTRAL_TEXT


@pytest.mark.parametrize(
    "url",
    [
        "tg://user?id=42",
        "tg://photo?id=42",
        "javascript:alert(1)",
        "#note-1",
        "ftp://x.io",
        "https://",
        "http://[::1",
        "https://@",
        "https://:80",
        "https://x.io/two words",
        "https://x.io/\tpath",
        "https://x.io/\npath",
        "https://x.io/\x00path",
        "https://x.io/\x1fpath",
        None,
    ],
    ids=[
        "user-mention",
        "media-reference",
        "script",
        "in-message-anchor",
        "ftp",
        "no-host",
        "unparseable",
        "credentials-without-host",
        "port-without-host",
        "space",
        "tab",
        "newline",
        "null",
        "unit-separator",
        "no-url",
    ],
)
def test_text_link_a_card_could_not_send_is_dropped_with_its_text_kept(url: str | None):
    link = MessageEntity(type=MessageEntity.TEXT_LINK, offset=0, length=4, url=url)
    assert serialize_entities("here", [link]) == "here"


def test_url_entity_with_a_scheme_a_card_could_not_send_is_dropped():
    address = "ftp://files.example.com"
    assert (
        serialize_entities(address, [MessageEntity(type=MessageEntity.URL, offset=0, length=len(address))]) == address
    )


def test_text_mention_is_dropped_with_its_text_kept():
    """It links to a Telegram account by id, which the meeting card would show to everyone it reaches."""
    mention = MessageEntity(
        type=MessageEntity.TEXT_MENTION, offset=0, length=3, user=User(id=42, first_name="Ann", is_bot=False)
    )
    assert serialize_entities("Ann", [mention]) == "Ann"


# ---------------------------------------------------------------------------
# strip_format_tags() parity
# ---------------------------------------------------------------------------


def test_strip_format_tags_matches_parser_visible_text():
    tagged = (
        '<b>Hi</b> &amp; &lt;3 <tg-emoji emoji-id="42">😀</tg-emoji> ${name} <span class="tg-spoiler">s</span> '
        '<a href="https://x.io/?a=1&amp;b=&quot;2&quot;">go</a>'
    )
    assert strip_format_tags(tagged) == parse_format_tags(tagged, {}).text
