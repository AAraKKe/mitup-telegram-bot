import logging
from contextlib import nullcontext
from typing import cast

import pytest
from _pytest.python_api import RaisesContext
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot.callback_data import CallbackData
from mitup_bot.custom_context import ContextId
from mitup_bot.exceptions import MalformedCallbackData, UserNotFound
from mitup_bot.handlers.edit_meeting.enums import ConversationMeetingState, EditMeetingHandlerId
from mitup_bot.handlers.edit_meeting.views import edit_max_participants_view, edit_participants_view
from mitup_bot.models import User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import MeetingMessages
from mitup_bot.utils.types import StubMitupApp
from mitup_bot.views import factory
from tests.helpers import MockApi, UpdateRequest, call_handler, create_meetup
from tests.stub_db import MockDbSession


@pytest.fixture
def api():
    with MockApi.start("mitup_bot.handlers.edit_meeting.edit_meeting_participants") as api:
        yield api


def failure_cases(callback_data: CallbackData):
    return [
        (
            UpdateRequest(callback_query=callback_data),
            "user_with_settings",
            pytest.raises(MalformedCallbackData),
        ),
        (
            UpdateRequest(callback_query=callback_data.with_id(1)),
            "none",
            pytest.raises(UserNotFound),
        ),
        (
            UpdateRequest(callback_query=callback_data.with_id(999)),
            "user_with_settings",
            nullcontext(),
        ),
    ]


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_PARTICIPANTS.with_id(2))], indirect=True
)
@pytest.mark.asyncio
async def test_callback_edit_meeting_participants_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    api: MockApi,
    app: StubMitupApp,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[1])

    context, _ = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_CALLBACK)

    api.assert_edit_message_called(context, update, edit_participants_view(2))


@pytest.mark.parametrize(
    "update, user_fixture, expectation",
    failure_cases(cb.EDIT_MEETING_PARTICIPANTS),
    indirect=["update"],
    ids=["no_meeting_id", "user_not_found", "user_does_not_own_meeting"],
)
@pytest.mark.asyncio
async def test_callback_edit_meeting_participants_failures(
    request: pytest.FixtureRequest,
    mock_session: MockDbSession,
    update: Update,
    user_fixture: str,
    expectation: RaisesContext,
    app: StubMitupApp,
    caplog: pytest.LogCaptureFixture,
):
    user: User | None = request.getfixturevalue(user_fixture)
    mock_session.add_object(user, "tg_user_id")
    mock_session.add_object(create_meetup(999))

    with expectation:
        with caplog.at_level(logging.WARNING):
            with MockApi.start("mitup_bot.guards") as _api:
                context, _ = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_CALLBACK)
                assert "User tried 'Edit participants' with a meeting that does not belong to them." in caplog.text
                _api.assert_edit_message_called(context, update, factory.main_menu_view())


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_MAX_PARTICIPANTS.with_id(1))], indirect=True
)
@pytest.mark.asyncio
async def test_callback_edit_meeting_max_participants_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    api: MockApi,
    app: StubMitupApp,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    context, result = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_CALLBACK)

    api.assert_send_message_called(context, update, edit_max_participants_view(1))

    assert result is ConversationMeetingState.EDIT_MAX_PARTICIPANTS
    with context.meeting_id(ContextId.EDIT_MEETING_MAX_PARTICIPANTS) as meeting_id:
        assert meeting_id == 1


@pytest.mark.parametrize(
    "update, user_fixture, expectation",
    failure_cases(cb.EDIT_MEETING_MAX_PARTICIPANTS),
    indirect=["update"],
    ids=["no_meeting_id", "user_not_found", "user_does_not_own_meeting"],
)
@pytest.mark.asyncio
async def test_callback_edit_meeting_max_participants_failures(
    request: pytest.FixtureRequest,
    mock_session: MockDbSession,
    update: Update,
    user_fixture: str,
    expectation: RaisesContext,
    app: StubMitupApp,
    caplog: pytest.LogCaptureFixture,
):
    user: User | None = request.getfixturevalue(user_fixture)
    mock_session.add_object(user, "tg_user_id")
    mock_session.add_object(create_meetup(999))

    with expectation:
        with MockApi.start("mitup_bot.guards") as _api:
            with caplog.at_level(logging.WARNING):
                context, result = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_CALLBACK)

                assert result is ConversationHandler.END
                assert "User tried 'Edit max participants' with a meeting that does not belong to them." in caplog.text
                _api.assert_edit_message_called(context, update, factory.main_menu_view())

                assert not context.has_meeting_id(ContextId.EDIT_MEETING_MAX_PARTICIPANTS)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_NO_LIMIT_PARTICIPANTS.with_id(1))], indirect=True
)
async def test_callback_edit_meeting_no_limit_participants_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    api: MockApi,
    app: StubMitupApp,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    meeting = user_with_settings.meetups[0]

    # Default value to assert that it has been changed
    meeting.max_members = 10

    context, result = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_NO_LIMIT_CALLBACK)

    mock_session.assert_flushed()
    assert not meeting.max_members

    response_view = edit_participants_view(1).with_context(
        MeetingMessages.MAX_PARTICIPANTS_SET_SUCCESS.get(max_participants=MeetingMessages.NO_LIMIT_PARTICIPANTS.get())
    )
    api.assert_send_message_called(context, update, response_view)
    assert result is ConversationHandler.END


@pytest.mark.parametrize(
    "update, user_fixture, expectation",
    failure_cases(cb.EDIT_MEETING_NO_LIMIT_PARTICIPANTS),
    indirect=["update"],
    ids=["no_meeting_id", "user_not_found", "user_does_not_own_meeting"],
)
async def test_callback_edit_meeting_no_limit_participants_failures(
    request: pytest.FixtureRequest,
    mock_session: MockDbSession,
    update: Update,
    user_fixture: str,
    expectation: RaisesContext,
    app: StubMitupApp,
    caplog: pytest.LogCaptureFixture,
):
    user: User | None = request.getfixturevalue(user_fixture)
    mock_session.add_object(user, "tg_user_id")
    mock_session.add_object(create_meetup(999))

    with expectation:
        with MockApi.start("mitup_bot.guards") as _api:
            with caplog.at_level(logging.WARNING):
                context, result = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_NO_LIMIT_CALLBACK)

                assert result is ConversationHandler.END
                assert (
                    "User tried 'Edit no limit participants' with a meeting that does not belong to them."
                    in caplog.text
                )
                _api.assert_edit_message_called(context, update, factory.main_menu_view())


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.CANCEL_EDIT_MEETING_PARTICIPANS.with_id(2))], indirect=True
)
@pytest.mark.asyncio
async def test_callback_cancel_edit_meeting_participants_property_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    api: MockApi,
    app: StubMitupApp,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[1])

    context, result = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_CANCEL_CALLBACK)

    api.assert_edit_message_called(context, update, edit_participants_view(2))
    assert result is ConversationHandler.END


@pytest.mark.parametrize(
    "update, expectation",
    [
        (UpdateRequest(message="0"), pytest.raises(AssertionError)),
        (UpdateRequest(message="-3"), pytest.raises(AssertionError)),
        (UpdateRequest(message="not a number"), pytest.raises(AssertionError)),
    ],
    indirect=["update"],
    ids=["zero_participants", "negative_participants", "not_a_number"],
)
async def test_positive_filter_works(
    caplog: pytest.LogCaptureFixture,
    mock_session: MockDbSession,
    update: Update,
    expectation: RaisesContext,
    user_with_settings: User,
    api: MockApi,
    app: StubMitupApp,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)

    with expectation:
        with caplog.at_level(logging.ERROR):
            # If the update is not a positive number, the handler should not be able to process it
            _, _ = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_MESSAGE)
            assert "This update would not be processed by the handler!" in caplog.text


@pytest.mark.parametrize("update", [UpdateRequest(message="4")], indirect=True)
async def test_edit_meeting_max_participants_message_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    api: MockApi,
    app: StubMitupApp,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)

    # Default value to assert that it has been changed
    meeting.max_members = 10

    context, result = await call_handler(
        update,
        app,
        EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_MESSAGE,
        with_meeting_id=(ContextId.EDIT_MEETING_MAX_PARTICIPANTS, 1),
    )

    expected_view = edit_participants_view(cast(int, meeting.id)).with_context(
        MeetingMessages.MAX_PARTICIPANTS_SET_SUCCESS.get(max_participants=meeting.max_members)
    )

    assert meeting.max_members == 4
    mock_session.assert_flushed()
    api.assert_send_message_called(context, update, expected_view)
    assert result is ConversationHandler.END

    assert not context.has_meeting_id(ContextId.EDIT_MEETING_MAX_PARTICIPANTS)


@pytest.mark.parametrize("update", [UpdateRequest(message="420")], indirect=True)
@pytest.mark.asyncio
async def test_edit_max_participants_message_fails_if_context_not_saved(
    caplog: pytest.LogCaptureFixture,
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    api: MockApi,
    app: StubMitupApp,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)

    with caplog.at_level(logging.ERROR):
        context, result = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_MESSAGE)
        assert "User data 'meeting_id' requested but not set" in caplog.text

    assert meeting.location.name is None
    mock_session.assert_not_flushed()
    assert result is ConversationHandler.END
    api.assert_edit_message_called(context, update, factory.main_menu_view())


@pytest.mark.parametrize(
    "update",
    [(UpdateRequest(message="0")), (UpdateRequest(message="-3")), (UpdateRequest(message="not a number"))],
    indirect=["update"],
    ids=["zero_participants", "negative_participants", "not_a_number"],
)
@pytest.mark.asyncio
async def test_edit_meeting_wrong_max_participants_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    api: MockApi,
    app: StubMitupApp,
):
    context, result = await call_handler(
        update,
        app,
        EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_WRONG_MESSAGE,
        with_meeting_id=(ContextId.EDIT_MEETING_MAX_PARTICIPANTS, 1),
    )
    response_view = edit_max_participants_view(1, fail=True)

    assert result is ConversationMeetingState.EDIT_MAX_PARTICIPANTS
    api.assert_send_message_called(context, update, response_view)


@pytest.mark.parametrize("update", [(UpdateRequest(message="no number today"))], indirect=True)
@pytest.mark.asyncio
async def test_edit_meeting_wrong_max_participants_fails_if_context_not_saved(
    caplog: pytest.LogCaptureFixture,
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    api: MockApi,
    app: StubMitupApp,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)

    with caplog.at_level(logging.ERROR):
        context, result = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_WRONG_MESSAGE)
        assert "User data 'meeting_id' requested but not set" in caplog.text

    assert meeting.max_members is None
    mock_session.assert_not_flushed()
    assert result is ConversationHandler.END
    api.assert_edit_message_called(context, update, factory.main_menu_view())
