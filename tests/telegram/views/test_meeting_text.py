import datetime as dt

from mitup_bot.emojis import Emojis
from mitup_bot.models import Meetup
from mitup_bot.utils.messages import MeetingDisplayMessages
from mitup_bot.views.meeting_text import (
    description_content,
    inline_query_message,
    participant_name,
    plain_datetime,
    title_content,
)
from tests.helpers import create_joined_link, create_meetup, create_settings, create_user

CUSTOM_EMOJI_ID = "5368324170671202286"


# --- Stored title and description as card content ---


def test_title_content_restores_markup_from_the_title_column():
    meetup = create_meetup(1, title=f'<b>Raid</b> <tg-emoji emoji-id="{CUSTOM_EMOJI_ID}">😀</tg-emoji>')

    assert title_content(meetup).html == f'<b>Raid</b> <tg-emoji emoji-id="{CUSTOM_EMOJI_ID}">😀</tg-emoji>'


def test_title_content_keeps_escaped_lookalike_text_literal():
    """A title somebody typed cannot become markup: the stored escapes decode to characters, and
    those characters are escaped again on the way into the card."""
    meetup = create_meetup(1, title="&lt;b&gt;hi&lt;/b&gt; &amp; co")

    assert title_content(meetup).text == "<b>hi</b> & co"


def test_description_content_is_none_for_unset_or_empty_description():
    assert description_content(create_meetup(1)) is None
    assert description_content(create_meetup(2, description="")) is None


def test_description_content_restores_markup_from_the_description_column():
    meetup = create_meetup(1, description="<tg-spoiler>hidden</tg-spoiler> plans")

    description = description_content(meetup)

    assert description is not None
    assert description.html == "<tg-spoiler>hidden</tg-spoiler> plans"


# --- Participant names ---


def test_participant_name_trails_the_invitation_that_brought_them():
    owner = create_user(id=1, first_name="Owner")
    meeting = create_meetup(1, owner=owner)
    invited = create_user(id=2, first_name="Bob")
    inviter = create_user(id=3, first_name="Alice")
    link = create_joined_link(invited, meeting, id=1, invited_by=inviter)

    name = participant_name(link)

    assert "Bob" in name.text
    assert "Alice" in name.text
    assert "<i>" in name.html, "expected the italic markup of the invited-by fragment"


def test_participant_name_is_the_bare_name_when_nobody_invited_them():
    owner = create_user(id=1, first_name="Owner")
    meeting = create_meetup(1, owner=owner)
    joined = create_user(id=2, first_name="Bob")

    name = participant_name(create_joined_link(joined, meeting, id=1))

    assert name.text == "Bob"
    assert name.is_plain


# --- Previews that cannot carry entities ---

# 22:45 in Madrid, the evening before in UTC, as a meeting datetime column holds it.
STARTS_AT = dt.datetime(2027, 3, 17, 21, 45, tzinfo=dt.UTC)
# Pinned to the year the meeting falls in, which is what keeps the year out of the expectation
# below: a preview spells the year out only when the meeting outlives the year it was created in.
CREATED_AT = dt.datetime(2027, 1, 5, 9, 0, tzinfo=dt.UTC)
STARTS_AT_IN_SPANISH = "mié, 17 mar, 22:45"


def meeting_in_madrid() -> Meetup:
    """A meeting starting at STARTS_AT, owned by someone whose timezone is Madrid's."""
    meeting = create_meetup(1, datetime=STARTS_AT, language="es_ES", created_time=CREATED_AT)
    create_user(
        id=1,
        tg_user_id=123,
        owned_meetings=[meeting],
        settings=create_settings(timezone="Europe/Madrid", language="es_ES"),
    )
    return meeting


def test_plain_datetime_writes_the_start_out_for_a_preview_that_cannot_carry_entities():
    assert plain_datetime(meeting_in_madrid()) == STARTS_AT_IN_SPANISH


def test_plain_datetime_falls_back_to_the_placeholder_when_no_time_is_set():
    meeting = meeting_in_madrid()
    meeting.datetime = None

    assert plain_datetime(meeting) == MeetingDisplayMessages.DATE_NOT_SET.text(lang=meeting.lang)


def test_inline_query_message_previews_the_start_as_readable_text():
    # An inline result description is a bare string: nothing here may need formatting to read.
    assert STARTS_AT_IN_SPANISH in inline_query_message(meeting_in_madrid())


def test_inline_query_message_of_a_dateless_meeting_previews_the_count_alone():
    meeting = create_meetup(1)
    create_user(id=1, tg_user_id=123, owned_meetings=[meeting])

    message = inline_query_message(meeting)

    assert message.startswith(str(Emojis.JOINED))
    assert str(Emojis.CLOCK) not in message
