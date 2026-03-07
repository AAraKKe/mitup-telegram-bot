from collections.abc import Callable
from datetime import UTC, datetime
from unittest import mock

import pytest
from telegram import Chat, MessageEntity, Update
from telegram import Message as TgMessage

from mitup_bot.callback_data import CallbackData
from mitup_bot.exceptions import MeetupNotFound, NoMessageAvailable
from mitup_bot.models import JoinedUsers, Meetup, MeetupLocation, Message, MessageButtons, Settings, User
from mitup_bot.translations import SUPPORTED_LANGUAGES
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils import render
from mitup_bot.utils.emojis import Emojis
from mitup_bot.utils.entities import DateTimeMessageEntity, FormattedText
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages
from mitup_bot.views import ButtonConfig, Keyboard, MitupInlineView, MitupView
from mitup_bot.views.factory import options_button
from tests.helpers import UpdateRequest, create_meetup, create_user
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


def expected_location_name(lang: str, expected_name: str | None, expected_coordinates: str | None) -> FormattedText:
    if expected_name is None and expected_coordinates is None:
        return MeetingMessages.LOCATION_NOT_SET.get(lang=lang)
    return FormattedText(f"{expected_name or ''} {expected_coordinates or ''}".strip())


def expected_participants_message(max_participants: bool, lang: str, n_participants: int) -> str:
    participant_label = (
        MeetingMessages.PARTICIPANT.get(lang=lang).text
        if n_participants == 1
        else MeetingMessages.PARTICIPANTS.get(lang=lang).text
    )
    max_text = (
        MeetingMessages.MAX_PARTICIPANTS.get(lang=lang, max_participants=5).text
        if max_participants
        else f"({MeetingMessages.NO_LIMIT_PARTICIPANTS.get(lang=lang).text})"
    )
    return f"{n_participants} {participant_label} {max_text}"


def expected_message(
    lang: str,
    description: bool,
    datetime: bool,
    username: bool,
    location_name: bool,
    coordinates: bool,
    max_participants: bool,
    incognito: bool,
    invited_user: bool = False,
) -> str:
    str_description = "Test Description" if description else MeetingMessages.DESCRIPTION_NOT_SET.get(lang=lang)
    # When datetime is set, _datetime_display returns EntityDateTime("Meeting time", ...) — text is "Meeting time"
    str_date = "Meeting time" if datetime else MeetingMessages.DATE_NOT_SET.get(lang=lang)
    owner_inline = "john_doe" if username else "John"
    location = expected_location_name(
        lang=lang,
        expected_name="Test Location" if location_name else None,
        expected_coordinates=f"[{Emojis.PIN}]" if coordinates else None,
    )
    str_participants = expected_participants_message(
        max_participants, lang=lang, n_participants=2 if invited_user else 1
    )
    incognito_prefix = f"{Emojis.GLASSES} " if incognito else ""
    str_participants = f"{incognito_prefix}{str_participants}\n  {owner_inline}"
    if invited_user:
        invited_by_text = MeetingMessages.INVITED_BY_USER.get(lang=lang, user=owner_inline).text
        str_participants += f"\n  invited_user ({invited_by_text})"

    return render(
        t"Test Meeting ({MeetingMessages.CREATED_BY.get(lang=lang, owner=owner_inline)})\n\n"
        t"--- {Emojis.DESCRIPTION} {str_description}\n"
        t"--- {Emojis.CLOCK} {str_date}\n"
        t"--- {Emojis.MAP} {location}\n"
        t"--- {Emojis.JOINED} {str_participants}"
    ).text


def expected_inline_message(
    lang: str,
    description: bool,
    datetime: bool,
    username: bool,
    location_name: bool,
    coordinates: bool,
    max_participants: bool,
    incognito: bool,
    invited_user: bool = False,
) -> str:
    owner_inline = "john_doe" if username else "John"
    created_by = MeetingMessages.CREATED_BY.get(lang=lang, owner=owner_inline).text

    str_participants = expected_participants_message(
        max_participants, lang=lang, n_participants=2 if invited_user else 1
    )
    incognito_prefix = f"{Emojis.GLASSES} " if incognito else ""
    participants_list = "" if incognito else f"\n  {owner_inline}"
    if invited_user and not incognito:
        invited_by_text = MeetingMessages.INVITED_BY_USER.get(lang=lang, user=owner_inline).text
        participants_list += f"\n  invited_user ({invited_by_text})"
    str_participants = f"{incognito_prefix}{str_participants}{participants_list}"

    lines = [f"Test Meeting ({created_by})"]
    if description:
        lines.append(f"--- {Emojis.DESCRIPTION} Test Description")
    if datetime:
        # When datetime is set, _datetime_display returns EntityDateTime("Meeting time", ...) — text is "Meeting time"
        lines.append(f"--- {Emojis.CLOCK} Meeting time")
    if location_name or coordinates:
        location_text = expected_location_name(
            lang=lang,
            expected_name="Test Location" if location_name else None,
            expected_coordinates=f"[{Emojis.PIN}]" if coordinates else None,
        ).text
        lines.append(f"--- {Emojis.MAP} {location_text}")
    lines.append(f"--- {Emojis.JOINED} {str_participants}")
    return "\n".join(lines)


@pytest.mark.parametrize("mock_meeting", [EXAMPLE_MEETING, None], ids=["meeting_exist", "meeting_does_not_exist"])
def test_meeting_does_not_exist(mock_session: MockDbSession, mock_meeting: mock.MagicMock):
    mock_session.add_object(mock_meeting)
    meeting = Meetup.by_id(mock_session, 123, must_exist=False)

    expected_query = mock_session.queries_executed[0]

    assert "WHERE meetups.id = 1" in expected_query

    assert meeting == mock_meeting


def test_meeting_does_not_exist_fail_when_must_exist(mock_session: MockDbSession):
    with pytest.raises(MeetupNotFound):
        Meetup.by_id(mock_session, 1, must_exist=True)


@pytest.mark.parametrize(
    "name, expected_name",
    [
        (None, None),
        ("Central Park", "Central Park"),
        ("", None),
        (" ", None),
    ],
    ids=["name_not_set", "name_set", "name_empty", "name_space"],
)
@pytest.mark.parametrize(
    "coordinates, expected_coordinates",
    [
        (None, None),
        (COORDINATES, f"[{Emojis.PIN}]"),
    ],
    ids=["coordinates_not_set", "coordinates_set"],
)
def test_meetup_location_string_conversion(
    name: str | None,
    coordinates: tuple[float, float] | None,
    expected_name: str | None,
    expected_coordinates: str | None,
    lang: str,
):
    location = MeetupLocation(name=name, coordinates=coordinates)

    expected = expected_location_name(lang, expected_name, expected_coordinates)

    assert expected == location.description(lang=lang)


@pytest.mark.parametrize(
    "description, meetup_datetime, username, location_name, location_coordinates, max_participants",
    [
        (False, True, True, True, True, True),
        (True, False, True, True, True, True),
        (True, True, False, True, True, True),
        (True, True, True, False, False, True),
        (True, True, True, True, True, False),
        (True, True, True, False, True, True),
        (True, True, True, True, False, True),
        (True, True, True, True, True, True),
    ],
    ids=[
        "no_description",
        "no_date",
        "no_username",
        "no_location",
        "no_max_members",
        "with_location_coordinates",
        "with_location_name",
        "all_fields",
    ],
)
@pytest.mark.parametrize(
    "is_inline,expected_method",
    [[True, expected_inline_message], [False, expected_message]],
    ids=["inline_message", "normal_message"],
)
@pytest.mark.parametrize("incognito", [True, False], ids=["incognito", "no_incognito"])
@pytest.mark.parametrize("invited_user", [True, False], ids=["with_invited_user", "without_invited_user"])
def test_meetup_message(
    settings: Settings,
    description: bool,
    meetup_datetime: bool,
    username: bool,
    location_name: bool,
    location_coordinates: bool,
    max_participants: bool,
    is_inline: bool,
    expected_method: Callable[[str, bool, bool, bool, bool, bool, bool, bool, bool], str],
    lang: str,
    incognito: bool,
    invited_user: bool,
):
    location = MeetupLocation(
        name="Test Location" if location_name else None,
        coordinates=COORDINATES if location_coordinates else None,
    )
    owner = User(first_name="John", username="john_doe" if username else None, tg_user_id=1, settings=settings)
    meeting = Meetup(
        title="Test Meeting",
        description="Test Description" if description else None,
        datetime=datetime(1987, 7, 16, 23, 59, tzinfo=UTC) if meetup_datetime else None,
        location=location,
        max_members=5 if max_participants else None,
        owner=owner,
        language=lang,
        waiting_list=False,
        public=False,
        allow_invitation=False,
        incognito=incognito,
    )
    # Have at least one user joined to evaluate the list of user joined
    JoinedUsers(user=owner, meetup=meeting)

    if invited_user:
        invited = create_user(
            id=2,
            tg_user_id=2,
            first_name="invited_user",
            username="invited_user",
            settings=settings,
        )
        JoinedUsers(user=invited, meetup=meeting, invited_by=owner)

    expected_text = expected_method(
        lang,
        description,
        meetup_datetime,
        username,
        location_name,
        location_coordinates,
        max_participants,
        incognito,
        invited_user,
    )

    result: FormattedText = meeting.inline_message if is_inline else meeting.message
    assert result.text == expected_text

    # Entity structure: "Test Meeting" is always bold at offset=0, length=12.
    bold_entities = [e for e in result.entities if e.type == MessageEntity.BOLD]
    assert len(bold_entities) == 1
    assert bold_entities[0].offset == 0
    assert bold_entities[0].length == 12  # "Test Meeting"

    # A date_time entity is present if and only if a datetime was set.
    dt_entities = [e for e in result.entities if isinstance(e, DateTimeMessageEntity)]
    if meetup_datetime:
        assert len(dt_entities) == 1
        assert dt_entities[0].unix_time == int(datetime(1987, 7, 16, 23, 59, tzinfo=UTC).timestamp())
    else:
        assert not dt_entities


@pytest.mark.parametrize(
    "participants,max_participants,expected",
    [
        (1, None, lambda lang: f"1 ({MeetingMessages.NO_LIMIT_PARTICIPANTS.get(lang=lang).text})"),
        (0, None, lambda lang: MeetingMessages.EMPTY.get(lang=lang).text),
        (
            0,
            2,
            lambda lang: (
                f"{MeetingMessages.EMPTY.get(lang=lang).text} "
                f"{MeetingMessages.MAX_PARTICIPANTS.get(lang=lang, max_participants=2).text}"
            ),
        ),
        (1, 2, lambda lang: "(1/2)"),
    ],
    ids=["one_participant_no_limit", "empty", "empty_with_limit", "one_participant_with_limit"],
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

    assert f"{expected_incognito}{expected(user_with_settings.lang)}" == render(meeting.participants_badge).text


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


def build_inline_message(lang: str, meeting_datetime: datetime | None) -> str:
    result = [f"{Emojis.JOINED} {MeetingMessages.EMPTY.get(lang=lang).text}"]
    if meeting_datetime:
        # inline_query_message uses _plain_datetime: plain UTC string with no timezone suffix
        result.append(f"{Emojis.CLOCK} 2024-01-12 12:30")
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

    expected = build_inline_message(user_with_settings.lang, meeting_datetime)
    inline_query_text = meeting.inline_query_message.text

    assert expected == inline_query_text
    assert "A description that should not appear in the inline preview" not in inline_query_text
    assert "A location that should not appear" not in inline_query_text


@pytest.mark.parametrize(
    "joined_count,max_participants,expected",
    [
        (
            0,
            None,
            lambda lang: (
                f"{MeetingMessages.EMPTY.get(lang=lang).text} "
                f"({MeetingMessages.NO_LIMIT_PARTICIPANTS.get(lang=lang).text})"
            ),
        ),
        (
            1,
            None,
            lambda lang: (
                f"1 {MeetingMessages.PARTICIPANT.get(lang=lang).text} "
                f"({MeetingMessages.NO_LIMIT_PARTICIPANTS.get(lang=lang).text})|\n  Joined_0"
            ),
        ),
        (
            2,
            2,
            lambda lang: (
                f"2 {MeetingMessages.PARTICIPANTS.get(lang=lang).text} "
                f"{MeetingMessages.MAX_PARTICIPANTS.get(lang=lang, max_participants=2).text}"
                f"|\n  Joined_0\n  Joined_1"
            ),
        ),
        (
            1,
            2,
            lambda lang: (
                f"1 {MeetingMessages.PARTICIPANT.get(lang=lang).text} "
                f"{MeetingMessages.MAX_PARTICIPANTS.get(lang=lang, max_participants=2).text}|\n  Joined_0"
            ),
        ),
    ],
    ids=["empty", "no_limit", "limit_reached", "limit_not_reached"],
)
@pytest.mark.parametrize(
    "incognito, expected_incognito", [(True, f"{Emojis.GLASSES} "), (False, "")], ids=["incognito", "no_incognito"]
)
@pytest.mark.parametrize("with_list", [True, False], ids=["with_list", "without_list"])
def test_participants_text(
    user_with_settings: User,
    joined_count: int,
    max_participants: int,
    expected: Callable[[str], str],
    incognito: bool,
    expected_incognito: str,
    with_list: bool,
):
    meeting = create_meetup(
        id=1,
        owner=user_with_settings,
        title="Test Meeting",
        description="Test Description",
        language=user_with_settings.lang,
        incognito=incognito,
        max_members=max_participants,
    )

    # Add as many joined user as necessary
    # sourcery skip: no-loop-in-tests
    for idx in range(joined_count):
        user = User(first_name=f"Joined_{idx}", tg_user_id=idx, settings=user_with_settings.settings)
        JoinedUsers(user=user, meetup=meeting)

    # We expect the text to cinlude the list or not depending on:
    # - with_list: Always include the list
    # - incognito: Not include it only if we are not requesting to show the list
    expected_text = (
        expected(user_with_settings.lang).replace("|", "")
        if with_list
        else expected(user_with_settings.lang).split("|")[0]
        if incognito
        else expected(user_with_settings.lang).replace("|", "")
    )
    participants_text = meeting.participants_text_with_list if with_list else meeting.participants_text
    assert f"{expected_incognito}{expected_text}" == render(participants_text).text


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
    message = meeting.add_message(update, meeting.owner)

    assert message.inline_message_id == inline_message_id
    assert message.message_id == message_id
    assert message.chat_id == chat_id
    assert message.chat_instance == chat_instance


def test_add_message_does_nothing_if_message_exists():
    meeting = create_meetup(id=1, owner=User(first_name="John", tg_user_id=1, settings=Settings()))
    message = Message(
        id=123, message_id=123, chat_id=123, buttons=MessageButtons(keyboard=meeting.main_view.keyboard), meetup=meeting
    )

    assert message == meeting.add_message(
        Update(123, message=TgMessage(message_id=123, date=datetime.now(), chat=Chat(id=123, type="PRIVATE"))),
        meeting.owner,
    )
    assert len(meeting.messages) == 1


def test_add_message_fails_if_no_message_in_update(meeting: Meetup):
    with pytest.raises(NoMessageAvailable):
        meeting.add_message(Update(123), meeting.owner)


def expected_meeting_settings_view(
    meeting: Meetup,
) -> MitupView:
    lang = meeting.owner.lang
    waiting_list = meeting.waiting_list
    public = meeting.public
    invitation = meeting.allow_invitation
    incognito = meeting.incognito

    message = MeetingMessages.EDIT_SETTINGS_MESSAGE.get(lang=lang)
    waiting_list_button = options_button(
        cb.SET_MEETING_WAITING_LIST.with_id(meeting.db_id),
        ButtonMessages.WAITING_LIST.get(lang=lang),
        waiting_list,
    )
    public_button = options_button(
        cb.SET_MEETING_PUBLIC.with_id(meeting.db_id), ButtonMessages.PUBLIC.get(lang=lang), public
    )
    invitation_button = options_button(
        cb.SET_MEETING_ALLOW_INVITATIONS.with_id(meeting.db_id),
        ButtonMessages.OPEN_INVITATION.get(lang=lang),
        invitation,
    )
    incognito_button = options_button(
        cb.SET_MEETING_INCOGNITO.with_id(meeting.db_id), ButtonMessages.INCOGNITO.get(lang=lang), incognito
    )

    return MitupView(
        message,
        keyboard=[
            [waiting_list_button, public_button],
            [invitation_button, incognito_button],
        ],
    ).with_back_button(text=ButtonMessages.EDIT, callback_data=cb.EDIT_MEETING.with_id(meeting.db_id), lang=lang)


@pytest.mark.parametrize("waiting_list", [True, False], ids=["waiting_list_true", "waiting_list_false"])
@pytest.mark.parametrize("public", [True, False], ids=["public_true", "public_false"])
@pytest.mark.parametrize("invitation", [True, False], ids=["invitation_true", "invitation_false"])
@pytest.mark.parametrize("incognito", [True, False], ids=["incognito_true", "incognito_false"])
def test_default_meeting_options_view(
    waiting_list: bool,
    public: bool,
    invitation: bool,
    incognito: bool,
    user_with_settings: User,
):
    meeting = user_with_settings.meetups[0]
    meeting.allow_invitation = invitation
    meeting.incognito = incognito
    meeting.public = public
    meeting.waiting_list = waiting_list

    view = meeting.settings_view

    expected_view = expected_meeting_settings_view(meeting)

    assert expected_view == view


def expected_inline_keyboard(language: str, *, chat_instance: str | None = None) -> Keyboard:
    expected_keyboard = [
        [
            ButtonConfig(
                text=ButtonMessages.JOIN.get(lang=language),
                callback_data=cb.JOIN.with_id(123),
            ),
            ButtonConfig(
                text=ButtonMessages.LEAVE.get(lang=language),
                callback_data=cb.LEAVE.with_id(123),
            ),
        ]
    ]

    if not chat_instance:
        expected_keyboard.append(
            [
                ButtonConfig(
                    text=ButtonMessages.MAKE_SEARCHABLE.get(lang=language),
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
    view = meeting.inline_view()

    expected_view = MitupInlineView(
        description=meeting.inline_message,
        keyboard=expected_inline_keyboard(language=used_language),
        id="123",
        title=meeting.title,
        inline_description=meeting.inline_query_message,
    ).with_footnote(MeetingMessages.NOT_SEARCHABLE_FOOTNOTE.get(lang=used_language))

    assert expected_view == view


@pytest.mark.parametrize(
    "meeting_language",
    SUPPORTED_LANGUAGES + [None],
    ids=[f"meeting_language_{lang}" for lang in SUPPORTED_LANGUAGES] + ["meeting_language_none"],
)
def test_inline_view_searchable(meeting: Meetup, meeting_language: str | None):
    meeting.language = meeting_language
    used_language = meeting_language or meeting.owner.lang

    view = meeting.inline_view(chat_instance="some_chat_instance")

    expected_view = MitupInlineView(
        description=meeting.inline_message,
        keyboard=expected_inline_keyboard(language=used_language, chat_instance="some_chat_instance"),
        id="123",
        title=meeting.title,
        inline_description=meeting.inline_query_message,
    ).with_footnote(MeetingMessages.SEARCHABLE_FOOTNOTE.get(lang=used_language))

    assert expected_view == view


@pytest.mark.parametrize(
    "max_members,is_full", [[1, True], [2, False], [None, False]], ids=["full_not", "not_full", "not_full_no_limit"]
)
def test_meeting_is_full(max_members: int | None, is_full: bool):
    meeting = create_meetup(id=1, max_members=max_members)

    meeting.max_members = max_members
    JoinedUsers(user=meeting.owner, meetup=meeting)
    assert meeting.full is is_full


@pytest.mark.parametrize("is_waiting_list", [True, False], ids=["waiting_list", "not_waiting_list"])
def test_create_joined_link(is_waiting_list: bool):
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)
    user = create_user(id=2, first_name="Bob")
    joined_link = meeting.create_joined_link(user, is_waiting_list)

    assert joined_link.is_waiting_list == is_waiting_list
    assert joined_link.meetup == meeting
    assert joined_link.user == user
    assert joined_link in meeting.joined_links


def test_n_joined_counts_only_non_waiting_participants():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")
    user3 = create_user(id=4, first_name="Charlie")

    meeting.create_joined_link(user1, is_waiting_list=False)
    meeting.create_joined_link(user2, is_waiting_list=False)
    meeting.create_joined_link(user3, is_waiting_list=True)

    assert meeting.n_participants == 2


def test_n_waiting_counts_only_waiting_participants():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")
    user3 = create_user(id=4, first_name="Charlie")

    meeting.create_joined_link(user1, is_waiting_list=False)
    meeting.create_joined_link(user2, is_waiting_list=True)
    meeting.create_joined_link(user3, is_waiting_list=True)

    assert meeting.n_waiting == 2


def test_participants_returns_only_non_waiting_links():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")
    user3 = create_user(id=4, first_name="Charlie")

    link1 = meeting.create_joined_link(user1, is_waiting_list=False)
    link2 = meeting.create_joined_link(user2, is_waiting_list=False)
    meeting.create_joined_link(user3, is_waiting_list=True)

    participants = meeting.participants

    assert len(participants) == 2
    assert link1 in participants
    assert link2 in participants


def test_participant_returns_specific_participant_by_user_id():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")

    link1 = meeting.create_joined_link(user1, is_waiting_list=False)
    meeting.create_joined_link(user2, is_waiting_list=False)

    participant = meeting.participant(2)

    assert participant is not None
    assert participant == link1
    assert participant.user == user1


@pytest.mark.parametrize(
    "is_public",
    [True, False],
    ids=["public_meeting", "private_meeting"],
)
def test_share_button_in_keyboard(is_public):
    meetup = create_meetup(123, "Meeting", public=is_public)
    keyboard = meetup.build_inline_keyboard()

    share_buttons = [btn for row in keyboard for btn in row if btn.switch_inline_query is not None]
    if is_public:
        assert len(share_buttons) == 1
        assert share_buttons[0].switch_inline_query == "123"
    else:
        assert len(share_buttons) == 0


def test_participant_returns_none_if_user_not_found():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)
    user1 = create_user(id=2, first_name="Bob")

    meeting.create_joined_link(user1, is_waiting_list=False)

    participant = meeting.participant(999)

    assert participant is None


def test_participant_returns_none_for_waiting_list_user():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)
    user1 = create_user(id=2, first_name="Bob")

    meeting.create_joined_link(user1, is_waiting_list=True)

    participant = meeting.participant(2)

    assert participant is None


def test_has_participant_returns_true_for_joined_user():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)
    user1 = create_user(id=2, first_name="Bob")

    meeting.create_joined_link(user1, is_waiting_list=False)

    assert meeting.has_participant(2)


def test_has_participant_returns_true_for_waiting_list_user():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)
    user1 = create_user(id=2, first_name="Bob")

    meeting.create_joined_link(user1, is_waiting_list=True)

    assert meeting.has_participant(2)


def test_has_participant_returns_false_for_non_participant():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)

    assert not meeting.has_participant(999)


def test_waiting_links_returns_sorted_waiting_list():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")
    user3 = create_user(id=4, first_name="Charlie")

    link1 = meeting.create_joined_link(user1, is_waiting_list=True)
    meeting.create_joined_link(user2, is_waiting_list=False)
    link3 = meeting.create_joined_link(user3, is_waiting_list=True)

    waiting = meeting.waiting_links()

    assert len(waiting) == 2
    assert waiting[0] == link1
    assert waiting[1] == link3


@pytest.mark.parametrize(
    "max_members,waiting_list_enabled,expected",
    [
        (None, False, True),
        (None, True, True),
        (2, False, False),
        (2, True, True),
        (3, False, True),
    ],
    ids=["no_limit", "no_limit_with_waiting", "full_no_waiting", "full_with_waiting", "not_full"],
)
def test_join_allowed(max_members: int | None, waiting_list_enabled: bool, expected: bool):
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner, max_members=max_members)
    meeting.waiting_list = waiting_list_enabled
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")

    meeting.create_joined_link(user1, is_waiting_list=False)
    meeting.create_joined_link(user2, is_waiting_list=False)

    assert meeting.join_allowed() == expected


def test_add_participant_adds_to_meeting_when_not_full():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner, max_members=3)
    user1 = create_user(id=2, first_name="Bob")

    joined_link = meeting.add_participant(user1)

    assert joined_link is not None
    assert not joined_link.is_waiting_list
    assert joined_link.user == user1
    assert meeting.n_participants == 1


def test_add_participant_adds_to_waiting_list_when_full():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner, max_members=1)
    meeting.waiting_list = True
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")
    meeting.create_joined_link(user2, is_waiting_list=False)

    joined_link = meeting.add_participant(user1)

    assert joined_link is not None
    assert joined_link.is_waiting_list
    assert joined_link.user == user1
    assert meeting.n_waiting == 1


def test_add_participant_returns_none_when_full_and_no_waiting_list():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner, max_members=1)
    meeting.waiting_list = False
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")
    meeting.create_joined_link(user2, is_waiting_list=False)

    joined_link = meeting.add_participant(user1)

    assert joined_link is None
    assert meeting.n_participants == 1


def test_remove_participant_removes_participant():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)
    user1 = create_user(id=2, first_name="Bob")

    link = meeting.create_joined_link(user1, is_waiting_list=False)
    promoted = meeting.remove_participant(link)

    assert link not in meeting.joined_links
    assert len(promoted) == 0


def test_remove_participant_promotes_from_waiting_list():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner, max_members=2)
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")

    link1 = meeting.create_joined_link(user1, is_waiting_list=False)
    link2 = meeting.create_joined_link(user2, is_waiting_list=True)

    promoted = meeting.remove_participant(link1)

    assert len(promoted) == 1
    assert promoted[0] == link2
    assert not link2.is_waiting_list
    assert meeting.n_participants == 1
    assert meeting.n_waiting == 0


def test_remove_participant_promotes_multiple_from_waiting_list():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner, max_members=3, waiting_list=True)
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")
    user3 = create_user(id=4, first_name="Charlie")
    user4 = create_user(id=5, first_name="Diana")
    user5 = create_user(id=6, first_name="Ethan")

    link1 = meeting.create_joined_link(user1, is_waiting_list=False)
    link2 = meeting.create_joined_link(user2, is_waiting_list=True)
    link3 = meeting.create_joined_link(user3, is_waiting_list=True)
    link4 = meeting.create_joined_link(user4, is_waiting_list=True)
    link5 = meeting.create_joined_link(user5, is_waiting_list=True)

    promoted = meeting.remove_participant(link1)

    assert len(promoted) == 3
    assert promoted[0] == link2
    assert promoted[1] == link3
    assert promoted[2] == link4
    assert not link2.is_waiting_list
    assert not link3.is_waiting_list
    assert not link4.is_waiting_list
    assert link5.is_waiting_list
    assert meeting.n_participants == 3
    assert meeting.n_waiting == 1


def test_promote_from_waiting_list_promotes_all_when_no_limit():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")

    link1 = meeting.create_joined_link(user1, is_waiting_list=True)
    link2 = meeting.create_joined_link(user2, is_waiting_list=True)

    promoted = meeting.promote_from_waiting_list()

    assert len(promoted) == 2
    assert promoted[0] == link1
    assert promoted[1] == link2
    assert not link1.is_waiting_list
    assert not link2.is_waiting_list
    assert meeting.n_waiting == 0


def test_promote_from_waiting_list_promotes_up_to_max():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner, max_members=2)
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")
    user3 = create_user(id=4, first_name="Charlie")

    link1 = meeting.create_joined_link(user1, is_waiting_list=True)
    link2 = meeting.create_joined_link(user2, is_waiting_list=True)
    link3 = meeting.create_joined_link(user3, is_waiting_list=True)

    promoted = meeting.promote_from_waiting_list()

    assert len(promoted) == 2
    assert promoted[0] == link1
    assert promoted[1] == link2
    assert not link1.is_waiting_list
    assert not link2.is_waiting_list
    assert link3.is_waiting_list
    assert meeting.n_waiting == 1
    assert meeting.n_participants == 2


def test_promote_from_waiting_list_returns_empty_when_no_waiting():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner)

    promoted = meeting.promote_from_waiting_list()

    assert len(promoted) == 0


def test_promote_from_waiting_list_respects_order():
    owner = create_user(id=1, first_name="John")
    meeting = create_meetup(id=1, owner=owner, max_members=2)
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")
    user3 = create_user(id=4, first_name="Charlie")

    link1 = meeting.create_joined_link(user1, is_waiting_list=True)
    link2 = meeting.create_joined_link(user2, is_waiting_list=True)
    link3 = meeting.create_joined_link(user3, is_waiting_list=True)

    promoted = meeting.promote_from_waiting_list()

    # Should promote in order they joined
    assert promoted[0] == link1
    assert promoted[1] == link2
    assert link3.is_waiting_list


def test_participants_list_text_includes_waiting_list_section():
    owner = create_user(id=1, first_name="Owner")
    meeting = create_meetup(id=1, owner=owner, max_members=3)
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")
    user3 = create_user(id=4, first_name="Charlie")
    user4 = create_user(id=5, first_name="Dave")

    # Add regular participants
    meeting.create_joined_link(user1, is_waiting_list=False)
    meeting.create_joined_link(user2, is_waiting_list=False)
    meeting.create_joined_link(user3, is_waiting_list=False)

    # Add waiting list participants
    meeting.create_joined_link(user4, is_waiting_list=True)

    expected = (
        f"\n  Bob"
        f"\n  Alice"
        f"\n  Charlie"
        f"\n--- {Emojis.WAITING} {ButtonMessages.WAITING_LIST.get(lang=meeting.lang).text} \n  "
        f"Dave"
    )

    assert expected == meeting.participants_list_text.text


def test_participants_list_text_without_waiting_list():
    owner = create_user(id=1, first_name="Owner")
    meeting = create_meetup(id=1, owner=owner)
    user1 = create_user(id=2, first_name="Bob")
    user2 = create_user(id=3, first_name="Alice")

    # Add only regular participants
    meeting.create_joined_link(user1, is_waiting_list=False)
    meeting.create_joined_link(user2, is_waiting_list=False)

    expected = "\n  Bob\n  Alice"

    assert expected == meeting.participants_list_text.text


# --- Invite button visibility in main_view and external_view ---


def invite_buttons(view: MitupView, meeting: Meetup) -> list[ButtonConfig]:
    invite_cb = cb.INVITE.with_id(meeting.db_id)
    return [btn for row in view.keyboard for btn in row if btn.callback_data == invite_cb]


@pytest.mark.parametrize("allow_invitation", [True, False], ids=["allow_invitation", "no_invitation"])
def test_main_view_invite_button_visibility(allow_invitation: bool, user_with_settings: User):
    meeting = create_meetup(id=10, owner=user_with_settings, invitation=allow_invitation)

    view = meeting.main_view
    found_invite_buttons = invite_buttons(view, meeting)

    if allow_invitation:
        assert len(found_invite_buttons) == 1  # INVITE button must appear exactly once
    else:
        assert len(found_invite_buttons) == 0  # INVITE button must be absent


@pytest.mark.parametrize("allow_invitation", [True, False], ids=["allow_invitation", "no_invitation"])
def test_external_view_invite_button_visibility(allow_invitation: bool, user_with_settings: User):
    meeting = create_meetup(id=11, owner=user_with_settings, invitation=allow_invitation)

    view = meeting.external_view
    found_invite_buttons = invite_buttons(view, meeting)

    if allow_invitation:
        assert len(found_invite_buttons) == 1  # INVITE button must appear exactly once
    else:
        assert len(found_invite_buttons) == 0  # INVITE button must be absent


def test_participants_list_text_preserves_entities_from_participant_name():
    owner = create_user(id=1, first_name="Owner")
    meeting = create_meetup(id=1, owner=owner)
    invited = create_user(id=2, first_name="Bob")
    inviter = create_user(id=3, first_name="Alice")
    meeting.create_joined_link(invited, is_waiting_list=False, invited_by=inviter)

    result = meeting.participants_list_text
    # The text must include the participant name and the invited-by annotation.
    assert "Bob" in result.text
    assert "Alice" in result.text
    # Entities from the invited-by FormattedText must survive into the list.
    assert result.entities, "expected formatting entities from the invited-by text to be present"


def test_external_view():
    owner = create_user(id=1, first_name="Owner")
    meeting = create_meetup(id=1, owner=owner, invitation=True)

    expected_view = MitupView(
        meeting.inline_message,
        [
            [
                ButtonConfig(
                    text=ButtonMessages.JOIN.get(lang=meeting.user_language),
                    callback_data=cb.JOIN.with_id(meeting.db_id),
                ),
                ButtonConfig(
                    text=ButtonMessages.INVITE.get(lang=meeting.user_language),
                    callback_data=cb.INVITE.with_id(meeting.db_id),
                ),
                ButtonConfig(
                    text=ButtonMessages.LEAVE.get(lang=meeting.user_language),
                    callback_data=cb.LEAVE.with_id(meeting.db_id),
                ),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.MAIN_MENU.back(lang=meeting.user_language),
                    callback_data=cb.MAIN_MENU,
                ),
            ],
        ],
    )

    assert expected_view == meeting.external_view


def test_edit_view(user_with_settings: User):
    meeting = user_with_settings.meetups[0]
    now_in_tz = meeting.owner.datetime_in_tz(meeting.datetime) if meeting.datetime else meeting.owner.now_in_tz()

    expected_view = MitupView(
        meeting.message,
        [
            [
                ButtonConfig(
                    text=ButtonMessages.TITLE.get(lang=meeting.user_language),
                    callback_data=cb.EDIT_MEETING_TITLE.with_id(meeting.db_id),
                ),
                ButtonConfig(
                    text=ButtonMessages.DESCRIPTION.get(lang=meeting.user_language),
                    callback_data=cb.EDIT_MEETING_DESCRIPTION.with_id(meeting.db_id),
                ),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.DATE.get(lang=meeting.user_language),
                    callback_data=cb.EDIT_MEETING_DATE.with_id(meeting.db_id).with_date(now_in_tz.date()),
                ),
                ButtonConfig(
                    text=ButtonMessages.TIME.get(lang=meeting.user_language),
                    callback_data=cb.EDIT_MEETING_TIME.with_id(meeting.db_id),
                ),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.PARTICIPANTS.get(lang=meeting.user_language),
                    callback_data=cb.EDIT_MEETING_PARTICIPANTS.with_id(meeting.db_id),
                ),
                ButtonConfig(
                    text=ButtonMessages.LOCATION.get(lang=meeting.user_language),
                    callback_data=cb.EDIT_MEETING_LOCATION.with_id(meeting.db_id),
                ),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.LANGUAGE.get(lang=meeting.user_language),
                    callback_data=cb.EDIT_MEETING_LANGUAGE.with_id(meeting.db_id),
                ),
                ButtonConfig(
                    text=ButtonMessages.SETTINGS.get(lang=meeting.user_language),
                    callback_data=cb.EDIT_MEETING_SETTINGS.with_id(meeting.db_id),
                ),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.DONE.get(lang=meeting.user_language),
                    callback_data=cb.SHOW_MEETING.with_id(meeting.db_id),
                ),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.MAIN_MENU.back(lang=meeting.user_language),
                    callback_data=cb.MAIN_MENU,
                ),
            ],
        ],
    )

    assert expected_view == meeting.edit_view
