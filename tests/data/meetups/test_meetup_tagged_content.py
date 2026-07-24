from telegram import MessageEntity

from mitup_bot.utils.entities import parse_format_tags, serialize_entities
from tests.helpers import create_meetup

CUSTOM_EMOJI_ID = "5368324170671202286"


def test_set_title_stores_tagged_form_in_title_column():
    meetup = create_meetup(1)
    text = "Raid night 😀"
    entities = [
        MessageEntity(type=MessageEntity.BOLD, offset=0, length=4),
        MessageEntity(type=MessageEntity.CUSTOM_EMOJI, offset=11, length=2, custom_emoji_id=CUSTOM_EMOJI_ID),
    ]

    meetup.set_title(serialize_entities(text, entities))

    assert meetup.title == f'<b>Raid</b> night <tg-emoji emoji-id="{CUSTOM_EMOJI_ID}">😀</tg-emoji>'
    assert meetup.plain_title == text


def test_set_description_stores_tagged_form_in_description_column():
    meetup = create_meetup(1)
    text = "Bring snacks & drinks"
    entities = [MessageEntity(type=MessageEntity.ITALIC, offset=6, length=6)]

    meetup.set_description(serialize_entities(text, entities))

    assert meetup.description == "Bring <i>snacks</i> &amp; drinks"
    assert meetup.plain_description == text


def test_tagged_title_reads_the_title_column_directly():
    meetup = create_meetup(1, title="<b>Raid night</b>")

    assert meetup.tagged_title == "<b>Raid night</b>"


def test_tagged_description_is_none_when_description_unset():
    meetup = create_meetup(1)

    assert meetup.tagged_description is None
    assert meetup.plain_description is None


def test_plain_title_keeps_escaped_lookalike_text_literal():
    meetup = create_meetup(1, title="&lt;b&gt;hi&lt;/b&gt; &amp; &quot;friends&quot;")

    assert meetup.plain_title == '<b>hi</b> & "friends"'


def test_title_round_trip_preserves_entities_and_lookalike_text():
    text = "<b>hi</b> 😀"
    entities = [MessageEntity(type=MessageEntity.CUSTOM_EMOJI, offset=10, length=2, custom_emoji_id=CUSTOM_EMOJI_ID)]
    meetup = create_meetup(1)

    meetup.set_title(serialize_entities(text, entities))
    parsed = parse_format_tags(meetup.tagged_title, {})

    assert parsed.text == text
    assert parsed.entities == entities
    assert meetup.plain_title == text


def test_short_description_truncates_on_plain_text_with_entities_present():
    text = "A long description that overflows the preview limit"
    entities = [MessageEntity(type=MessageEntity.BOLD, offset=0, length=len(text))]
    meetup = create_meetup(1)

    meetup.set_description(serialize_entities(text, entities))

    assert meetup.short_description == "A long description that ..."


def test_short_description_measures_the_visible_text_not_the_tags():
    # 26 visible characters — under the 30-character preview limit even though the tagged
    # column is far longer, so no truncation may happen.
    text = "short but heavily styled x"
    entities = [
        MessageEntity(type=MessageEntity.BOLD, offset=0, length=5),
        MessageEntity(type=MessageEntity.ITALIC, offset=10, length=7),
        MessageEntity(type=MessageEntity.STRIKETHROUGH, offset=18, length=6),
    ]
    meetup = create_meetup(1)

    meetup.set_description(serialize_entities(text, entities))

    assert meetup.description is not None and len(meetup.description) > 30
    assert meetup.short_description == text
