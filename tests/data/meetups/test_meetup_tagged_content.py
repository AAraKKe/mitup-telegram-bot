import html

from telegram import MessageEntity

from mitup_bot.utils.entities import parse_format_tags, serialize_entities
from tests.helpers import create_meetup

CUSTOM_EMOJI_ID = "5368324170671202286"


def test_set_title_writes_plain_and_tagged_columns():
    meetup = create_meetup(1)
    text = "Raid night 😀"
    entities = [
        MessageEntity(type=MessageEntity.BOLD, offset=0, length=4),
        MessageEntity(type=MessageEntity.CUSTOM_EMOJI, offset=11, length=2, custom_emoji_id=CUSTOM_EMOJI_ID),
    ]

    meetup.set_title(text, serialize_entities(text, entities))

    assert meetup.title == text
    assert meetup.title_tagged == f'<b>Raid</b> night <tg-emoji emoji-id="{CUSTOM_EMOJI_ID}">😀</tg-emoji>'


def test_set_description_writes_plain_and_tagged_columns():
    meetup = create_meetup(1)
    text = "Bring snacks & drinks"
    entities = [MessageEntity(type=MessageEntity.ITALIC, offset=6, length=6)]

    meetup.set_description(text, serialize_entities(text, entities))

    assert meetup.description == text
    assert meetup.description_tagged == "Bring <i>snacks</i> &amp; drinks"


def test_tagged_title_prefers_stored_tagged_value():
    meetup = create_meetup(1, title="Raid night")
    meetup.title_tagged = "<b>Raid night</b>"

    assert meetup.tagged_title == "<b>Raid night</b>"


def test_tagged_title_falls_back_to_escaped_plain_title():
    title = '<b>hi</b> & "friends"'
    meetup = create_meetup(1, title=title)

    assert meetup.title_tagged is None
    assert meetup.tagged_title == html.escape(title)

    parsed = parse_format_tags(meetup.tagged_title, {})
    assert parsed.text == title
    assert parsed.entities == []


def test_tagged_description_is_none_when_description_unset():
    assert create_meetup(1).tagged_description is None


def test_tagged_description_falls_back_to_escaped_plain_description():
    description = "<3 & <b>bold</b>"
    meetup = create_meetup(1, description=description)

    tagged = meetup.tagged_description
    assert tagged is not None
    assert tagged == html.escape(description)
    assert parse_format_tags(tagged, {}).text == description


def test_title_round_trip_preserves_entities_and_lookalike_text():
    text = "<b>hi</b> 😀"
    entities = [MessageEntity(type=MessageEntity.CUSTOM_EMOJI, offset=10, length=2, custom_emoji_id=CUSTOM_EMOJI_ID)]
    meetup = create_meetup(1)

    meetup.set_title(text, serialize_entities(text, entities))
    parsed = parse_format_tags(meetup.tagged_title, {})

    assert parsed.text == text
    assert parsed.entities == entities


def test_short_description_truncates_on_plain_text_with_entities_present():
    text = "A long description that overflows the preview limit"
    entities = [MessageEntity(type=MessageEntity.BOLD, offset=0, length=len(text))]
    meetup = create_meetup(1)

    meetup.set_description(text, serialize_entities(text, entities))

    assert meetup.short_description == "A long description that ..."
