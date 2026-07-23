from telegram import MessageEntity

from mitup_bot.utils.entities import FormattedText, utf16_len
from mitup_bot.views.meeting_text import meeting_message, rich_description, rich_title
from tests.helpers import create_meetup, create_user

CUSTOM_EMOJI_ID = "5368324170671202286"


def test_rich_title_restores_entities_from_tagged_column():
    meetup = create_meetup(1, title="Raid 😀")
    meetup.title_tagged = f'<b>Raid</b> <tg-emoji emoji-id="{CUSTOM_EMOJI_ID}">😀</tg-emoji>'

    assert rich_title(meetup) == FormattedText(
        "Raid 😀",
        [
            MessageEntity(type=MessageEntity.BOLD, offset=0, length=4),
            MessageEntity(type=MessageEntity.CUSTOM_EMOJI, offset=5, length=2, custom_emoji_id=CUSTOM_EMOJI_ID),
        ],
    )


def test_rich_title_fallback_keeps_lookalike_text_literal():
    meetup = create_meetup(1, title="<b>hi</b> & co")

    assert rich_title(meetup) == FormattedText("<b>hi</b> & co")


def test_rich_description_is_none_for_unset_or_empty_description():
    assert rich_description(create_meetup(1)) is None
    assert rich_description(create_meetup(2, description="")) is None


def test_rich_description_restores_entities_from_tagged_column():
    meetup = create_meetup(1, description="hidden plans")
    meetup.description_tagged = "<tg-spoiler>hidden</tg-spoiler> plans"

    assert rich_description(meetup) == FormattedText(
        "hidden plans", [MessageEntity(type=MessageEntity.SPOILER, offset=0, length=6)]
    )


def test_meeting_message_carries_title_and_description_entities():
    meetup = create_meetup(1, title="Raid 😀", description="be there")
    create_user(id=1, tg_user_id=123, owned_meetings=[meetup])
    meetup.title_tagged = f'Raid <tg-emoji emoji-id="{CUSTOM_EMOJI_ID}">😀</tg-emoji>'
    meetup.description_tagged = "be <i>there</i>"

    message = meeting_message(meetup)

    assert message.text.startswith("Raid 😀 (")
    title_bold = MessageEntity(type=MessageEntity.BOLD, offset=0, length=utf16_len("Raid 😀"))
    custom_emoji = MessageEntity(type=MessageEntity.CUSTOM_EMOJI, offset=5, length=2, custom_emoji_id=CUSTOM_EMOJI_ID)
    assert title_bold in message.entities
    assert custom_emoji in message.entities

    italic_offset = utf16_len(message.text[: message.text.index("there")])
    assert MessageEntity(type=MessageEntity.ITALIC, offset=italic_offset, length=5) in message.entities
