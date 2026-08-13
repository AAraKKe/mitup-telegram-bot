from telegram import MessageEntity, User

from mitup_bot.utils.entities import FormattedText, ellipsize, truncate, utf16_len

# A grinning face is outside the BMP: one character, two UTF-16 code units.
EMOJI = "😀"


def test_truncate_leaves_text_within_the_limit_untouched():
    text = FormattedText("hello", [MessageEntity(type=MessageEntity.BOLD, offset=0, length=5)])

    assert truncate(text, 5) == text
    assert truncate(text, 500) == text


def test_truncate_cuts_at_the_limit():
    assert truncate(FormattedText("abcdefghij"), 4) == FormattedText("abcd")


def test_truncate_to_nothing_drops_every_entity():
    text = FormattedText("hello", [MessageEntity(type=MessageEntity.BOLD, offset=0, length=5)])

    assert truncate(text, 0) == FormattedText("")


def test_truncate_keeps_an_entity_ending_exactly_at_the_cut():
    bold = MessageEntity(type=MessageEntity.BOLD, offset=0, length=4)
    text = FormattedText("abcdefgh", [bold])

    assert truncate(text, 4) == FormattedText("abcd", [bold])


def test_truncate_drops_an_entity_starting_past_the_cut():
    text = FormattedText("abcdefgh", [MessageEntity(type=MessageEntity.BOLD, offset=4, length=4)])

    assert truncate(text, 4) == FormattedText("abcd")


def test_truncate_clamps_an_entity_spanning_the_cut():
    text = FormattedText("abcdefgh", [MessageEntity(type=MessageEntity.ITALIC, offset=2, length=6)])

    assert truncate(text, 4) == FormattedText("abcd", [MessageEntity(type=MessageEntity.ITALIC, offset=2, length=2)])


def test_truncate_keeps_the_attributes_of_a_clamped_entity():
    link = MessageEntity(type=MessageEntity.TEXT_LINK, offset=0, length=8, url="https://example.com")

    clamped = truncate(FormattedText("abcdefgh", [link]), 4).entities[0]

    assert (clamped.type, clamped.offset, clamped.length, clamped.url) == (MessageEntity.TEXT_LINK, 0, 4, link.url)


def test_truncate_keeps_the_user_and_language_of_a_clamped_entity():
    """Clamping copies the entity, and the copy must carry EVERY field the original had.

    `user` (a text_mention of someone without a username) and `language` (a pre block) are the
    fields a hand-written copy is likeliest to forget, and losing them silently downgrades the
    rendering rather than failing.
    """
    mention = MessageEntity(
        type=MessageEntity.TEXT_MENTION, offset=0, length=8, user=User(id=7, first_name="Ana", is_bot=False)
    )
    pre = MessageEntity(type=MessageEntity.PRE, offset=0, length=8, language="python")

    clamped_mention = truncate(FormattedText("abcdefgh", [mention]), 4).entities[0]
    clamped_pre = truncate(FormattedText("abcdefgh", [pre]), 4).entities[0]

    assert clamped_mention.user is not None
    assert clamped_mention.user.id == 7
    assert clamped_pre.language == "python"


def test_truncate_never_cuts_inside_a_surrogate_pair():
    # The cut lands between the two code units of the emoji, so it moves back and drops it whole.
    text = FormattedText(f"ab{EMOJI}cd")

    assert truncate(text, 3) == FormattedText("ab")


def test_truncate_keeps_a_whole_astral_character_that_fits():
    assert truncate(FormattedText(f"ab{EMOJI}cd"), 4) == FormattedText(f"ab{EMOJI}")


def test_truncate_drops_the_entity_of_a_character_lost_to_the_surrogate_step_back():
    custom_emoji = MessageEntity(type=MessageEntity.CUSTOM_EMOJI, offset=2, length=2, custom_emoji_id="1")
    text = FormattedText(f"ab{EMOJI}cd", [custom_emoji])

    assert truncate(text, 3) == FormattedText("ab")


def test_ellipsize_leaves_text_within_the_limit_untouched():
    text = FormattedText("hello")

    assert ellipsize(text, 5) == text


def test_ellipsize_marks_the_cut_and_stays_within_the_limit():
    ellipsized = ellipsize(FormattedText("abcdefghij"), 4)

    assert ellipsized.text == "abc…"
    assert utf16_len(ellipsized.text) == 4


def test_ellipsize_keeps_the_entities_of_what_survives():
    text = FormattedText("abcdefghij", [MessageEntity(type=MessageEntity.BOLD, offset=0, length=10)])

    assert ellipsize(text, 4) == FormattedText("abc…", [MessageEntity(type=MessageEntity.BOLD, offset=0, length=3)])


def test_ellipsize_with_no_room_for_content_keeps_only_the_ellipsis():
    assert ellipsize(FormattedText("abcdefghij"), 1) == FormattedText("…")


def test_ellipsize_with_no_room_at_all_returns_nothing():
    assert ellipsize(FormattedText("abcdefghij"), 0) == FormattedText("")
