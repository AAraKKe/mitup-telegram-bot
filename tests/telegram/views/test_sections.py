from mitup_bot.emojis import Emojis
from mitup_bot.translations import SUPPORTED_LANGUAGES
from mitup_bot.utils.messages import MeetingCardSectionMessages
from mitup_bot.utils.rich_message import RichContent
from mitup_bot.views.sections import card_section, section_header


def test_a_section_puts_its_glyph_and_title_over_the_block_they_introduce(lang: str):
    section = card_section(MeetingCardSectionMessages.WHEN, Emojis.CLOCK, lang, RichContent("the block"))

    title = MeetingCardSectionMessages.WHEN.rich(lang=lang).html
    assert section.html == f"{Emojis.CLOCK} <b>{title}</b><br/>the block"


def test_a_trailer_rides_the_title_line_behind_a_separator(lang: str):
    header = section_header(MeetingCardSectionMessages.PARTICIPANTS, Emojis.JOINED, lang, trailer=RichContent("3 of 8"))

    title = MeetingCardSectionMessages.PARTICIPANTS.rich(lang=lang).html
    assert header.html == f"{Emojis.JOINED} <b>{title}</b> · 3 of 8"


def test_a_header_with_nothing_riding_it_carries_no_separator(lang: str):
    header = section_header(MeetingCardSectionMessages.WHEN, Emojis.CLOCK, lang)

    assert "·" not in header.text


def test_a_section_with_a_trailer_still_opens_its_block_on_the_line_below(lang: str):
    section = card_section(
        MeetingCardSectionMessages.PARTICIPANTS,
        Emojis.JOINED,
        lang,
        RichContent("the block"),
        trailer=RichContent("3 of 8"),
    )

    assert section.html.endswith(" · 3 of 8<br/>the block")


def test_a_section_reads_in_the_language_it_is_given(lang: str):
    other = next(code for code in SUPPORTED_LANGUAGES if code != lang)

    section = card_section(MeetingCardSectionMessages.WHERE, Emojis.MAP, other, RichContent())

    assert MeetingCardSectionMessages.WHERE.rich(lang=other).text in section.text
