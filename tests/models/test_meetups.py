from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast
from unittest import mock

import pytest
from telegram import Update

from mitup_bot.callback_data import CallbackData
from mitup_bot.exceptions import MeetupNotFound, NoMessageAvailable
from mitup_bot.models import JoinedUsers, Meetup, MeetupLocation, Message, Settings, User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.emojis import Emojis
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages, sanitize
from mitup_bot.views import MitupView
from mitup_bot.views.factory import options_button
from tests.helpers import UpdateRequest, create_meetup
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
    show_timezone=True,
)
COORDINATES = (123.1, -321.1)


def expected_location_name(lang: str, expected_name: str | None, expected_coordinates: str | None):
    return (
        MeetingMessages.LOCATION_NOT_SET.get(lang=lang)
        if expected_coordinates is None and expected_name is None
        else f"{expected_name or ''} {expected_coordinates or ''}".strip()
    )


def expected_participants_message(max_participants: bool, lang: str) -> str:
    total_participants = f"1 {MeetingMessages.PARTICIPANT.get(lang=lang)}"
    max_participants_text = (
        f"{MeetingMessages.MAX_PARTICIPANTS.get(lang=lang, max_participants=5)}"
        if max_participants
        else f"{MeetingMessages.NO_LIMIT_PARTICIPANTS.get(lang=lang)}"
    )

    return sanitize(f"{total_participants} {max_participants_text}", full=True)


def expected_message(
    lang: str,
    description: bool,
    datetime: bool,
    username: bool,
    location_name: bool,
    coordinates: bool,
    max_participants: bool,
    incognito: bool,
) -> str:
    str_description = "Test Description" if description else MeetingMessages.DESCRIPTION_NOT_SET.get(lang=lang)
    str_date = "1987\\-07\\-17 01:59 \\(Europe/Madrid\\)" if datetime else MeetingMessages.DATE_NOT_SET.get(lang=lang)
    owner = "john\\_doe" if username else "John"
    location = expected_location_name(
        lang=lang,
        expected_name="Test Location" if location_name else None,
        expected_coordinates="\\[📍\\]" if coordinates else None,
    )
    str_participants = expected_participants_message(max_participants, lang=lang)
    incognito_prefix = f"{Emojis.GLASSES} " if incognito else ""
    str_participants = f"{incognito_prefix}{str_participants}\n\t{owner}"

    return (
        f"*Test Meeting* \\({MeetingMessages.CREATED_BY.get(lang=lang, owner=owner)}\\)\n\n"
        f"\\-\\-\\- {Emojis.DESCRIPTION} {str_description}\n"
        f"\\-\\-\\- {Emojis.CLOCK} {str_date}\n"
        f"\\-\\-\\- {Emojis.MAP} {location}\n"
        f"\\-\\-\\- {Emojis.JOINED} {str_participants}"
    )


def expected_inline_message(
    lang: str,
    description: bool,
    datetime: bool,
    username: bool,
    location_name: bool,
    coordinates: bool,
    max_participants: bool,
    incognito: bool,
) -> str:
    owner = "john\\_doe" if username else "John"
    str_participants = expected_participants_message(max_participants, lang=lang)
    incognito_prefix = f"{Emojis.GLASSES} " if incognito else ""
    participants_list = "" if incognito else f"\n\t{owner}"
    str_participants = f"{incognito_prefix}{str_participants}{participants_list}"

    str_location = expected_location_name(
        lang=lang,
        expected_name="Test Location" if location_name else None,
        expected_coordinates="\\[📍\\]" if coordinates else None,
    )
    result = f"*Test Meeting* \\({MeetingMessages.CREATED_BY.get(lang=lang, owner=owner)}\\)\n\n"
    if description:
        result += f"\\-\\-\\- {Emojis.DESCRIPTION} Test Description\n"
    if datetime:
        result += f"\\-\\-\\- {Emojis.CLOCK} 1987\\-07\\-17 01:59 \\(Europe/Madrid\\)\n"
    if str_location != MeetingMessages.LOCATION_NOT_SET.get(lang=lang):
        result += f"\\-\\-\\- {Emojis.MAP} {str_location}\n"

    result += f"\\-\\-\\- {Emojis.JOINED} {str_participants}"
    return result


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
def test_meetup_message(
    settings: Settings,
    description: bool,
    meetup_datetime: bool,
    username: bool,
    location_name: bool,
    location_coordinates: bool,
    max_participants: bool,
    is_inline: bool,
    expected_method: Callable[[str, bool, bool, bool, bool, bool, bool, bool], str],
    lang: str,
    incognito: bool,
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
        show_timezone=True,
    )
    # Have at least one user joined to evaluate the list of user joined
    JoinedUsers(user=owner, meetup=meeting)

    expected = expected_method(
        lang,
        description,
        meetup_datetime,
        username,
        location_name,
        location_coordinates,
        max_participants,
        incognito,
    )

    actual = meeting.inline_message if is_inline else meeting.message
    assert expected == actual


def test_time_properly_converted_for_timezone(settings: Settings):
    meeting = Meetup(
        title="Test Meeting",
        description="Test Description",
        datetime=datetime(2024, 1, 12, 12, 30),
        owner=User(first_name="John", username="john_doe", tg_user_id=1, settings=settings),
        waiting_list=False,
        public=False,
        allow_invitation=False,
        incognito=False,
        show_timezone=True,
    )

    # Expected time is 1 hour ahead of the one in the meeting (in UTC) assuming converstion to settings.tz
    # Europe/Madrid
    expected_time = "2024-01-12 13:30 (Europe/Madrid)"

    assert expected_time == meeting.str_datetime

    # Updatingt he timezone to Europe/Dublin in January the date should be the same but the time should be 12:30
    # since Dublin is in the same timezone as UTC
    settings.timezone = "Europe/Dublin"
    expected_time = "2024-01-12 12:30 (Europe/Dublin)"
    assert expected_time == meeting.str_datetime


@pytest.mark.parametrize(
    "participants,max_participants,expected",
    [
        (1, None, lambda lang: f"1 ({MeetingMessages.NO_LIMIT_PARTICIPANTS.get(lang=lang)})"),
        (0, None, lambda lang: f"{MeetingMessages.EMPTY.get(lang=lang)}"),
        (
            0,
            2,
            lambda lang: (
                f"{MeetingMessages.EMPTY.get(lang=lang)} "
                f"{MeetingMessages.MAX_PARTICIPANTS.get(lang=lang, max_participants=2)}"
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

    assert f"{expected_incognito}{expected(user_with_settings.lang)}" == meeting.participants_badge


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
        show_timezone=True,
    )

    assert expected_description == meeting.short_description


def build_inline_message(
    lang: str, description: str | None, datetime: datetime | None, location: MeetupLocation
) -> str:
    message = ""
    if description:
        message += f"{Emojis.DESCRIPTION} {description}\n"
    message += f"{Emojis.JOINED} {MeetingMessages.EMPTY.get(lang=lang)}\n"
    if datetime:
        message += f"{Emojis.CLOCK} 2024-01-12 13:30 (Europe/Madrid)"
        if location.name:
            message += f" {Emojis.PIN} {location.name}"
    elif location.name:
        message += f"{Emojis.PIN} {location.name}"

    return message


@pytest.mark.parametrize(
    "description",
    ["A short description", None],
    ids=["with_description", "without_description"],
)
@pytest.mark.parametrize(
    "datetime",
    [datetime(2024, 1, 12, 12, 30).astimezone(UTC), None],
    ids=["with_datetime", "without_datetime"],
)
@pytest.mark.parametrize(
    "location",
    [
        MeetupLocation(name="My Location", coordinates=(123, 123)),
        MeetupLocation(),
        MeetupLocation(name="My Location"),
        MeetupLocation(coordinates=(123, 123)),
    ],
    ids=["with_location", "without_location", "with_location_name", "with_location_coordinates"],
)
def test_inline_query_message(
    user_with_settings: User, description: str | None, datetime: datetime | None, location: MeetupLocation
):
    meeting = Meetup(
        title="Test Meeting",
        description=description,
        datetime=datetime,
        location=location,
        owner=user_with_settings,
        waiting_list=False,
        public=False,
        allow_invitation=False,
        incognito=False,
        show_timezone=True,
    )

    message = build_inline_message(user_with_settings.lang, description, datetime, location)

    assert message == meeting.inline_query_message


@pytest.mark.parametrize(
    "joined_count,max_participants,expected",
    [
        (
            0,
            None,
            lambda lang: f"{MeetingMessages.EMPTY.get(lang=lang)} "
            f"{MeetingMessages.NO_LIMIT_PARTICIPANTS.get(lang=lang)}",
        ),
        (
            1,
            None,
            lambda lang: f"1 {MeetingMessages.PARTICIPANT.get(lang=lang)} "
            f"{MeetingMessages.NO_LIMIT_PARTICIPANTS.get(lang=lang)}|\n\tJoined_0",
        ),
        (
            2,
            2,
            lambda lang: f"2 {MeetingMessages.PARTICIPANTS.get(lang=lang)} "
            f"{MeetingMessages.MAX_PARTICIPANTS.get(lang=lang, max_participants=2)}|\n\tJoined_0\n\tJoined_1",
        ),
        (
            1,
            2,
            lambda lang: f"1 {MeetingMessages.PARTICIPANT.get(lang=lang)} "
            f"{MeetingMessages.MAX_PARTICIPANTS.get(lang=lang, max_participants=2)}|\n\tJoined_0",
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
    assert f"{expected_incognito}{expected_text}" == participants_text


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


def test_has_message_returns_false_when_there_is_no_message(meeting: Meetup):
    assert not meeting.has_message(Update(123))


@pytest.mark.parametrize(
    "update,message_id,inline_message_id,chat_id",
    [
        (UpdateRequest(message=True, callback_query=False), 123, None, 123),
        (UpdateRequest(message=False, callback_query=True), 123, None, 123),
        (
            UpdateRequest(message=False, callback_query=CallbackData(entity="test"), inline_message_id="123"),
            None,
            "123",
            None,
        ),
    ],
    ids=["message", "callback_query", "inline_query"],
    indirect=["update"],
)
def test_add_message_to_meeting_from_update(
    meeting: Meetup, update: Update, message_id: int, inline_message_id: str, chat_id: int
):
    message = meeting.add_message(update, meeting.owner)

    assert message.inline_message_id == inline_message_id
    assert message.message_id == message_id
    assert message.chat_id == chat_id


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
    show_timezone = meeting.show_timezone

    message = MeetingMessages.EDIT_SETTINGS_MESSAGE.get(lang=lang)
    waiting_list_button = options_button(
        cb.SET_MEETING_WAITING_LIST.with_id(cast(int, meeting.id)),
        ButtonMessages.WAITING_LIST.get(lang=lang),
        waiting_list,
    )
    public_button = options_button(
        cb.SET_MEETING_PUBLIC.with_id(cast(int, meeting.id)), ButtonMessages.PUBLIC.get(lang=lang), public
    )
    invitation_button = options_button(
        cb.SET_MEETING_ALLOW_INVITATIONS.with_id(cast(int, meeting.id)),
        ButtonMessages.OPEN_INVITATION.get(lang=lang),
        invitation,
    )
    incognito_button = options_button(
        cb.SET_MEETING_INCOGNITO.with_id(cast(int, meeting.id)), ButtonMessages.INCOGNITO.get(lang=lang), incognito
    )
    show_timezone_button = options_button(
        cb.SET_MEETING_SHOW_TIMEZONE.with_id(cast(int, meeting.id)),
        ButtonMessages.SHOW_TIMEZONE.get(lang=lang),
        show_timezone,
    )

    return MitupView(
        message,
        keyboard=[
            [waiting_list_button, public_button],
            [invitation_button, incognito_button],
            [show_timezone_button],
        ],
    ).with_back_button(
        text=ButtonMessages.EDIT, callback_data=cb.EDIT_MEETING.with_id(cast(int, meeting.id)), lang=lang
    )


@pytest.mark.parametrize("waiting_list", [True, False], ids=["waiting_list_true", "waiting_list_false"])
@pytest.mark.parametrize("public", [True, False], ids=["public_true", "public_false"])
@pytest.mark.parametrize("invitation", [True, False], ids=["invitation_true", "invitation_false"])
@pytest.mark.parametrize("incognito", [True, False], ids=["incognito_true", "incognito_false"])
@pytest.mark.parametrize("show_timezone", [True, False], ids=["show_timezone_true", "show_timezone_false"])
def test_default_meeting_options_view(
    waiting_list: bool,
    public: bool,
    invitation: bool,
    incognito: bool,
    show_timezone: bool,
    user_with_settings: User,
):
    meeting = user_with_settings.meetups[0]
    meeting.allow_invitation = invitation
    meeting.incognito = incognito
    meeting.public = public
    meeting.show_timezone = show_timezone
    meeting.waiting_list = waiting_list

    view = meeting.settings_view

    expected_view = expected_meeting_settings_view(meeting)

    assert expected_view == view
