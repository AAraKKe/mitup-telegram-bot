import logging

import pytest
from aws_embedded_metrics.unit import Unit
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot.callback_data import CallbackData
from mitup_bot.custom_context import ContextId
from mitup_bot.exceptions import MalformedCallbackData, UserNotFound
from mitup_bot.handlers.edit_meeting.enums import ConversationMeetingState, EditMeetingHandlerId
from mitup_bot.handlers.edit_meeting.views import edit_max_participants_view, edit_participants_view
from mitup_bot.models import User
from mitup_bot.monitoring import MetricKey
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages
from mitup_bot.views import factory
from tests.helpers import (
    AnyFloat,
    MockApi,
    StubMitupApp,
    StubMitupContext,
    UpdateRequest,
    call_handler,
    create_meetup,
    create_user,
)
from tests.helpers.stub_db import MockDbSession


@pytest.fixture
def api():
    with MockApi.start("mitup_bot.handlers.edit_meeting.edit_meeting_participants") as api:
        yield api


def failure_cases(callback_data: CallbackData):
    return [
        (
            UpdateRequest(callback_query=callback_data),
            "user_with_settings",
            MalformedCallbackData,
            1,
        ),
        (
            UpdateRequest(callback_query=callback_data.with_id(1)),
            "none",
            UserNotFound,
            1,
        ),
    ]


def assert_metrics_for_failure(error_count: int, error_type: type[Exception], context: StubMitupContext):
    expected_metric_names: list[str | MetricKey] = [
        MetricKey.FAULT.with_prefix(error_type.__name__),
        MetricKey.FAULT,
        MetricKey.TIME,
    ]
    expected_metric_values = [error_count, error_count, AnyFloat()]
    expected_metric_units = [Unit.COUNT, Unit.COUNT, Unit.MILLISECONDS]

    context.metrics_engine.assert_handler_metrics_emitted(
        expected_metric_names,
        expected_metric_values,
        units=expected_metric_units,
        exception=error_type,
    )


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_PARTICIPANTS.with_id(2))], indirect=True
)
async def test_edit_meeting_participants_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    api: MockApi,
    app: StubMitupApp,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[1])

    context, _ = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_CALLBACK)

    api.assert_edit_message_called(context, update, edit_participants_view(user_with_settings.meetups[1]))


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_PARTICIPANTS.with_id(999))], indirect=True
)
async def test_edit_meeting_participants_meeting_not_owned(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
    caplog: pytest.LogCaptureFixture,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(create_meetup(999))

    with caplog.at_level(logging.WARNING):
        with MockApi.start("mitup_bot.guards") as _api:
            context, _ = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_CALLBACK)

            assert "User tried 'Edit participants' with a meeting that does not belong to them." in caplog.text
            _api.assert_edit_message_called(context, update, factory.main_menu_view(lang=user_with_settings.lang))

    context.metrics_engine.assert_metrics_emited(
        [MetricKey.ERROR.with_prefix("MeetingNotOwned"), MetricKey.FAULT, MetricKey.TIME],
        [1, 0, AnyFloat()],
        units=[Unit.COUNT, Unit.COUNT, Unit.MILLISECONDS],
        add_handler_dimensions=True,
        add_update_properties=True,
    )


@pytest.mark.parametrize(
    "update, user_fixture, error_type, error_count",
    failure_cases(cb.EDIT_MEETING_PARTICIPANTS),
    indirect=["update"],
    ids=["no_meeting_id", "user_not_found"],
)
async def test_edit_meeting_participants_failures(
    request: pytest.FixtureRequest,
    mock_session: MockDbSession,
    update: Update,
    user_fixture: str,
    error_type: type[Exception],
    error_count: int,
    app: StubMitupApp,
    caplog: pytest.LogCaptureFixture,
    lang: str,  # Need to add it just to make sure the value is available when getting the user fixture
):
    user: User | None = request.getfixturevalue(user_fixture)
    mock_session.add_object(user, "tg_user_id")
    mock_session.add_object(create_meetup(999))

    with caplog.at_level(logging.WARNING):
        with MockApi.start("mitup_bot.guards") as _api:
            context, _ = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_CALLBACK)
            if error_type is None:
                assert "User tried 'Edit participants' with a meeting that does not belong to them." in caplog.text
                _api.assert_edit_message_called(context, update, factory.main_menu_view())

    assert_metrics_for_failure(error_count, error_type, context)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_MAX_PARTICIPANTS.with_id(1))], indirect=True
)
async def test_edit_meeting_max_participants_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    api: MockApi,
    app: StubMitupApp,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    context, result = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_CALLBACK)

    api.assert_send_message_called(context, update, edit_max_participants_view(user_with_settings.meetups[0]))

    assert result is ConversationMeetingState.EDIT_MAX_PARTICIPANTS
    with context.meeting_id(ContextId.EDIT_MEETING_MAX_PARTICIPANTS) as meeting_id:
        assert meeting_id == 1


@pytest.mark.parametrize(
    "update, user_fixture, error_type, error_count",
    failure_cases(cb.EDIT_MEETING_MAX_PARTICIPANTS),
    indirect=["update"],
    ids=["no_meeting_id", "user_not_found"],
)
async def test_edit_meeting_max_participants_failures(
    request: pytest.FixtureRequest,
    mock_session: MockDbSession,
    update: Update,
    user_fixture: str,
    error_type: type[Exception],
    error_count: int,
    app: StubMitupApp,
    caplog: pytest.LogCaptureFixture,
    lang: str,  # Need to add it just to make sure the value is available when getting the user fixture
):
    user: User | None = request.getfixturevalue(user_fixture)
    mock_session.add_object(user, "tg_user_id")
    mock_session.add_object(create_meetup(999))

    with MockApi.start("mitup_bot.guards") as _api:
        with caplog.at_level(logging.WARNING):
            context, _ = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_CALLBACK)

    assert_metrics_for_failure(error_count, error_type, context)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_MAX_PARTICIPANTS.with_id(999))], indirect=True
)
async def test_edit_meeting_max_participants_meeting_not_owned(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
    caplog: pytest.LogCaptureFixture,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(create_meetup(999))

    with caplog.at_level(logging.WARNING):
        with MockApi.start("mitup_bot.guards") as _api:
            context, result = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_CALLBACK)

            assert result is ConversationHandler.END
            assert "User tried 'Edit max participants' with a meeting that does not belong to them." in caplog.text
            _api.assert_edit_message_called(context, update, factory.main_menu_view(lang=user_with_settings.lang))

            assert not context.has_meeting_id(ContextId.EDIT_MEETING_MAX_PARTICIPANTS)

    context.metrics_engine.assert_metrics_emited(
        [MetricKey.ERROR.with_prefix("MeetingNotOwned"), MetricKey.FAULT, MetricKey.TIME],
        [1, 0, AnyFloat()],
        units=[Unit.COUNT, Unit.COUNT, Unit.MILLISECONDS],
        add_handler_dimensions=True,
        add_update_properties=True,
    )


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_NO_LIMIT_PARTICIPANTS.with_id(1))], indirect=True
)
async def test_edit_meeting_no_limit_participants_works(
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

    response_view = edit_participants_view(user_with_settings.meetups[0]).with_context(
        MeetingMessages.MAX_PARTICIPANTS_SET_SUCCESS.get(
            max_participants=MeetingMessages.NO_LIMIT_PARTICIPANTS.get(lang=user_with_settings.lang)
        )
    )
    api.assert_send_message_called(context, update, response_view)
    assert result is ConversationHandler.END


@pytest.mark.parametrize(
    "update, user_fixture, error_type, error_count",
    failure_cases(cb.EDIT_MEETING_NO_LIMIT_PARTICIPANTS),
    indirect=["update"],
    ids=["no_meeting_id", "user_not_found"],
)
async def test_edit_meeting_no_limit_participants_failures(
    request: pytest.FixtureRequest,
    mock_session: MockDbSession,
    update: Update,
    user_fixture: str,
    error_type: type[Exception],
    error_count: int,
    app: StubMitupApp,
    caplog: pytest.LogCaptureFixture,
    lang: str,  # Need to add it just to make sure the value is available when getting the user fixture
):
    user: User | None = request.getfixturevalue(user_fixture)
    mock_session.add_object(user, "tg_user_id")
    mock_session.add_object(create_meetup(999))

    with MockApi.start("mitup_bot.guards") as _api:
        with caplog.at_level(logging.WARNING):
            context, result = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_NO_LIMIT_CALLBACK)

            if error_type is None:
                assert result is ConversationHandler.END
                assert (
                    "User tried 'Edit no limit participants' with a meeting that does not belong to them."
                    in caplog.text
                )
                _api.assert_edit_message_called(context, update, factory.main_menu_view())

    assert_metrics_for_failure(error_count, error_type, context)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_NO_LIMIT_PARTICIPANTS.with_id(999))], indirect=True
)
async def test_edit_meeting_no_limit_participants_meeting_not_owned(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
    caplog: pytest.LogCaptureFixture,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(create_meetup(999))

    with caplog.at_level(logging.WARNING):
        with MockApi.start("mitup_bot.guards") as _api:
            context, result = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_NO_LIMIT_CALLBACK)

            assert result is ConversationHandler.END
            assert "User tried 'Edit no limit participants' with a meeting that does not belong to them." in caplog.text
            _api.assert_edit_message_called(context, update, factory.main_menu_view(lang=user_with_settings.lang))

    context.metrics_engine.assert_metrics_emited(
        [MetricKey.ERROR.with_prefix("MeetingNotOwned"), MetricKey.FAULT, MetricKey.TIME],
        [1, 0, AnyFloat()],
        units=[Unit.COUNT, Unit.COUNT, Unit.MILLISECONDS],
        add_handler_dimensions=True,
        add_update_properties=True,
    )


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.CANCEL_EDIT_MEETING_PARTICIPANS.with_id(2))], indirect=True
)
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

    api.assert_edit_message_called(context, update, edit_participants_view(user_with_settings.meetups[1]))
    assert result is ConversationHandler.END


@pytest.mark.parametrize(
    "update",
    [
        (UpdateRequest(message_text="0")),
        (UpdateRequest(message_text="-3")),
        (UpdateRequest(message_text="not a number")),
    ],
    indirect=["update"],
    ids=["zero_participants", "negative_participants", "not_a_number"],
)
async def test_positive_filter_works(
    caplog: pytest.LogCaptureFixture,
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    api: MockApi,
    app: StubMitupApp,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)

    with pytest.raises(AssertionError):
        with caplog.at_level(logging.ERROR):
            # If the update is not a positive number, the handler should not be able to process it
            _, _ = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_MESSAGE)
            assert "This update would not be processed by the handler!" in caplog.text


@pytest.mark.parametrize("update", [UpdateRequest(message_text="4")], indirect=True)
async def test_edit_meeting_max_participants_message_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    api: MockApi,
    app: StubMitupApp,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    # Default value to assert that it has been changed
    meeting.max_members = 10

    context, result = await call_handler(
        update,
        app,
        EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_MESSAGE,
        with_meeting_id={ContextId.EDIT_MEETING_MAX_PARTICIPANTS: 1},
    )

    expected_view = edit_participants_view(meeting).with_context(
        MeetingMessages.MAX_PARTICIPANTS_SET_SUCCESS.get(max_participants=meeting.max_members)
    )

    assert meeting.max_members == 4
    mock_session.assert_flushed()
    api.assert_send_message_called(context, update, expected_view)
    assert result is ConversationHandler.END

    assert not context.has_meeting_id(ContextId.EDIT_MEETING_MAX_PARTICIPANTS)


@pytest.mark.parametrize("update", [UpdateRequest(message_text="420")], indirect=True)
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
    mock_session.add_object(user_with_settings, "tg_user_id")

    with caplog.at_level(logging.ERROR):
        context, result = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_MESSAGE)
        assert "User data 'meeting_id' requested but not set" in caplog.text

    assert meeting.location.name is None
    mock_session.assert_not_flushed()
    assert result is ConversationHandler.END
    api.assert_edit_message_called(context, update, factory.main_menu_view(lang=user_with_settings.lang))


@pytest.mark.parametrize(
    "update",
    [
        (UpdateRequest(message_text="0")),
        (UpdateRequest(message_text="-3")),
        (UpdateRequest(message_text="not a number")),
    ],
    indirect=["update"],
    ids=["zero_participants", "negative_participants", "not_a_number"],
)
async def test_edit_meeting_wrong_max_participants_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    api: MockApi,
    app: StubMitupApp,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    context, result = await call_handler(
        update,
        app,
        EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_WRONG_MESSAGE,
        with_meeting_id={ContextId.EDIT_MEETING_MAX_PARTICIPANTS: 1},
    )
    response_view = edit_max_participants_view(user_with_settings.meetups[0], fail=True)

    assert result is ConversationMeetingState.EDIT_MAX_PARTICIPANTS
    api.assert_send_message_called(context, update, response_view)


@pytest.mark.parametrize("update", [(UpdateRequest(message_text="no number today"))], indirect=True)
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
    mock_session.add_object(user_with_settings, "tg_user_id")

    with caplog.at_level(logging.ERROR):
        context, result = await call_handler(update, app, EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_WRONG_MESSAGE)
        assert "User data 'meeting_id' requested but not set" in caplog.text

    assert meeting.max_members is None
    mock_session.assert_not_flushed()
    assert result is ConversationHandler.END
    api.assert_edit_message_called(context, update, factory.main_menu_view(lang=user_with_settings.lang))


def test_edit_meeting_participants_view_without_participants(
    user_with_settings: User,
    mock_session: MockDbSession,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    view = edit_participants_view(user_with_settings.meetups[0])

    # Without any participants the view only has the max participants button
    # The keyboard has the max participants button and the back button
    assert len(view.keyboard) == 2
    # The first row only has one
    assert len(view.keyboard[0]) == 1
    assert view.keyboard[0][0].text == ButtonMessages.MEETING_MAX_PARTICIPANTS.get(lang=user_with_settings.lang)


def test_edit_meeting_participants_view_with_participants_shows_kick_out_button(
    user_with_settings: User,
    mock_session: MockDbSession,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])
    other_user = create_user(id=2, username="other_user", first_name="Other User")
    user_with_settings.meetups[0].add_participant(other_user)

    view = edit_participants_view(user_with_settings.meetups[0])

    # The keyboard has the 2 rows of buttons
    assert len(view.keyboard) == 2
    # The first row now has 2 buttons
    assert len(view.keyboard[0]) == 2
    assert view.keyboard[0][0].text == ButtonMessages.MEETING_MAX_PARTICIPANTS.get(lang=user_with_settings.lang)
    assert view.keyboard[0][1].text == ButtonMessages.MEETING_KICK_OUT.get(lang=user_with_settings.lang)
