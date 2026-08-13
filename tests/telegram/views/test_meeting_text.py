import datetime as dt
from collections.abc import Callable

import pytest
from telegram import MessageEntity

from mitup_bot import limits
from mitup_bot.emojis import Emojis
from mitup_bot.models import Meetup, MeetupLocation
from mitup_bot.utils.entities import ELLIPSIS, MAX_MESSAGE_UTF16_LENGTH, FormattedText, utf16_len
from mitup_bot.utils.messages import MeetingDisplayMessages
from mitup_bot.views.meeting_text import (
    MEETING_CARD_BUDGET,
    STATE_BANNER_RESERVE,
    collapsed_participants,
    inline_card,
    inline_card_sections,
    inline_message,
    meeting_card,
    meeting_card_sections,
    meeting_message,
    rich_description,
    rich_title,
)
from tests.helpers import create_joined_link, create_meetup, create_user

CUSTOM_EMOJI_ID = "5368324170671202286"

# The shape that took a meeting off the air in production: a location name holding a base64 blob.
LOCATION_BLOB = "data:image/png;base64," + "QUJD" * 750

# One distinct character per field, so a fitted card's remaining length in each is a character count.
# The blob shares characters with all three and is kept out of the tests that count them.
TITLE_FILLER = "T"
DESCRIPTION_FILLER = "D"
LOCATION_FILLER = "L"

# A place name stored far past what intake accepts, as the blob above was.
OVERLONG_LOCATION = LOCATION_FILLER * 3000


def test_rich_title_restores_entities_from_title_column():
    meetup = create_meetup(1, title=f'<b>Raid</b> <tg-emoji emoji-id="{CUSTOM_EMOJI_ID}">😀</tg-emoji>')

    assert rich_title(meetup) == FormattedText(
        "Raid 😀",
        [
            MessageEntity(type=MessageEntity.BOLD, offset=0, length=4),
            MessageEntity(type=MessageEntity.CUSTOM_EMOJI, offset=5, length=2, custom_emoji_id=CUSTOM_EMOJI_ID),
        ],
    )


def test_rich_title_keeps_escaped_lookalike_text_literal():
    meetup = create_meetup(1, title="&lt;b&gt;hi&lt;/b&gt; &amp; co")

    assert rich_title(meetup) == FormattedText("<b>hi</b> & co")


def test_rich_description_is_none_for_unset_or_empty_description():
    assert rich_description(create_meetup(1)) is None
    assert rich_description(create_meetup(2, description="")) is None


def test_rich_description_restores_entities_from_description_column():
    meetup = create_meetup(1, description="<tg-spoiler>hidden</tg-spoiler> plans")

    assert rich_description(meetup) == FormattedText(
        "hidden plans", [MessageEntity(type=MessageEntity.SPOILER, offset=0, length=6)]
    )


def test_meeting_message_carries_title_and_description_entities():
    meetup = create_meetup(
        1,
        title=f'Raid <tg-emoji emoji-id="{CUSTOM_EMOJI_ID}">😀</tg-emoji>',
        description="be <i>there</i>",
    )
    create_user(id=1, tg_user_id=123, owned_meetings=[meetup])

    message = meeting_message(meetup)

    assert message.text.startswith("Raid 😀 (")
    title_bold = MessageEntity(type=MessageEntity.BOLD, offset=0, length=utf16_len("Raid 😀"))
    custom_emoji = MessageEntity(type=MessageEntity.CUSTOM_EMOJI, offset=5, length=2, custom_emoji_id=CUSTOM_EMOJI_ID)
    assert title_bold in message.entities
    assert custom_emoji in message.entities

    italic_offset = utf16_len(message.text[: message.text.index("there")])
    assert MessageEntity(type=MessageEntity.ITALIC, offset=italic_offset, length=5) in message.entities


def meeting_with_participants(
    *,
    title: str = "Board game night",
    description: str | None = "Bring snacks",
    location_name: str | None = None,
    participants: int = 0,
    waiting: int = 0,
    incognito: bool = False,
    starts_at: dt.datetime | None = None,
    ends_at: dt.datetime | None = None,
) -> Meetup:
    location = MeetupLocation(name=location_name) if location_name else MeetupLocation()
    meeting = create_meetup(
        1,
        title=title,
        description=description,
        location=location,
        max_members=10,
        incognito=incognito,
        datetime=starts_at,
    )
    meeting.end_datetime = ends_at
    owner = create_user(id=1, tg_user_id=123, first_name="Owner", owned_meetings=[meeting])
    assert meeting.owner is owner
    # sourcery skip: no-loop-in-tests
    for index in range(participants + waiting):
        member = create_user(id=index + 2, tg_user_id=index + 2, first_name=f"Member {index}")
        create_joined_link(member, meeting, id=index, is_waiting_list=index >= participants)
    return meeting


def meeting_at_every_input_cap(*, participants: int = 2) -> Meetup:
    """A meeting whose every free-text field is as long as intake accepts, dated start and end.

    The worst card a user can actually produce, as opposed to the pathological stored values the
    other fitting tests use to force the later steps.
    """
    return meeting_with_participants(
        title=TITLE_FILLER * limits.TITLE_MAX_CHARS,
        description=DESCRIPTION_FILLER * limits.DESCRIPTION_MAX_CHARS,
        location_name=LOCATION_FILLER * limits.LOCATION_NAME_MAX_CHARS,
        participants=participants,
        starts_at=dt.datetime(2026, 9, 1, 18, 0, tzinfo=dt.UTC),
        ends_at=dt.datetime(2026, 9, 1, 21, 0, tzinfo=dt.UTC),
    )


def assert_entities_stay_inside(message: FormattedText):
    length = utf16_len(message.text)
    # sourcery skip: no-loop-in-tests
    for entity in message.entities:
        assert entity.offset >= 0
        assert entity.offset + entity.length <= length, f"{entity} reaches past {length} code units"


def listed_names(message: FormattedText, total: int) -> list[str]:
    """The participant names the card lists, each matched as a whole line.

    Names occupy an indented line of their own, so matching lines rather than substrings keeps
    "Member 1" from being found inside "Member 10".
    """
    lines = {line.strip() for line in message.text.splitlines()}
    return [f"Member {index}" for index in range(total) if f"Member {index}" in lines]


def filler_after_cut(target: int) -> int:
    """Filler units a section cut to *target* keeps — the ellipsis takes the last unit for itself."""
    return target - utf16_len(ELLIPSIS)


def natural_overflow(meeting: Meetup) -> int:
    """How far past the budget the meeting's card runs before anything is given up."""
    return utf16_len(meeting_card(meeting, meeting_card_sections(meeting)).text) - MEETING_CARD_BUDGET


def test_meeting_message_of_an_ordinary_meeting_is_rendered_untrimmed():
    meeting = meeting_with_participants(participants=1)

    assert meeting_message(meeting) == meeting_card(meeting, meeting_card_sections(meeting))


def test_inline_message_of_an_ordinary_meeting_is_rendered_untrimmed():
    meeting = meeting_with_participants(participants=1)

    assert inline_message(meeting) == inline_card(meeting, inline_card_sections(meeting))


def test_meeting_message_of_an_ordinary_meeting_keeps_its_exact_layout():
    meeting = meeting_with_participants(participants=1)
    created_by = MeetingDisplayMessages.CREATED_BY.get(lang="en", owner="Owner").text
    date_not_set = MeetingDisplayMessages.DATE_NOT_SET.get(lang="en").text
    location_not_set = MeetingDisplayMessages.LOCATION_NOT_SET.get(lang="en").text
    participant_label = MeetingDisplayMessages.PARTICIPANT_LABEL.get(lang="en").text
    max_label = MeetingDisplayMessages.MAX_PARTICIPANTS_LABEL.get(lang="en", max_participants=10).text

    assert meeting_message(meeting).text == (
        f"Board game night ({created_by})\n\n"
        f"--- {Emojis.DESCRIPTION} Bring snacks\n"
        f"--- {Emojis.CLOCK} {date_not_set}\n"
        f"--- {Emojis.MAP} {location_not_set}\n"
        f"--- {Emojis.JOINED} 1 {participant_label} {max_label}\n  Member 0"
    )


@pytest.mark.parametrize("render_card", [meeting_message, inline_message], ids=["owner_card", "inline_card"])
def test_maximal_meeting_fits_telegram_message_limit(render_card: Callable[[Meetup], FormattedText]):
    meeting = meeting_with_participants(
        title="Board game night 🎲" * 10,
        description="<b>Bring snacks</b> 😀 " * 200,
        location_name=LOCATION_BLOB,
        participants=100,
        waiting=20,
    )

    message = render_card(meeting)

    assert utf16_len(message.text) <= MAX_MESSAGE_UTF16_LENGTH
    assert_entities_stay_inside(message)


@pytest.mark.parametrize("render_card", [meeting_message, inline_message], ids=["owner_card", "inline_card"])
def test_card_at_every_input_cap_keeps_all_its_names(render_card: Callable[[Meetup], FormattedText]):
    """The worst card intake allows still lists everyone: prose pays for the overrun, names do not."""
    meeting = meeting_at_every_input_cap(participants=2)

    message = render_card(meeting)

    assert utf16_len(message.text) <= MEETING_CARD_BUDGET
    # Listing both names is what rules out the "names not shown" tail: the card only carries that
    # line when it is holding some back.
    assert listed_names(message, 2) == ["Member 0", "Member 1"]
    assert message.text.count(DESCRIPTION_FILLER) >= limits.DESCRIPTION_GUARANTEED_CHARS
    assert message.text.count(LOCATION_FILLER) == limits.LOCATION_NAME_MAX_CHARS


def test_over_budget_card_takes_from_the_description_only_what_the_overflow_needs():
    """Being over budget costs the description that much and no more — not a drop to its floor."""
    meeting = meeting_at_every_input_cap()
    overflow = natural_overflow(meeting)
    assert overflow > 0

    message = meeting_message(meeting)

    assert message.text.count(DESCRIPTION_FILLER) == filler_after_cut(limits.DESCRIPTION_MAX_CHARS - overflow)
    assert message.text.count(DESCRIPTION_FILLER) > limits.DESCRIPTION_GUARANTEED_CHARS


def test_over_budget_card_shortens_the_place_name_once_the_description_sits_at_its_floor():
    """A stored location far past its intake cap outlasts the description, then gives way itself."""
    meeting = meeting_with_participants(
        description=DESCRIPTION_FILLER * limits.DESCRIPTION_MAX_CHARS,
        location_name=OVERLONG_LOCATION,
        participants=60,
    )

    message = meeting_message(meeting)

    assert utf16_len(message.text) <= MEETING_CARD_BUDGET
    assert message.text.count(DESCRIPTION_FILLER) == filler_after_cut(limits.DESCRIPTION_GUARANTEED_CHARS)
    assert message.text.count(LOCATION_FILLER) >= filler_after_cut(limits.LOCATION_NAME_GUARANTEED_CHARS)
    assert message.text.count(LOCATION_FILLER) < len(OVERLONG_LOCATION)
    assert len(listed_names(message, 60)) == 60


def test_card_over_budget_with_both_floors_reached_collapses_the_participants_list():
    """Only stored values no intake path produces leave the names as the last thing left to give."""
    meeting = meeting_with_participants(
        description=DESCRIPTION_FILLER * limits.DESCRIPTION_MAX_CHARS,
        location_name=OVERLONG_LOCATION,
        participants=300,
    )

    message = meeting_message(meeting)
    hidden = 300 - len(listed_names(message, 300))

    assert utf16_len(message.text) <= MEETING_CARD_BUDGET
    assert message.text.count(DESCRIPTION_FILLER) == filler_after_cut(limits.DESCRIPTION_GUARANTEED_CHARS)
    assert message.text.count(LOCATION_FILLER) == filler_after_cut(limits.LOCATION_NAME_GUARANTEED_CHARS)
    assert hidden > 0
    assert MeetingDisplayMessages.PARTICIPANTS_TRUNCATED.get(lang="en", count=hidden).text in message.text


@pytest.mark.parametrize("participants", [60, 300, 400], ids=["location_cut", "names_cut", "names_cut_harder"])
def test_fitted_card_never_shows_less_than_the_guaranteed_lengths(participants: int):
    """Whatever a card gives up later in the order, the fields given up first keep their floors."""
    meeting = meeting_with_participants(
        description=DESCRIPTION_FILLER * limits.DESCRIPTION_MAX_CHARS,
        location_name=OVERLONG_LOCATION,
        participants=participants,
    )

    message = meeting_message(meeting)

    assert utf16_len(message.text) <= MEETING_CARD_BUDGET
    assert message.text.count(DESCRIPTION_FILLER) >= filler_after_cut(limits.DESCRIPTION_GUARANTEED_CHARS)
    assert message.text.count(LOCATION_FILLER) >= filler_after_cut(limits.LOCATION_NAME_GUARANTEED_CHARS)


def test_oversized_card_keeps_the_count_of_everyone_left_out():
    meeting = meeting_with_participants(
        description=DESCRIPTION_FILLER * limits.DESCRIPTION_MAX_CHARS,
        location_name=OVERLONG_LOCATION,
        participants=200,
        waiting=100,
    )

    message = meeting_message(meeting)
    hidden = 300 - len(listed_names(message, 300))

    assert hidden > 0
    assert MeetingDisplayMessages.PARTICIPANTS_TRUNCATED.get(lang="en", count=hidden).text in message.text


def test_collapsing_an_empty_participants_section_puts_no_names_into_it():
    """A card that lists nobody by design gets nothing back when the fit runs out of room.

    Negative room is what `fitted_sections` passes once both prose floors are reached and the card
    is still over, and an incognito card reaches that point with an empty section.
    """
    meeting = meeting_with_participants(participants=60, incognito=True)

    assert collapsed_participants(meeting, FormattedText(""), -100) == FormattedText("")


def test_incognito_inline_card_names_nobody_when_it_has_to_be_cut():
    """Fitting an incognito card must not put back the names the card withholds by design.

    The title is long enough that the card is still over budget with both floors reached, which is
    the only point at which the fit reaches for the participants section at all.
    """
    meeting = meeting_with_participants(
        title=TITLE_FILLER * 3300,
        description=DESCRIPTION_FILLER * limits.DESCRIPTION_MAX_CHARS,
        location_name=OVERLONG_LOCATION,
        participants=60,
        incognito=True,
    )

    assert utf16_len(inline_card(meeting, inline_card_sections(meeting)).text) > MEETING_CARD_BUDGET

    message = inline_message(meeting)

    assert utf16_len(message.text) <= MEETING_CARD_BUDGET
    assert "Member" not in message.text
    # An incognito card has no participants list at all, so not even the line counting the names it
    # is holding back belongs on it.
    assert MeetingDisplayMessages.PARTICIPANTS_TRUNCATED.get(lang=meeting.lang, count=60).text not in message.text


def test_card_whose_chrome_alone_overflows_is_cut_to_the_limit():
    """Title, owner and date/time are never given up, so an oversized title leaves nothing else to
    trade and the card is cut outright rather than sent and rejected."""
    meeting = meeting_with_participants(title=f"<b>{'Board game night 🎲' * 300}</b>", participants=5)

    message = meeting_message(meeting)

    assert utf16_len(message.text) <= MAX_MESSAGE_UTF16_LENGTH
    assert_entities_stay_inside(message)


def test_state_banner_reserve_covers_the_banners(lang: str):
    """The view layer prepends a state banner to a rendered card, so the budget's reserve has to
    hold the longest banner any language produces plus the blank line under it."""
    banners = (
        MeetingDisplayMessages.IN_PROGRESS_BANNER.get(lang=lang),
        MeetingDisplayMessages.DELETED_BANNER.get(lang=lang),
        MeetingDisplayMessages.FINISHED_BANNER.get(lang=lang),
        MeetingDisplayMessages.FINISHED_SUMMARY_BANNER.get(
            lang=lang, start_datetime="2026-01-01 00:00", end_datetime="2026-01-01 23:59", attendee_count=9999
        ),
    )

    # sourcery skip: no-loop-in-tests
    for banner in banners:
        assert utf16_len(banner.text) + utf16_len("\n\n") <= STATE_BANNER_RESERVE
