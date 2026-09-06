import datetime as dt

import pytest
from telegram import MessageEntity, User

from mitup_bot.keyboards import ButtonConfig
from mitup_bot.utils.entities import FormattedText, parse_format_tags
from mitup_bot.utils.rich_message import (
    DOCUMENT_ATTACH_NAME,
    MalformedRichEntity,
    OverlappingRichSpans,
    RichContent,
    RichDocument,
    RichMessagePayload,
    RichPhoto,
    RichTag,
    UncarriedRichPhoto,
    UnsupportedClassicButton,
    UnsupportedRichButton,
    UnsupportedRichEntity,
    button_markup,
    classic_markup,
    collage_content,
    formatted_text_to_rich_html,
    keyboard_markup,
    photo_content,
    rich_text,
    rich_text_length,
    slideshow_content,
)

# A party popper is outside the BMP: one character, two UTF-16 code units.
EMOJI = "🎉"

MOMENT = dt.datetime(2026, 3, 1, 12, 0, tzinfo=dt.UTC)
MOMENT_UNIX = 1772366400


def rich_html(text: str, *entities: MessageEntity) -> str:
    return formatted_text_to_rich_html(FormattedText(text, list(entities)))


# ---------------------------------------------------------------------------
# Escaping
# ---------------------------------------------------------------------------


def test_plain_text_without_entities_is_unchanged():
    assert rich_html("just words") == "just words"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("a & b", "a &amp; b"),
        ("5 < 6", "5 &lt; 6"),
        ("7 > 6", "7 &gt; 6"),
        ("<b>user typed this</b>", "&lt;b&gt;user typed this&lt;/b&gt;"),
        ("a &amp; b", "a &amp;amp; b"),
    ],
    ids=["ampersand", "less-than", "greater-than", "bold-lookalike", "character-reference"],
)
def test_text_content_escapes_the_markup_characters(text: str, expected: str):
    assert rich_html(text) == expected


def test_text_content_keeps_quotes_verbatim():
    """Quotes only matter inside an attribute; escaping them in content would bloat every payload."""
    assert rich_html('she said "it\'s fine"') == 'she said "it\'s fine"'


def test_text_inside_an_entity_is_escaped_too():
    bold = MessageEntity(type=MessageEntity.BOLD, offset=0, length=5)

    assert rich_html("a < b", bold) == "<b>a &lt; b</b>"


@pytest.mark.parametrize(
    "url, expected_href",
    [
        ("https://x.io/?a=1&b=2", "https://x.io/?a=1&amp;b=2"),
        ('https://x.io/"onmouseover=', "https://x.io/&quot;onmouseover="),
        ("https://x.io/'quoted", "https://x.io/&#x27;quoted"),
    ],
    ids=["ampersand", "double-quote", "single-quote"],
)
def test_attribute_values_escape_quotes_and_ampersands(url: str, expected_href: str):
    link = MessageEntity(type=MessageEntity.TEXT_LINK, offset=0, length=4, url=url)

    assert rich_html("here", link) == f'<a href="{expected_href}">here</a>'


def test_custom_emoji_id_is_escaped_as_an_attribute():
    entity = MessageEntity(type=MessageEntity.CUSTOM_EMOJI, offset=0, length=1, custom_emoji_id='1"2')

    assert rich_html("x", entity) == '<tg-emoji emoji-id="1&quot;2">x</tg-emoji>'


def test_pre_language_is_escaped_as_an_attribute():
    entity = MessageEntity(type=MessageEntity.PRE, offset=0, length=1, language='py"thon')

    assert rich_html("x", entity) == '<pre><code class="language-py&quot;thon">x</code></pre>'


# ---------------------------------------------------------------------------
# Entity type mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entity_type, tag",
    [
        (MessageEntity.BOLD, "b"),
        (MessageEntity.ITALIC, "i"),
        (MessageEntity.UNDERLINE, "u"),
        (MessageEntity.STRIKETHROUGH, "s"),
        (MessageEntity.CODE, "code"),
        (MessageEntity.SPOILER, "tg-spoiler"),
        (MessageEntity.BLOCKQUOTE, "blockquote"),
    ],
    ids=["bold", "italic", "underline", "strikethrough", "code", "spoiler", "blockquote"],
)
def test_simple_entity_wraps_its_span_in_the_bare_tag(entity_type: str, tag: str):
    entity = MessageEntity(type=entity_type, offset=6, length=5)

    assert rich_html("hello world", entity) == f"hello <{tag}>world</{tag}>"


def test_pre_without_a_language_is_a_bare_pre_block():
    entity = MessageEntity(type=MessageEntity.PRE, offset=0, length=7)

    assert rich_html("x = 1;\n", entity) == "<pre>x = 1;\n</pre>"


def test_pre_with_a_language_nests_a_language_tagged_code_block():
    entity = MessageEntity(type=MessageEntity.PRE, offset=0, length=5, language="python")

    assert rich_html("x = 1", entity) == '<pre><code class="language-python">x = 1</code></pre>'


def test_text_link_becomes_an_anchor_to_its_url():
    entity = MessageEntity(type=MessageEntity.TEXT_LINK, offset=6, length=4, url="https://mitup.app")

    assert rich_html("click here", entity) == 'click <a href="https://mitup.app">here</a>'


def test_text_mention_becomes_an_anchor_to_the_user_deep_link():
    entity = MessageEntity(
        type=MessageEntity.TEXT_MENTION, offset=0, length=3, user=User(id=7, first_name="Ana", is_bot=False)
    )

    assert rich_html("Ana joined", entity) == '<a href="tg://user?id=7">Ana</a> joined'


def test_url_becomes_an_anchor_to_the_text_it_covers():
    entity = MessageEntity(type=MessageEntity.URL, offset=5, length=17)

    assert rich_html("see: https://mitup.app", entity) == 'see: <a href="https://mitup.app">https://mitup.app</a>'


def test_custom_emoji_carries_its_emoji_id():
    entity = MessageEntity(type=MessageEntity.CUSTOM_EMOJI, offset=0, length=2, custom_emoji_id="5368324170671202286")

    assert rich_html("👍", entity) == '<tg-emoji emoji-id="5368324170671202286">👍</tg-emoji>'


def test_date_time_without_a_format_omits_the_format_attribute():
    entity = MessageEntity(type=MessageEntity.DATE_TIME, offset=0, length=5, unix_time=MOMENT)

    assert rich_html("later", entity) == f'<tg-time unix="{MOMENT_UNIX}">later</tg-time>'


def test_date_time_with_a_format_carries_it():
    entity = MessageEntity(type=MessageEntity.DATE_TIME, offset=0, length=5, unix_time=MOMENT, date_time_format="DT")

    assert rich_html("later", entity) == f'<tg-time unix="{MOMENT_UNIX}" format="DT">later</tg-time>'


# ---------------------------------------------------------------------------
# Rejected entities
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entity_type",
    [MessageEntity.BOT_COMMAND, MessageEntity.EXPANDABLE_BLOCKQUOTE, MessageEntity.MENTION, MessageEntity.HASHTAG],
    ids=["bot-command", "expandable-blockquote", "mention", "hashtag"],
)
def test_unmapped_entity_type_raises_naming_the_type(entity_type: str):
    entity = MessageEntity(type=entity_type, offset=0, length=5)

    with pytest.raises(UnsupportedRichEntity, match=f"'{entity_type}'") as raised:
        rich_html("hello", entity)

    assert isinstance(raised.value, ValueError)


def test_unrecognized_entity_type_string_raises_naming_it():
    """A type PTB does not know stays a bare string, and the message must still name it."""
    entity = MessageEntity(type="quantum_underline", offset=0, length=5)

    with pytest.raises(UnsupportedRichEntity, match="'quantum_underline'"):
        rich_html("hello", entity)


@pytest.mark.parametrize(
    "entity, missing",
    [
        (MessageEntity(type=MessageEntity.TEXT_LINK, offset=0, length=5), "url"),
        (MessageEntity(type=MessageEntity.TEXT_MENTION, offset=0, length=5), "user"),
        (MessageEntity(type=MessageEntity.CUSTOM_EMOJI, offset=0, length=5), "custom_emoji_id"),
        (MessageEntity(type=MessageEntity.DATE_TIME, offset=0, length=5), "unix_time"),
    ],
    ids=["text-link", "text-mention", "custom-emoji", "date-time"],
)
def test_mapped_entity_missing_its_attribute_raises_naming_the_attribute(entity: MessageEntity, missing: str):
    with pytest.raises(MalformedRichEntity, match=repr(missing)):
        rich_html("hello", entity)


def test_partially_overlapping_entities_are_rejected():
    # Italic starts inside bold but crosses its end, so no nesting of the two tags is valid.
    entities = [
        MessageEntity(type=MessageEntity.BOLD, offset=0, length=4),
        MessageEntity(type=MessageEntity.ITALIC, offset=2, length=4),
    ]

    with pytest.raises(OverlappingRichSpans, match="italic"):
        rich_html("abcdef", *entities)


# ---------------------------------------------------------------------------
# Nesting and ordering
# ---------------------------------------------------------------------------


def test_bold_containing_italic_nests():
    entities = [
        MessageEntity(type=MessageEntity.BOLD, offset=0, length=8),
        MessageEntity(type=MessageEntity.ITALIC, offset=4, length=4),
    ]

    assert rich_html("see this", *entities) == "<b>see <i>this</i></b>"


def test_entities_covering_the_same_span_nest_longest_first():
    entities = [
        MessageEntity(type=MessageEntity.ITALIC, offset=0, length=2),
        MessageEntity(type=MessageEntity.BOLD, offset=0, length=2),
    ]

    assert rich_html("hi", *entities) == "<i><b>hi</b></i>"


def test_unsorted_entities_are_ordered_before_emission():
    entities = [
        MessageEntity(type=MessageEntity.ITALIC, offset=4, length=4),
        MessageEntity(type=MessageEntity.BOLD, offset=0, length=8),
    ]

    assert rich_html("see this", *entities) == "<b>see <i>this</i></b>"


def test_adjacent_entities_do_not_merge():
    entities = [
        MessageEntity(type=MessageEntity.BOLD, offset=0, length=2),
        MessageEntity(type=MessageEntity.ITALIC, offset=2, length=2),
    ]

    assert rich_html("abcd", *entities) == "<b>ab</b><i>cd</i>"


def test_three_levels_of_nesting_close_in_reverse_order():
    entities = [
        MessageEntity(type=MessageEntity.BLOCKQUOTE, offset=0, length=12),
        MessageEntity(type=MessageEntity.BOLD, offset=4, length=8),
        MessageEntity(type=MessageEntity.CODE, offset=8, length=4),
    ]

    assert rich_html("aaaabbbbcccc", *entities) == "<blockquote>aaaa<b>bbbb<code>cccc</code></b></blockquote>"


def test_nesting_the_full_depth_telegram_allows():
    """Telegram caps nesting at 16 levels, so every depth up to that has to serialize."""
    types = [
        MessageEntity.BLOCKQUOTE,
        MessageEntity.BOLD,
        MessageEntity.ITALIC,
        MessageEntity.UNDERLINE,
        MessageEntity.CODE,
    ]
    entities = [MessageEntity(type=entity_type, offset=i, length=10 - 2 * i) for i, entity_type in enumerate(types)]

    expected = "<blockquote>a<b>b<i>c<u>d<code>ef</code>g</u>h</i>i</b>j</blockquote>"
    assert rich_html("abcdefghij", *entities) == expected


def test_entity_reaching_past_the_end_of_the_text_still_closes_its_tag():
    """A span longer than the text it covers must not leave an unclosed tag in the payload.

    Telegram rejects a message whose entity runs past the end of the text, so this is malformed
    input either way, but shipping broken HTML would fail far from the cause.
    """
    entity = MessageEntity(type=MessageEntity.BOLD, offset=0, length=5)

    assert rich_html("hi", entity) == "<b>hi</b>"


# ---------------------------------------------------------------------------
# UTF-16 offsets
# ---------------------------------------------------------------------------


def test_astral_character_before_a_span_does_not_shift_its_tags():
    # "🎉 " is 3 UTF-16 code units but 2 characters, so a code-point walk would open the tag early.
    entity = MessageEntity(type=MessageEntity.BOLD, offset=3, length=2)

    assert rich_html(f"{EMOJI} go", entity) == f"{EMOJI} <b>go</b>"


def test_astral_characters_inside_and_around_several_spans():
    text = f"{EMOJI} hello 👍 world 🚀"
    entities = [
        MessageEntity(type=MessageEntity.BOLD, offset=3, length=5),
        MessageEntity(type=MessageEntity.CUSTOM_EMOJI, offset=9, length=2, custom_emoji_id="42"),
    ]

    expected = f'{EMOJI} <b>hello</b> <tg-emoji emoji-id="42">👍</tg-emoji> world 🚀'
    assert rich_html(text, *entities) == expected


def test_a_span_covering_only_an_astral_character():
    entity = MessageEntity(type=MessageEntity.SPOILER, offset=2, length=2)

    assert rich_html(f"ab{EMOJI}cd", entity) == f"ab<tg-spoiler>{EMOJI}</tg-spoiler>cd"


# ---------------------------------------------------------------------------
# Newlines
# ---------------------------------------------------------------------------


def test_every_newline_in_plain_text_becomes_a_line_break():
    """The parser collapses a bare newline to a space, so a multi-line screen needs `<br/>`."""
    assert rich_html("first\n\nsecond\nthird\n") == "first<br/><br/>second<br/>third<br/>"


def test_newlines_inside_and_between_entities_become_line_breaks():
    entities = [
        MessageEntity(type=MessageEntity.BOLD, offset=0, length=7),
        MessageEntity(type=MessageEntity.ITALIC, offset=9, length=3),
    ]

    assert rich_html("one\ntwo\n\nend", *entities) == "<b>one<br/>two</b><br/><br/><i>end</i>"


@pytest.mark.parametrize(
    "entity_type, tag",
    [
        (MessageEntity.BOLD, "b"),
        (MessageEntity.CODE, "code"),
        (MessageEntity.BLOCKQUOTE, "blockquote"),
    ],
    ids=["bold", "code", "blockquote"],
)
def test_a_newline_inside_an_inline_tag_becomes_a_line_break(entity_type: str, tag: str):
    """`<br/>` survives inside an inline tag; a bare newline collapses there like anywhere else."""
    entity = MessageEntity(type=entity_type, offset=0, length=3)

    assert rich_html("a\nb", entity) == f"<{tag}>a<br/>b</{tag}>"


def test_a_newline_inside_pre_stays_raw():
    """A `<pre>` block keeps a raw newline and swallows a `<br/>`, so it must not be substituted."""
    entity = MessageEntity(type=MessageEntity.PRE, offset=0, length=3)

    assert rich_html("a\nb", entity) == "<pre>a\nb</pre>"


def test_a_newline_inside_a_language_tagged_pre_stays_raw():
    entity = MessageEntity(type=MessageEntity.PRE, offset=0, length=3, language="python")

    assert rich_html("a\nb", entity) == '<pre><code class="language-python">a\nb</code></pre>'


def test_a_newline_nested_deeper_inside_pre_stays_raw():
    """The format-tag dialect can nest an entity inside a `<pre>`, and the interior stays raw.

    `parse_format_tags` tracks each tag on its own, so `<pre>a<b>b</b></pre>` yields a bold entity
    within the pre entity's span.
    """
    nested = parse_format_tags("<pre>a\n<b>b\nc</b></pre>", {})

    assert formatted_text_to_rich_html(nested) == "<pre>a\n<b>b\nc</b></pre>"


def test_newlines_around_a_pre_still_become_line_breaks():
    entity = MessageEntity(type=MessageEntity.PRE, offset=2, length=1)

    assert rich_html("x\ny\nz", entity) == "x<br/><pre>y</pre><br/>z"


def test_empty_text_produces_an_empty_payload():
    assert rich_html("") == ""


# ---------------------------------------------------------------------------
# Structural blocks
# ---------------------------------------------------------------------------


def test_a_footer_is_the_small_muted_close_of_a_block():
    assert RichContent("Created by: Owner").wrap(RichTag.FOOTER).html == "<footer>Created by: Owner</footer>"


# ---------------------------------------------------------------------------
# RichMessagePayload
# ---------------------------------------------------------------------------


def test_payload_defaults_to_skipping_entity_detection():
    assert RichMessagePayload(html="<b>hi</b>").to_api_dict() == {
        "html": "<b>hi</b>",
        "skip_entity_detection": True,
    }


def test_payload_carries_an_explicit_skip_entity_detection():
    assert RichMessagePayload(html="hi", skip_entity_detection=False).to_api_dict() == {
        "html": "hi",
        "skip_entity_detection": False,
    }


def test_payload_is_frozen():
    payload = RichMessagePayload(html="hi")

    with pytest.raises(AttributeError):
        payload.html = "other"  # ty: ignore[invalid-assignment]  # nolink: intentional, the test asserts the frozen dataclass rejects assignment


def test_payload_carries_the_serialized_formatted_text():
    bold = MessageEntity(type=MessageEntity.BOLD, offset=0, length=2)
    payload = RichMessagePayload(html=formatted_text_to_rich_html(FormattedText("hi there", [bold])))

    assert payload.to_api_dict()["html"] == "<b>hi</b> there"


# ---------------------------------------------------------------------------
# The file a message carries
# ---------------------------------------------------------------------------

EXPORT = RichDocument(content=b'{"user": 1}', filename="export.json")


def export_payload() -> RichMessagePayload:
    return RichMessagePayload.from_content(RichContent("your data"), document=EXPORT)


def test_a_payload_carrying_no_file_names_no_media():
    payload = RichMessagePayload.from_content(RichContent("plain"))

    assert "media" not in payload.to_api_dict()
    assert payload.upload_kwargs() == {}


def test_a_carried_file_becomes_a_block_under_the_content():
    assert export_payload().html == 'your data<tg-document src="tg://document?id=document"></tg-document>'


def test_a_carried_file_is_named_in_the_media_list():
    assert export_payload().to_api_dict()["media"] == [
        {"id": "document", "media": {"type": "document", "media": "attach://document_file"}}
    ]


def test_a_carried_file_travels_as_its_own_upload():
    upload = export_payload().upload_kwargs()[DOCUMENT_ATTACH_NAME]

    assert upload.filename == "export.json"
    assert upload.input_file_content == b'{"user": 1}'
    # The uploaded part is named, not attached: PTB sends it under the parameter key, which is the
    # name the media entry's `attach://` resolves.
    assert upload.attach_name is None


def test_the_media_reference_and_the_upload_agree_on_one_name():
    """The two halves are written apart: a block naming a part nobody sends is refused by
    Telegram, so the name has to come off the same constant."""
    payload = export_payload()
    [entry] = payload.to_api_dict()["media"]

    assert entry["media"]["media"] == f"attach://{next(iter(payload.upload_kwargs()))}"


def test_a_carried_file_costs_no_visible_characters():
    """The block renders as an attachment rather than as text, so it cannot push a message that
    fits over the ceiling Telegram counts."""
    assert rich_text_length(export_payload().html) == len("your data")


# ---------------------------------------------------------------------------
# The photos a message shows
# ---------------------------------------------------------------------------

BANNER = RichPhoto(media_id="AQADHRJrGzSd4FB-", file_id="AgACAgQAAxkBAAIB")
SECOND_BANNER = RichPhoto(media_id="AQADHRJrGzSd4FB_", file_id="AgACAgQAAxkBAAIC")


def banner_payload(*photos: RichPhoto) -> RichMessagePayload:
    body = RichContent("the meeting").prepend(collage_content(photo_content(photo.media_id) for photo in photos))
    return RichMessagePayload.from_content(body, photos=photos)


def test_a_photo_renders_as_a_block_naming_its_media_id():
    assert photo_content(BANNER.media_id).html == '<img src="tg://photo?id=AQADHRJrGzSd4FB-"/>'


def test_several_photos_render_inside_one_collage():
    parts = [photo_content(BANNER.media_id), photo_content(SECOND_BANNER.media_id)]

    assert collage_content(parts).html == (
        '<tg-collage><img src="tg://photo?id=AQADHRJrGzSd4FB-"/>'
        '<img src="tg://photo?id=AQADHRJrGzSd4FB_"/></tg-collage>'
    )


def test_several_photos_render_inside_one_slideshow():
    parts = [photo_content(BANNER.media_id), photo_content(SECOND_BANNER.media_id)]

    assert slideshow_content(parts).html == (
        '<tg-slideshow><img src="tg://photo?id=AQADHRJrGzSd4FB-"/>'
        '<img src="tg://photo?id=AQADHRJrGzSd4FB_"/></tg-slideshow>'
    )


def test_a_shown_photo_is_named_in_the_media_list_by_its_file():
    assert banner_payload(BANNER).to_api_dict()["media"] == [
        {"id": "AQADHRJrGzSd4FB-", "media": {"type": "photo", "media": "AgACAgQAAxkBAAIB"}}
    ]


def test_a_carried_file_and_the_photos_share_one_media_list():
    body = RichContent("your data").prepend(photo_content(BANNER.media_id))

    payload = RichMessagePayload.from_content(body, document=EXPORT, photos=[BANNER])

    assert [entry["id"] for entry in payload.to_api_dict()["media"]] == ["document", "AQADHRJrGzSd4FB-"]


def test_a_message_showing_no_photo_names_no_media():
    assert "media" not in RichMessagePayload.from_content(RichContent("plain")).to_api_dict()


def test_a_photo_nobody_points_at_still_travels():
    """The photo list comes from the meeting while the body decides what it draws, so a photo the
    body leaves out is sent without being shown."""
    payload = RichMessagePayload.from_content(RichContent("no banner here"), photos=[BANNER])

    assert payload.to_api_dict()["media"] == [
        {"id": "AQADHRJrGzSd4FB-", "media": {"type": "photo", "media": "AgACAgQAAxkBAAIB"}}
    ]


def test_pointing_at_a_photo_the_message_does_not_carry_is_refused():
    body = RichContent("the meeting").prepend(photo_content(BANNER.media_id))

    with pytest.raises(UncarriedRichPhoto) as raised:
        RichMessagePayload.from_content(body, photos=[SECOND_BANNER])

    assert "AQADHRJrGzSd4FB-" in str(raised.value)


def test_every_uncarried_photo_is_named_in_the_refusal():
    body = collage_content([photo_content(BANNER.media_id), photo_content(SECOND_BANNER.media_id)])

    with pytest.raises(UncarriedRichPhoto) as raised:
        RichMessagePayload.from_content(body)

    assert "AQADHRJrGzSd4FB-" in str(raised.value)
    assert "AQADHRJrGzSd4FB_" in str(raised.value)


def test_a_banner_costs_no_visible_characters():
    """Photos render as blocks, not text, so they do not count toward Telegram's text length limit."""
    assert rich_text_length(banner_payload(BANNER, SECOND_BANNER).html) == len("the meeting")


def test_the_plain_text_projection_drops_the_banner_whole():
    assert rich_text(banner_payload(BANNER, SECOND_BANNER).html) == "the meeting"


def test_the_plain_text_projection_drops_a_slideshow_whole():
    parts = [photo_content(BANNER.media_id), photo_content(SECOND_BANNER.media_id)]

    assert rich_text(slideshow_content(parts).append("the meeting").html) == "the meeting"


# ---------------------------------------------------------------------------
# Buttons inside the content
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "button, expected",
    [
        (
            ButtonConfig(text="Join", callback_data="join;meeting:1"),
            '<tg-button type="callback_data" data="join;meeting:1">Join</tg-button>',
        ),
        (
            ButtonConfig(text="Docs", url="https://mitup.social/user-guide/"),
            '<tg-button type="url" url="https://mitup.social/user-guide/">Docs</tg-button>',
        ),
        (
            ButtonConfig(text="Share", switch_inline_query="meeting:1"),
            '<tg-button type="switch_inline_query" query="meeting:1">Share</tg-button>',
        ),
        (
            ButtonConfig(text="Here", switch_inline_query_current_chat="meeting:1"),
            '<tg-button type="switch_inline_query_current_chat" query="meeting:1">Here</tg-button>',
        ),
    ],
    ids=["callback_data", "url", "switch_inline_query", "switch_inline_query_current_chat"],
)
def test_each_button_action_becomes_its_own_tag_type(button: ButtonConfig, expected: str):
    assert button_markup(button) == expected


def test_a_button_label_keeps_a_bare_newline():
    """A label is one line, and the parser collapses the newline it carries to a space.

    `ButtonConfig` does not reject a newline in `text`, so this is reachable; substituting `<br/>`
    would put a tag inside `<tg-button>`, where its behaviour is unverified.
    """
    button = ButtonConfig(text="Two\nlines", callback_data="join")

    assert button_markup(button) == '<tg-button type="callback_data" data="join">Two\nlines</tg-button>'


def test_a_button_label_is_escaped_as_content_and_its_action_as_an_attribute():
    button = ButtonConfig(text='5 < 6 & "up"', url='https://mitup.social/?q="x"&y=1')

    assert button_markup(button) == (
        '<tg-button type="url" url="https://mitup.social/?q=&quot;x&quot;&amp;y=1">5 &lt; 6 &amp; "up"</tg-button>'
    )


def test_a_disabled_button_becomes_an_inert_tag_carrying_no_action():
    button = ButtonConfig(text="2/5", disabled=True)

    assert button_markup(button) == '<tg-button type="disabled">2/5</tg-button>'


def test_a_disabled_button_label_is_escaped_as_content():
    button = ButtonConfig(text="5 < 6", disabled=True)

    assert button_markup(button) == '<tg-button type="disabled">5 &lt; 6</tg-button>'


def test_a_disabled_button_keeps_its_accent():
    """A status chip is inert but coloured: dropping the style would leave it reading as a control
    somebody had switched off."""
    button = ButtonConfig(text="Public", disabled=True, style="success")

    assert button_markup(button) == '<tg-button type="disabled" style="success">Public</tg-button>'


def test_a_disabled_button_in_a_classic_keyboard_raises():
    """A classic keyboard has no inert state, so a disabled button reaching it is a programming
    error: the keyboards bound for classic surfaces are built to keep every button tappable, and a
    silent drop would hide the control the caller thinks it rendered."""
    keyboard = [
        [
            ButtonConfig(text="≪", callback_data="page:1"),
            ButtonConfig(text="Full", disabled=True),
        ]
    ]

    with pytest.raises(UnsupportedClassicButton, match="'Full' is disabled"):
        classic_markup(keyboard)


def test_a_button_naming_no_action_is_rejected():
    """`ButtonConfig` validates that exactly one action is set; this guards the serializer against
    a row that reached it without one."""
    with pytest.raises(UnsupportedRichButton):
        button_markup(ButtonConfig.model_construct(text="Nothing"))


def test_a_keyboard_renders_one_row_per_keyboard_row_in_order():
    keyboard = [
        [ButtonConfig(text="A", callback_data="a")],
        [ButtonConfig(text="B", callback_data="b"), ButtonConfig(text="C", callback_data="c")],
    ]

    assert keyboard_markup(keyboard) == (
        '<tg-button-row><tg-button type="callback_data" data="a">A</tg-button></tg-button-row>'
        '<tg-button-row><tg-button type="callback_data" data="b">B</tg-button>'
        '<tg-button type="callback_data" data="c">C</tg-button></tg-button-row>'
    )


def test_a_row_carries_no_align_so_its_buttons_split_the_width():
    """Telegram stretches the buttons of an align-less row to fill it, which is how a classic
    inline keyboard row reads."""
    assert "align" not in keyboard_markup([[ButtonConfig(text="A", callback_data="a")]])


def test_an_empty_row_is_dropped_rather_than_sent():
    """A row holds one to eight buttons, so an empty one has no valid form on the wire."""
    assert keyboard_markup([[], [ButtonConfig(text="A", callback_data="a")], []]) == (
        '<tg-button-row><tg-button type="callback_data" data="a">A</tg-button></tg-button-row>'
    )
