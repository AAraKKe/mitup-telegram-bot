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
from mitup_bot.utils.entities import FormattedText
from mitup_bot.utils.messages import (
    ButtonMessages,
    MeetingAttachMessages,
    MeetingDisplayMessages,
    MeetingEditParticipantsMessages,
    MeetingEditSettingsMessages,
)
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
        return MeetingDisplayMessages.LOCATION_NOT_SET.get(lang=lang)
    return FormattedText(f"{expected_name or ''} {expected_coordinates or ''}".strip())


def expected_participants_message(max_participants: bool, lang: str, n_participants: int) -> str:
    participant_label = (
        MeetingDisplayMessages.PARTICIPANT_LABEL.get(lang=lang).text
        if n_participants == 1
        else MeetingDisplayMessages.PARTICIPANTS_LABEL.get(lang=lang).text
    )
    max_text = (
        MeetingDisplayMessages.MAX_PARTICIPANTS_LABEL.get(lang=lang, max_participants=5).text
        if max_participants
        else f"({MeetingEditParticipantsMessages.NO_LIMIT_LABEL.get(lang=lang).text})"
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
    str_description = "Test Description" if description else MeetingDisplayMessages.DESCRIPTION_NOT_SET.get(lang=lang)
    # When datetime is set, _datetime_section returns EntityDateTime("Meeting time", ...) — text is "Meeting time"
    str_date = "Meeting time" if datetime else MeetingDisplayMessages.DATE_NOT_SET.get(lang=lang)
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
        invited_by_text = MeetingDisplayMessages.INVITED_BY.get(lang=lang, user=owner_inline).text
        str_participants += f"\n  invited_user ({invited_by_text})"

    return render(
        t"Test Meeting ({MeetingDisplayMessages.CREATED_BY.get(lang=lang, owner=owner_inline)})\n\n"
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
    created_by = MeetingDisplayMessages.CREATED_BY.get(lang=lang, owner=owner_inline).text

    str_participants = expected_participants_message(
        max_participants, lang=lang, n_participants=2 if invited_user else 1
    )
    incognito_prefix = f"{Emojis.GLASSES} " if incognito else ""
    participants_list = "" if incognito else f"\n  {owner_inline}"
    if invited_user and not incognito:
        invited_by_text = MeetingDisplayMessages.INVITED_BY.get(lang=lang, user=owner_inline).text
        participants_list += f"\n  invited_user ({invited_by_text})"
    str_participants = f"{incognito_prefix}{str_participants}{participants_list}"

    has_location = location_name or coordinates
    lines = [f"Test Meeting ({created_by})"]
    if description:
        lines.append(f"--- {Emojis.DESCRIPTION} Test Description")
    if datetime:
        # _datetime_section ends with "\n". In the datetime branch of inline_message:
        #   t"\n{datetime_section}" produces "\n--- CLOCK time\n"
        # If location is present, location_section starts with "\n", producing a blank
        # line between the clock line and the location line.
        # If location is absent, location_section is "" and participants follow directly
        # with no blank line (since participants row has no leading "\n" in this branch).
        lines.append(f"--- {Emojis.CLOCK} Meeting time")
        if has_location:
            lines.append("")  # blank line: location_section begins with "\n"
    if has_location:
        location_text = expected_location_name(
            lang=lang,
            expected_name="Test Location" if location_name else None,
            expected_coordinates=f"[{Emojis.PIN}]" if coordinates else None,
        ).text
        lines.append(f"--- {Emojis.MAP} {location_text}")
        if not datetime:
            # In the no-datetime branch, location_section ends with "\n" and the
            # participants row starts with "\n", producing a blank line between them.
            lines.append("")
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
    dt_entities = [e for e in result.entities if e.type == MessageEntity.DATE_TIME]
    if meetup_datetime:
        assert len(dt_entities) == 1
        # PTB stores unix_time as datetime on the entity; compare against the expected datetime directly.
        assert dt_entities[0].unix_time == datetime(1987, 7, 16, 23, 59, tzinfo=UTC)
    else:
        assert not dt_entities


@pytest.mark.parametrize(
    "participants,max_participants,expected",
    [
        (1, None, lambda lang: f"1 ({MeetingEditParticipantsMessages.NO_LIMIT_LABEL.get(lang=lang).text})"),
        (0, None, lambda lang: MeetingDisplayMessages.PARTICIPANT_COUNT_EMPTY.get(lang=lang).text),
        (
            0,
            2,
            lambda lang: (
                f"{MeetingDisplayMessages.PARTICIPANT_COUNT_EMPTY.get(lang=lang).text} "
                f"{MeetingDisplayMessages.MAX_PARTICIPANTS_LABEL.get(lang=lang, max_participants=2).text}"
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
    result = [f"{Emojis.JOINED} {MeetingDisplayMessages.PARTICIPANT_COUNT_EMPTY.get(lang=lang).text}"]
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
                f"{MeetingDisplayMessages.PARTICIPANT_COUNT_EMPTY.get(lang=lang).text} "
                f"({MeetingEditParticipantsMessages.NO_LIMIT_LABEL.get(lang=lang).text})"
            ),
        ),
        (
            1,
            None,
            lambda lang: (
                f"1 {MeetingDisplayMessages.PARTICIPANT_LABEL.get(lang=lang).text} "
                f"({MeetingEditParticipantsMessages.NO_LIMIT_LABEL.get(lang=lang).text})|\n  Joined_0"
            ),
        ),
        (
            2,
            2,
            lambda lang: (
                f"2 {MeetingDisplayMessages.PARTICIPANTS_LABEL.get(lang=lang).text} "
                f"{MeetingDisplayMessages.MAX_PARTICIPANTS_LABEL.get(lang=lang, max_participants=2).text}"
                f"|\n  Joined_0\n  Joined_1"
            ),
        ),
        (
            1,
            2,
            lambda lang: (
                f"1 {MeetingDisplayMessages.PARTICIPANT_LABEL.get(lang=lang).text} "
                f"{MeetingDisplayMessages.MAX_PARTICIPANTS_LABEL.get(lang=lang, max_participants=2).text}|\n  Joined_0"
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

    message = MeetingEditSettingsMessages.DESCRIPTION.get(lang=lang)
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
    ).with_footnote(MeetingAttachMessages.FOOTNOTE_INACTIVE.get(lang=used_language))

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
    ).with_footnote(MeetingAttachMessages.FOOTNOTE_ACTIVE.get(lang=used_language))

    assert expected_view == view
