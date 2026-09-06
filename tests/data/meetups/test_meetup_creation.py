from collections.abc import Callable
from datetime import UTC, datetime
from unittest import mock
from zoneinfo import ZoneInfo

import pytest
from telegram import Chat, Update
from telegram import Message as TgMessage

from mitup_bot import supporter
from mitup_bot.callback_data import CallbackData
from mitup_bot.config import LimitsConfig
from mitup_bot.datetimes import TimeFormat
from mitup_bot.emojis import Emojis
from mitup_bot.exceptions import MeetupNotFound, NoMessageAvailable
from mitup_bot.keyboards import ButtonConfig, Keyboard
from mitup_bot.models import JoinedUsers, Meetup, MeetupLocation, Message, MessageButtons, Settings, User
from mitup_bot.supporter import SupporterLevel
from mitup_bot.translations import SUPPORTED_LANGUAGES
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import (
    ButtonMessages,
    MeetingAttachMessages,
    MeetingDisplayMessages,
    MeetingEditParticipantsMessages,
    MeetingEditSettingsMessages,
    SettingsMessages,
)
from mitup_bot.utils.rich_message import button_markup, horizontal_rule_content
from mitup_bot.views import MitupInlineView
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.datetime_format import localized_datetime
from mitup_bot.views.factory import toggle_chip
from mitup_bot.views.meeting import shared_card
from mitup_bot.views.meeting.sections import participants_count_line
from mitup_bot.views.meeting_text import inline_query_message, participants_badge
from tests.helpers import UpdateRequest, create_joined_link, create_meetup, create_user
from tests.helpers.stub_db import MockDbSession  # sourcery skip: dont-import-test-modules

EXAMPLE_MEETING = Meetup(
    id=123,
    owner_id=1,
    title="Test Meeting",
    description="Test Description",
    datetime=datetime(2001, 1, 1, 12, 12),
    waiting_list=False,
    public=False,
    allow_invitation=False,
    incognito=False,
)
COORDINATES = (123.1, -321.1)
# The free-tier participant cap these render tests exercise. Pinned via the autouse fixture below so
# a free owner's "no explicit limit" resolves to it (instead of rendering as unlimited) and the
# expectations stay deterministic regardless of any config another test might leave behind.
FREE_CAP = 20
# The start every card in this module is dated with, and the timezone the `settings` fixture gives
# the owner. A card writes the moment out in the owner's timezone, so the expectations need both.
MEETING_START = datetime(1987, 7, 16, 23, 59, tzinfo=UTC)
OWNER_TIMEZONE = ZoneInfo("Europe/Madrid")


def expected_datetime_text(value: datetime, lang: str, time_format: TimeFormat) -> str:
    """How a card writes *value* out: the owner's local wall clock, in the meeting's language.

    The meetings in this module are built without a creation timestamp, so a card never spells the
    year out for them.
    """
    return localized_datetime(value, lang=lang, tz=OWNER_TIMEZONE, created=None, time_format=time_format)


@pytest.fixture(autouse=True)
def pin_free_participant_cap(monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(supporter.PolicyState, "config", LimitsConfig(free_participant_capacity=FREE_CAP))
    return FREE_CAP


@pytest.mark.parametrize("mock_meeting", [EXAMPLE_MEETING, None], ids=["meeting_exist", "meeting_does_not_exist"])
async def test_meeting_does_not_exist(mock_session: MockDbSession, mock_meeting: mock.MagicMock):
    mock_session.add_object(mock_meeting)
    meeting = await Meetup.by_id(mock_session, 123, must_exist=False)

    expected_query = mock_session.queries_executed[0]

    assert "WHERE meetups.id = 1" in expected_query

    assert meeting == mock_meeting


async def test_meeting_does_not_exist_fail_when_must_exist(mock_session: MockDbSession):
    with pytest.raises(MeetupNotFound):
        await Meetup.by_id(mock_session, 1, must_exist=True)


def test_the_card_badges_a_patron_owner():
    owner = create_user(id=1, username="alice", tg_user_id=997_720, supporter_level=SupporterLevel.HOST_2)
    meeting = create_meetup(id=1, owner=owner, language="en")
    create_joined_link(owner, meeting, id=0)

    badged_owner = f"{Emojis.HOST_2} alice"
    text = meeting_views.shared_body(meeting).text

    assert text.count(badged_owner) == 2, "expected the badge in the byline and again in the attendee list"


def test_the_card_carries_no_badge_for_a_free_owner():
    owner = create_user(id=1, username="alice", tg_user_id=997_721)
    meeting = create_meetup(id=1, owner=owner, language="en")
    create_joined_link(owner, meeting, id=0)

    assert str(Emojis.HOST_2) not in meeting_views.shared_body(meeting).text


def test_incognito_meeting_omits_supporter_participant_badge():
    """Incognito hides the participant list, so a supporter's name, and its badge, never render."""
    owner = create_user(id=1, first_name="Owner", tg_user_id=997_722)
    meeting = create_meetup(id=1, owner=owner, incognito=True, language="en")
    patron_member = create_user(id=2, username="alice", tg_user_id=997_723, supporter_level=SupporterLevel.HOST_2)
    create_joined_link(patron_member, meeting, id=0)

    text = meeting_views.shared_body(meeting).text
    assert "alice" not in text
    assert str(Emojis.HOST_2) not in text


@pytest.mark.parametrize(
    "participants,max_participants,expected",
    [
        # A free owner's "no explicit limit" resolves to the cap, so the badge reads against it.
        (1, None, lambda lang: f"(1/{FREE_CAP})"),
        (
            0,
            None,
            lambda lang: (
                f"{MeetingDisplayMessages.PARTICIPANT_COUNT_EMPTY.text(lang=lang)} "
                f"{MeetingDisplayMessages.MAX_PARTICIPANTS_LABEL.text(lang=lang, max_participants=FREE_CAP)}"
            ),
        ),
        (
            0,
            2,
            lambda lang: (
                f"{MeetingDisplayMessages.PARTICIPANT_COUNT_EMPTY.text(lang=lang)} "
                f"{MeetingDisplayMessages.MAX_PARTICIPANTS_LABEL.text(lang=lang, max_participants=2)}"
            ),
        ),
        (1, 2, lambda lang: "(1/2)"),
    ],
    ids=["one_participant_free_cap", "empty_free_cap", "empty_with_limit", "one_participant_with_limit"],
)
@pytest.mark.parametrize(
    "incognito, expected_incognito", [(True, f"{Emojis.GLASSES} "), (False, "")], ids=["incognito", "no_incognito"]
)
def test_participants_badge(
    participants: int,
    max_participants: int,
    expected: Callable[[str], str],
    user_with_settings: User,
    incognito: bool,
    expected_incognito: str,
):
    meeting = create_meetup(
        id=1,
        owner=user_with_settings,
        title="Test Meeting",
        description="Test Description",
        max_members=max_participants,
        incognito=incognito,
        language=user_with_settings.lang,
    )

    # sourcery skip: no-loop-in-tests
    for idx in range(participants):
        user = User(first_name=f"Joined_{idx}", tg_user_id=idx, settings=user_with_settings.settings)
        JoinedUsers(user=user, meetup=meeting)

    assert f"{expected_incognito}{expected(user_with_settings.lang)}" == participants_badge(meeting)


@pytest.mark.parametrize(
    "description,expected_description",
    [
        (None, None),
        ("A short description", "A short description"),
        (
            "A long description to be cut off at some point in time, but not too soon",
            "A long description to be cut ...",
        ),
        ("A long description to be cut  off", "A long description to be cut ..."),
    ],
    ids=["no_description", "short_description", "long_description", "end_in_space"],
)
def test_short_description(description: str | None, expected_description: str | None):
    meeting = Meetup(
        title="Test Meeting",
        description=description,
        owner=User(first_name="John", username="john_doe", tg_user_id=1),
        waiting_list=False,
        public=False,
        allow_invitation=False,
        incognito=False,
    )

    assert expected_description == meeting.short_description


def build_inline_message(lang: str, meeting_datetime: datetime | None, time_format: TimeFormat) -> str:
    # Free owner + no explicit limit: the empty badge carries the effective cap label.
    empty = MeetingDisplayMessages.PARTICIPANT_COUNT_EMPTY.text(lang=lang)
    max_label = MeetingDisplayMessages.MAX_PARTICIPANTS_LABEL.text(lang=lang, max_participants=FREE_CAP)
    result = [f"{Emojis.JOINED} {empty} {max_label}"]
    if meeting_datetime:
        result.append(f"{Emojis.CLOCK} {expected_datetime_text(meeting_datetime, lang, time_format)}")
    return "\n".join(result)


@pytest.mark.parametrize(
    "meeting_datetime",
    [datetime(2024, 1, 12, 12, 30, tzinfo=UTC), None],
    ids=["with_datetime", "without_datetime"],
)
def test_inline_query_message(user_with_settings: User, meeting_datetime: datetime | None):
    meeting = Meetup(
        title="Test Meeting",
        description="A description that should not appear in the inline preview",
        datetime=meeting_datetime,
        location=MeetupLocation(name="A location that should not appear"),
        owner=user_with_settings,
        waiting_list=False,
        public=False,
        allow_invitation=False,
        incognito=False,
    )

    expected = build_inline_message(user_with_settings.lang, meeting_datetime, meeting.time_format)
    inline_query_text = inline_query_message(meeting)

    assert expected == inline_query_text
    assert "A description that should not appear in the inline preview" not in inline_query_text
    assert "A location that should not appear" not in inline_query_text


@pytest.mark.parametrize(
    "joined_count,expected",
    [
        # Uncapped owner keeps "no explicit limit" as unlimited, so the badge never shows the cap.
        (0, lambda lang: MeetingDisplayMessages.PARTICIPANT_COUNT_EMPTY.text(lang=lang)),
        (1, lambda lang: f"1 ({MeetingEditParticipantsMessages.NO_LIMIT_LABEL.text(lang=lang)})"),
    ],
    ids=["empty", "one_participant"],
)
def test_participants_badge_patron_owner_stays_no_limit(
    user_with_settings: User, joined_count: int, expected: Callable[[str], str]
):
    user_with_settings.supporter_level = SupporterLevel.HOST_2
    meeting = create_meetup(id=1, owner=user_with_settings, max_members=None, language=user_with_settings.lang)

    # sourcery skip: no-loop-in-tests
    for idx in range(joined_count):
        joined = User(first_name=f"Joined_{idx}", tg_user_id=idx, settings=user_with_settings.settings)
        JoinedUsers(user=joined, meetup=meeting)

    assert participants_badge(meeting) == expected(user_with_settings.lang)


def test_the_count_line_of_a_patron_owner_stays_no_limit(user_with_settings: User):
    """A Patron owner's meeting with no explicit limit reads as unlimited, never as the free cap."""
    user_with_settings.supporter_level = SupporterLevel.HOST_2
    meeting = create_meetup(id=1, owner=user_with_settings, max_members=None, language=user_with_settings.lang)
    joined = create_user(id=2, first_name="Joined_0", tg_user_id=0, settings=user_with_settings.settings)
    create_joined_link(joined, meeting, id=0)

    no_limit = MeetingEditParticipantsMessages.NO_LIMIT_LABEL.text(lang=user_with_settings.lang)
    assert participants_count_line(meeting).text == f"1 ({no_limit})"


@pytest.mark.parametrize(
    "update",
    [
        UpdateRequest(message=True, callback_query=False),
        UpdateRequest(message=False, callback_query=True),
        UpdateRequest(message=False, callback_query=True, inline_message_id="123"),
    ],
    ids=["message", "callback_query", "inline_query"],
    indirect=True,
)
def test_getting_message_from_update(update: Update, meeting: Meetup):
    message = Message(id=123, message_id=123, chat_id=123, inline_message_id="123", meetup=meeting)
    assert message == meeting.message_from_update(update)


def test_getting_message_from_update_returns_none_if_not_found(update: Update, meeting: Meetup):
    assert meeting.message_from_update(update) is None


@pytest.mark.parametrize("update", [UpdateRequest(message=True, callback_query=False)], indirect=True)
def test_getting_message_from_update_does_not_match_across_chats(update: Update, meeting: Meetup):
    """Message ids are only unique per chat, so the same id in another chat is a different message."""
    assert update.effective_message is not None
    Message(
        id=123,
        message_id=update.effective_message.message_id,
        chat_id=update.effective_message.chat_id + 1,
        meetup=meeting,
    )

    assert meeting.message_from_update(update) is None


def test_getting_message_from_update_returns_none_message_is_not_in_update(meeting: Meetup):
    assert meeting.message_from_update(Update(123)) is None


@pytest.mark.parametrize(
    "update",
    [
        UpdateRequest(message=True, callback_query=False),
        UpdateRequest(message=False, callback_query=True),
        UpdateRequest(message=False, callback_query=CallbackData(entity="test"), inline_message_id="123"),
    ],
    ids=["message", "callback_query", "inline_query"],
    indirect=True,
)
@pytest.mark.parametrize("has_message", [True, False], ids=["has_message", "does_not_have_message"])
def test_has_message(update: Update, meeting: Meetup, has_message: bool):
    if has_message:
        Message(id=123, message_id=123, chat_id=123, inline_message_id="123", meetup=meeting)
    assert meeting.has_message(update) is has_message


@pytest.mark.parametrize(
    "update,message_id,inline_message_id,chat_id,chat_instance",
    [
        (UpdateRequest(message=True, callback_query=False), 123, None, 123, None),
        (UpdateRequest(message=False, callback_query=True), 123, None, 123, None),
        (
            UpdateRequest(message=False, callback_query=CallbackData(entity="test"), from_bot_chat=False),
            None,
            "some_inline_message_id",
            None,
            "someinstance",
        ),
    ],
    ids=["message", "callback_query_within_bot_chat", "callback_query_outside_bot_chat"],
    indirect=["update"],
)
def test_add_message_to_meeting_from_update(
    meeting: Meetup, update: Update, message_id: int, inline_message_id: str, chat_id: int, chat_instance: str
):
    message = meeting.add_message(update, meeting_views.keyboard_for_update(update, meeting, meeting.owner))

    assert message.inline_message_id == inline_message_id
    assert message.message_id == message_id
    assert message.chat_id == chat_id
    assert message.chat_instance == chat_instance


def test_add_message_does_nothing_if_message_exists():
    meeting = create_meetup(id=1, owner=User(first_name="John", tg_user_id=1, settings=Settings()))
    message = Message(
        id=123,
        message_id=123,
        chat_id=123,
        buttons=MessageButtons(keyboard=meeting_views.owner_view(meeting).menu),
        meetup=meeting,
    )

    update = Update(123, message=TgMessage(message_id=123, date=datetime.now(), chat=Chat(id=123, type="PRIVATE")))
    assert message == meeting.add_message(update, meeting_views.keyboard_for_update(update, meeting, meeting.owner))
    assert len(meeting.messages) == 1


def test_add_message_fails_if_no_message_in_update(meeting: Meetup):
    with pytest.raises(NoMessageAvailable):
        meeting.add_message(Update(123), meeting_views.keyboard_for_update(Update(123), meeting, meeting.owner))


def test_meeting_behavior_view_sections(user_with_settings: User):
    """Every setting is its own section: bold name, the state chip that flips it, and the
    explanation below. Mixed values prove each chip reads its own setting."""
    meeting = user_with_settings.meetups[0]
    meeting.waiting_list = True
    meeting.public = False
    meeting.allow_invitation = True
    meeting.incognito = False
    meeting.lock_on_start = True

    view = meeting_views.behavior_view(meeting)
    html = view.message.html
    lang = meeting.owner.lang

    # The tap-to-toggle hint sits under the heading: the state chips read as pills to anyone
    # new to inline buttons, so the screen says they are tappable.
    assert SettingsMessages.TOGGLE_HINT.text(lang=lang) in html

    sections = [
        (
            ButtonMessages.WAITING_LIST,
            MeetingEditSettingsMessages.WAITING_LIST_EXPLANATION,
            cb.SET_MEETING_WAITING_LIST,
            True,
        ),
        (ButtonMessages.PUBLIC, MeetingEditSettingsMessages.PUBLIC_EXPLANATION, cb.SET_MEETING_PUBLIC, False),
        (
            ButtonMessages.OPEN_INVITATION,
            MeetingEditSettingsMessages.OPEN_INVITATIONS_EXPLANATION,
            cb.SET_MEETING_ALLOW_INVITATIONS,
            True,
        ),
        (ButtonMessages.INCOGNITO, MeetingEditSettingsMessages.INCOGNITO_EXPLANATION, cb.SET_MEETING_INCOGNITO, False),
        (
            ButtonMessages.LOCK_ON_START,
            MeetingEditSettingsMessages.LOCK_ON_START_EXPLANATION,
            cb.SET_MEETING_LOCK_ON_START,
            True,
        ),
    ]
    for name, explanation, callback, value in sections:
        assert f"<b>{name.text(lang=lang)}</b>" in html
        assert explanation.text(lang=lang) in html
        assert button_markup(toggle_chip(callback.with_id(meeting.db_id), value, lang)) in html

    # The menu carries only the back row: the toggles live in the body.
    assert view.menu == [
        [
            ButtonConfig(
                text=ButtonMessages.SETTINGS.back(lang=lang),
                callback_data=cb.EDIT_MEETING_SETTINGS.with_id(meeting.db_id),
            )
        ]
    ]


def expected_inline_keyboard(language: str, *, chat_instance: str | None = None) -> Keyboard:
    expected_keyboard = [
        [
            ButtonConfig(
                text=ButtonMessages.JOIN.text(lang=language),
                callback_data=cb.JOIN.with_id(123),
                style="success",
            ),
            ButtonConfig(
                text=ButtonMessages.LEAVE.text(lang=language),
                callback_data=cb.LEAVE.with_id(123),
                style="danger",
            ),
        ]
    ]

    if not chat_instance:
        expected_keyboard.append(
            [
                ButtonConfig(
                    text=ButtonMessages.MAKE_SEARCHABLE.text(lang=language),
                    callback_data=cb.ATTACH_TO_CHAT.with_id(123),
                ),
            ],
        )

    return expected_keyboard


@pytest.mark.parametrize(
    "meeting_language",
    SUPPORTED_LANGUAGES + [None],
    ids=[f"meeting_language_{lang}" for lang in SUPPORTED_LANGUAGES] + ["meeting_language_none"],
)
def test_inline_view(meeting: Meetup, meeting_language: str | None):
    # Ensure the language of the inline view is the language of the meeting
    # except when the meeting has no language
    meeting.language = meeting_language
    used_language = meeting_language or meeting.owner.lang
    view = meeting_views.inline_view(meeting)

    closing = shared_card.closing_footer(
        used_language,
        shared_card.SHARED_CHAT_SOURCE,
        MeetingAttachMessages.STATE_NOT_SEARCHABLE.rich(lang=used_language),
    )
    expected_view = MitupInlineView(
        message=meeting_views.shared_body(meeting).append(horizontal_rule_content()).append(closing),
        menu=expected_inline_keyboard(language=used_language),
        id="123",
        title=meeting.title,
        inline_description=inline_query_message(meeting),
    )

    assert expected_view == view


@pytest.mark.parametrize(
    "meeting_language",
    SUPPORTED_LANGUAGES + [None],
    ids=[f"meeting_language_{lang}" for lang in SUPPORTED_LANGUAGES] + ["meeting_language_none"],
)
def test_inline_view_searchable(meeting: Meetup, meeting_language: str | None):
    meeting.language = meeting_language
    used_language = meeting_language or meeting.owner.lang

    view = meeting_views.inline_view(meeting, chat_instance="some_chat_instance")

    closing = shared_card.closing_footer(
        used_language, shared_card.SHARED_CHAT_SOURCE, MeetingAttachMessages.STATE_SEARCHABLE.rich(lang=used_language)
    )
    expected_view = MitupInlineView(
        message=meeting_views.shared_body(meeting).append(horizontal_rule_content()).append(closing),
        menu=expected_inline_keyboard(language=used_language, chat_instance="some_chat_instance"),
        id="123",
        title=meeting.title,
        inline_description=inline_query_message(meeting),
    )

    assert expected_view == view
