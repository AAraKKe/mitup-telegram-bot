import pytest

from mitup_bot.emojis import Emojis
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils.messages import MeetingCardSectionMessages, MeetingEditParticipantsMessages
from mitup_bot.views.meeting.sections import participants_count_line
from tests.telegram.views.meeting.helpers import owned_meeting


def test_the_count_line_reads_as_bare_numbers_against_the_cap(lang: str):
    meeting = owned_meeting(lang=lang, guests=3)
    meeting.max_members = 8

    assert participants_count_line(meeting).text == (
        MeetingCardSectionMessages.COUNT_OF_MAX.text(lang=lang, count=3, max=8)
    )


def test_the_count_line_of_an_uncapped_meeting_names_no_ceiling(lang: str):
    meeting = owned_meeting(lang=lang, guests=2)
    meeting.owner.supporter_level = SupporterLevel.HOST_2
    meeting.max_members = None

    no_limit = MeetingEditParticipantsMessages.NO_LIMIT_LABEL.text(lang=lang)
    assert participants_count_line(meeting).text == f"2 ({no_limit})"


def test_the_count_line_covers_the_waiting_list(lang: str):
    """The cap governs the confirmed list, so a full meeting still counts above its own cap rather
    than reading as stuck at it."""
    meeting = owned_meeting(lang=lang, guests=2, waiting=3)
    meeting.max_members = 2

    assert participants_count_line(meeting).text == (
        MeetingCardSectionMessages.COUNT_OF_MAX.text(lang=lang, count=5, max=2)
    )


@pytest.mark.parametrize("incognito", [True, False], ids=["incognito", "listed"])
def test_the_count_line_marks_an_incognito_meeting(incognito: bool, lang: str):
    meeting = owned_meeting(lang=lang, incognito=incognito, guests=1)

    assert participants_count_line(meeting).text.startswith(str(Emojis.GLASSES)) is incognito


def test_the_count_line_of_an_empty_meeting_keeps_the_wording_that_carries_no_noun(lang: str):
    meeting = owned_meeting(lang=lang)

    assert str(meeting.effective_max_members) in participants_count_line(meeting).text
