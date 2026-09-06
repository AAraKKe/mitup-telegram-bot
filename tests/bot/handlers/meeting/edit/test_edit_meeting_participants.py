import logging

import pytest
from structlog.testing import capture_logs
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot import supporter
from mitup_bot.callback_data import CallbackData
from mitup_bot.config import LimitsConfig
from mitup_bot.custom_context import ContextId
from mitup_bot.exceptions import MalformedCallbackData, UserNotFound
from mitup_bot.handlers.meeting.edit.enums import ConversationMeetingState, EditMeetingHandlerId
from mitup_bot.handlers.meeting.edit.views import edit_max_participants_view
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import MeetingCounts, Message, User
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import (
    ButtonMessages,
    CommonMessages,
    MeetingEditParticipantsMessages,
    SupporterMessages,
)
from mitup_bot.views import RenderContext, factory
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.collaborate import collaborate_button
from tests.helpers import (
    AnyFloat,
    HandlerContext,
    UpdateRequest,
    assert_context_lost_logged,
    assert_locked_meetup_select,
    assert_meeting_rejection_logged,
    call_handler,
    create_meetup,
    create_member,
    owner_with_meeting,
)
from tests.helpers.conversation import ConversationStep, ConversationTester
from tests.helpers.monitoring import MetricAssertions
from tests.helpers.stub_db import MockDbSession


def failure_cases(callback_data: CallbackData):
    return [
        (
            UpdateRequest(callback_query=callback_data),
            "user_with_settings",
            MalformedCallbackData,
        ),
        (
            UpdateRequest(callback_query=callback_data.with_id(1)),
            "none",
            UserNotFound,
        ),
    ]


def assert_metrics_for_failure(error_type: type[Exception], metrics_client: MetricsClient):
    """Assert the invocation closed with exactly one `Fault` sample, naming its class when it faulted.

    A caller with no account row is an expected business state the error handler answers, so its
    sample is a 0 carrying no `error_type`; every other failure reaching here is a genuine fault.
    """
    handled = issubclass(error_type, UserNotFound)
    metrics = MetricAssertions(metrics_client)
    metrics.assert_emitted(
        name=MetricKey.FAULT, value=0 if handled else 1, times=1, exception=None if handled else error_type
    )
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_PARTICIPANTS.with_id(2))], indirect=True
)
async def test_edit_meeting_participants_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[1])

    context, _ = await call_handler(EditMeetingHandlerId.PARTICIPANTS_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_called(
        update,
        meeting_views.owner_view(user_with_settings.meetups[1]).with_context(
            CommonMessages.EDITING_REVAMP_BANNER.rich(lang=user_with_settings.lang)
        ),
    )


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_PARTICIPANTS.with_id(999))], indirect=True
)
async def test_edit_meeting_participants_meeting_not_owned(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(create_meetup(999, owner=create_member(id=2, tg_user_id=456)))

    with caplog.at_level(logging.WARNING):
        context, _ = await call_handler(EditMeetingHandlerId.PARTICIPANTS_CALLBACK, handler_context=handler_context)

        assert_meeting_rejection_logged(caplog, action="Edit participants", reason="meeting_not_owned")
        context.api.assert_edit_message_called(
            update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang))
        )

    metrics.assert_emitted(name=MetricKey.MEETING_NOT_OWNED, value=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)


@pytest.mark.parametrize(
    "update, user_fixture, error_type",
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
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
    lang: str,  # Need to add it just to make sure the value is available when getting the user fixture
    metrics_client: MetricsClient,
):
    user: User | None = request.getfixturevalue(user_fixture)
    mock_session.add_object(user, "tg_user_id")
    mock_session.add_object(create_meetup(999, owner=create_member(id=2, tg_user_id=456)))

    with caplog.at_level(logging.WARNING):
        context, _ = await call_handler(EditMeetingHandlerId.PARTICIPANTS_CALLBACK, handler_context=handler_context)
        if error_type is None:
            assert_meeting_rejection_logged(caplog, action="Edit participants", reason="meeting_not_owned")
            context.api.assert_edit_message_called(update, factory.main_menu_view())

    assert_metrics_for_failure(error_type, metrics_client)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_MAX_PARTICIPANTS.with_id(1))], indirect=True
)
async def test_edit_meeting_max_participants_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    context, result = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_CALLBACK, handler_context=handler_context
    )

    context.api.assert_edit_message_called(update, edit_max_participants_view(user_with_settings.meetups[0]))

    assert result is ConversationMeetingState.EDIT_MAX_PARTICIPANTS
    with context.meeting_id(ContextId.EDIT_MEETING_MAX_PARTICIPANTS) as meeting_id:
        assert meeting_id == 1


@pytest.mark.parametrize(
    "update, user_fixture, error_type",
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
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
    lang: str,  # Need to add it just to make sure the value is available when getting the user fixture
    metrics_client: MetricsClient,
):
    user: User | None = request.getfixturevalue(user_fixture)
    mock_session.add_object(user, "tg_user_id")
    mock_session.add_object(create_meetup(999, owner=create_member(id=2, tg_user_id=456)))

    with caplog.at_level(logging.WARNING):
        context, _ = await call_handler(
            EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_CALLBACK, handler_context=handler_context
        )

    assert_metrics_for_failure(error_type, metrics_client)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_MAX_PARTICIPANTS.with_id(999))], indirect=True
)
async def test_edit_meeting_max_participants_meeting_not_owned(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(create_meetup(999, owner=create_member(id=2, tg_user_id=456)))

    with caplog.at_level(logging.WARNING):
        context, result = await call_handler(
            EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_CALLBACK, handler_context=handler_context
        )

        assert result == ConversationHandler.END
        assert_meeting_rejection_logged(caplog, action="Edit max participants", reason="meeting_not_owned")
        context.api.assert_edit_message_called(
            update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang))
        )

        assert not context.has_meeting_id(ContextId.EDIT_MEETING_MAX_PARTICIPANTS)

    metrics.assert_emitted(name=MetricKey.MEETING_NOT_OWNED, value=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_NO_LIMIT_PARTICIPANTS.with_id(1))], indirect=True
)
async def test_edit_meeting_no_limit_participants_reports_cap_for_capped_owner(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    monkeypatch: pytest.MonkeyPatch,
):
    """Clearing the limit as a capped owner stores None but confirms the plan's cap, since the
    effective capacity clamps there rather than becoming unlimited."""
    monkeypatch.setattr(supporter.PolicyState, "config", LimitsConfig(free_participant_capacity=20))
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    meeting = user_with_settings.meetups[0]

    # Default value to assert that it has been changed
    meeting.max_members = 10

    context, result = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_NO_LIMIT_CALLBACK, handler_context=handler_context
    )

    # No explicit flush: the capacity change lands at commit, before the queued send runs.
    mock_session.assert_not_flushed()
    assert not meeting.max_members

    response_view = meeting_views.owner_view(user_with_settings.meetups[0]).with_context(
        MeetingEditParticipantsMessages.MAX_SUCCESS.rich(max_participants="20")
    )
    context.api.assert_send_message_called(update, response_view)
    assert result is ConversationHandler.END


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_NO_LIMIT_PARTICIPANTS.with_id(1))], indirect=True
)
async def test_edit_meeting_no_limit_participants_reports_no_limit_for_uncapped_owner(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """An uncapped (Gamemaster) owner clearing the limit gets the true no-limit confirmation."""
    user_with_settings.supporter_level = supporter.SupporterLevel.HOST_2
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    meeting = user_with_settings.meetups[0]
    meeting.max_members = 10

    context, result = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_NO_LIMIT_CALLBACK, handler_context=handler_context
    )

    assert not meeting.max_members
    response_view = meeting_views.owner_view(user_with_settings.meetups[0]).with_context(
        MeetingEditParticipantsMessages.MAX_SUCCESS.rich(
            max_participants=MeetingEditParticipantsMessages.NO_LIMIT_LABEL.rich(lang=user_with_settings.lang)
        )
    )
    context.api.assert_send_message_called(update, response_view)
    assert result is ConversationHandler.END


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_NO_LIMIT_PARTICIPANTS.with_id(1))], indirect=True
)
async def test_no_limit_participants_updates_shared_meeting_messages(
    mock_session: MockDbSession,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """The cap is part of what every shared card shows, so clearing it fans out to the tracked
    meeting messages instead of only refreshing the owner's bot chat."""
    mock_session.add_object(user_with_settings, "tg_user_id")

    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    meeting.max_members = 5
    meeting.messages.append(Message(message_id=111, chat_id=222, meetup=meeting))

    context, _ = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_NO_LIMIT_CALLBACK, handler_context=handler_context
    )

    context.api.assert_update_meeting_messages_called(meeting)


@pytest.mark.parametrize("update", [UpdateRequest(message_text="4")], indirect=True)
async def test_max_participants_message_updates_shared_meeting_messages(
    mock_session: MockDbSession,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """Setting a numeric cap fans out to the tracked meeting messages, matching the no-limit path."""
    mock_session.add_object(user_with_settings, "tg_user_id")

    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    meeting.messages.append(Message(message_id=111, chat_id=222, meetup=meeting))

    context, _ = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_MAX_PARTICIPANTS: 1},
    )

    context.api.assert_update_meeting_messages_called(meeting)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_NO_LIMIT_PARTICIPANTS.with_id(1))], indirect=True
)
async def test_no_limit_participants_loads_meeting_with_row_lock(
    mock_session: MockDbSession,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """Wiring guard for the per-meeting mutex (#187): lifting the participant cap races concurrent
    joins reading `full`, so the callback must load the meeting with for_update=True. The
    serialization behavior is covered on real Postgres in
    tests/models/db_behavior/test_meeting_row_locks.py; this only pins the call site."""
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    await call_handler(EditMeetingHandlerId.PARTICIPANTS_NO_LIMIT_CALLBACK, handler_context=handler_context)

    assert_locked_meetup_select(mock_session)


@pytest.mark.parametrize("update", [UpdateRequest(message_text="4")], indirect=True)
async def test_max_participants_message_loads_meeting_with_row_lock(
    mock_session: MockDbSession,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """Wiring guard for the per-meeting mutex (#187): the max-participants number message mutates
    capacity, so the handler must re-load the meeting with for_update=True before writing. The
    serialization behavior is covered on real Postgres in
    tests/models/db_behavior/test_meeting_row_locks.py; this only pins the call site."""
    mock_session.add_object(user_with_settings.meetups[0])
    mock_session.add_object(user_with_settings, "tg_user_id")

    await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_MAX_PARTICIPANTS: 1},
    )

    assert_locked_meetup_select(mock_session)


@pytest.mark.parametrize(
    "update, user_fixture, error_type",
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
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
    lang: str,  # Need to add it just to make sure the value is available when getting the user fixture
    metrics_client: MetricsClient,
):
    user: User | None = request.getfixturevalue(user_fixture)
    mock_session.add_object(user, "tg_user_id")
    mock_session.add_object(create_meetup(999, owner=create_member(id=2, tg_user_id=456)))

    with caplog.at_level(logging.WARNING):
        context, result = await call_handler(
            EditMeetingHandlerId.PARTICIPANTS_NO_LIMIT_CALLBACK, handler_context=handler_context
        )

        if error_type is None:
            assert result is ConversationHandler.END
            assert_meeting_rejection_logged(caplog, action="Edit no limit participants", reason="meeting_not_owned")
            context.api.assert_edit_message_called(update, factory.main_menu_view())

    assert_metrics_for_failure(error_type, metrics_client)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.EDIT_MEETING_NO_LIMIT_PARTICIPANTS.with_id(999))], indirect=True
)
async def test_edit_meeting_no_limit_participants_meeting_not_owned(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(create_meetup(999, owner=create_member(id=2, tg_user_id=456)))

    with caplog.at_level(logging.WARNING):
        context, result = await call_handler(
            EditMeetingHandlerId.PARTICIPANTS_NO_LIMIT_CALLBACK, handler_context=handler_context
        )

        assert result == ConversationHandler.END
        assert_meeting_rejection_logged(caplog, action="Edit no limit participants", reason="meeting_not_owned")
        context.api.assert_edit_message_called(
            update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang))
        )

    metrics.assert_emitted(name=MetricKey.MEETING_NOT_OWNED, value=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.CANCEL_EDIT_MEETING_PARTICIPANS.with_id(2))], indirect=True
)
async def test_callback_cancel_edit_meeting_participants_property_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[1])

    context, result = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_CANCEL_CALLBACK, handler_context=handler_context
    )

    context.api.assert_edit_message_called(update, meeting_views.owner_view(user_with_settings.meetups[1]))
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
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)

    with pytest.raises(AssertionError):
        with caplog.at_level(logging.ERROR):
            # If the update is not a positive number, the handler should not be able to process it
            _, _ = await call_handler(
                EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_MESSAGE, handler_context=handler_context
            )
            assert "This update would not be processed by the handler!" in caplog.text


@pytest.mark.parametrize("update", [UpdateRequest(message_text="4")], indirect=True)
async def test_edit_meeting_max_participants_message_works(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    # Default value to assert that it has been changed
    meeting.max_members = 10

    context, result = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_MAX_PARTICIPANTS: 1},
    )

    expected_view = meeting_views.owner_view(meeting)

    assert meeting.max_members == 4
    # No explicit flush: the capacity change lands at commit, before the queued sends run.
    mock_session.assert_not_flushed()
    context.api.assert_send_message_called(update, expected_view)
    assert result is ConversationHandler.END

    assert not context.has_meeting_id(ContextId.EDIT_MEETING_MAX_PARTICIPANTS)


@pytest.mark.parametrize("update", [UpdateRequest(message_text="21")], indirect=True)
async def test_edit_max_participants_free_owner_over_cap_is_rejected(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    monkeypatch: pytest.MonkeyPatch,
):
    """A free owner asking for more than the cap gets the upsell banner, keeps their current limit,
    and stays in the max-participants state so they can try again."""
    monkeypatch.setattr(supporter.PolicyState, "config", LimitsConfig(free_participant_capacity=20))
    meeting = user_with_settings.meetups[0]
    meeting.max_members = 5  # a known current value to prove it is left untouched
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, result = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_MAX_PARTICIPANTS: 1},
    )

    # The banner is addressed to the acting owner, so it renders in their language with the cap,
    # and carries the Collaborate upsell button inline where the text names it.
    rejection = SupporterMessages.PARTICIPANT_CAPACITY_EXCEEDED.rich(
        lang=user_with_settings.lang, cap=20, button_collaborate=collaborate_button(user_with_settings.lang)
    )
    expected_view = edit_max_participants_view(meeting).with_context(rejection)

    assert meeting.max_members == 5  # unchanged
    mock_session.assert_not_flushed()
    context.api.assert_send_message_called(update, expected_view)
    assert result is ConversationMeetingState.EDIT_MAX_PARTICIPANTS

    # The retry the banner invites lands in this same handler and needs the stored meeting id, so
    # a refusal must leave it behind.
    assert context.has_meeting_id(ContextId.EDIT_MEETING_MAX_PARTICIPANTS)


async def test_edit_max_participants_retry_after_a_refusal_sets_the_limit(
    mock_session: MockDbSession,
    user_with_settings: User,
    conversation: ConversationTester,
    monkeypatch: pytest.MonkeyPatch,
):
    """A refused number followed by a valid one succeeds on the second try.

    The whole point of keeping the owner in the state is that they can answer the banner, so the
    refusal and the retry run through the real conversation here rather than as isolated calls:
    the retry only works if the first pass left the stored meeting id alone.
    """
    monkeypatch.setattr(supporter.PolicyState, "config", LimitsConfig(free_participant_capacity=20))
    meeting = user_with_settings.meetups[0]
    meeting.max_members = 5
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    steps = [
        ConversationStep.callback(
            cb.EDIT_MEETING_MAX_PARTICIPANTS.with_id(1),
            expected_state=ConversationMeetingState.EDIT_MAX_PARTICIPANTS,
        ),
        ConversationStep.message("99999", expected_state=ConversationMeetingState.EDIT_MAX_PARTICIPANTS),
        ConversationStep.message("16", expected_state=None),
    ]

    result = await conversation.run(
        handler_id=EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_CONVERSATION,
        steps=steps,
    )

    assert meeting.max_members == 16
    retry = result.get_step(2)
    retry.context.api.assert_send_message_called(retry.context.get_update(), meeting_views.owner_view(meeting))
    # The flow is over, so the stored id goes with it.
    assert not retry.context.has_meeting_id(ContextId.EDIT_MEETING_MAX_PARTICIPANTS)


@pytest.mark.parametrize("update", [UpdateRequest(message_text="20")], indirect=True)
async def test_edit_max_participants_free_owner_at_cap_is_accepted(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    monkeypatch: pytest.MonkeyPatch,
):
    """The cap itself is a valid limit: a free owner entering exactly the cap succeeds."""
    monkeypatch.setattr(supporter.PolicyState, "config", LimitsConfig(free_participant_capacity=20))
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, result = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_MAX_PARTICIPANTS: 1},
    )

    expected_view = meeting_views.owner_view(meeting)

    assert meeting.max_members == 20
    context.api.assert_send_message_called(update, expected_view)
    assert result is ConversationHandler.END


@pytest.mark.parametrize("update", [UpdateRequest(message_text="500")], indirect=True)
async def test_edit_max_participants_patron_owner_over_cap_is_accepted(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    monkeypatch: pytest.MonkeyPatch,
):
    """A Patron owner is uncapped, so a limit well above the free cap is stored as-is."""
    monkeypatch.setattr(supporter.PolicyState, "config", LimitsConfig(free_participant_capacity=20))
    user_with_settings.supporter_level = SupporterLevel.HOST_2
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, result = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_MAX_PARTICIPANTS: 1},
    )

    expected_view = meeting_views.owner_view(meeting)

    assert meeting.max_members == 500
    context.api.assert_send_message_called(update, expected_view)
    assert result is ConversationHandler.END


@pytest.mark.parametrize("update", [UpdateRequest(message_text="420")], indirect=True)
async def test_edit_max_participants_message_fails_if_context_not_saved(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    with capture_logs() as logs:
        context, result = await call_handler(
            EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_MESSAGE, handler_context=handler_context
        )
    assert_context_lost_logged(logs, ContextId.EDIT_MEETING_MAX_PARTICIPANTS)

    assert meeting.location.name is None
    mock_session.assert_not_flushed()
    assert result is ConversationHandler.END
    context.api.assert_send_message_called(
        update,
        factory.main_menu_view(
            RenderContext(lang=user_with_settings.lang),
            message=CommonMessages.CONTEXT_LOST.rich(lang=user_with_settings.lang),
            counts=MeetingCounts(0, 0, 0),
        ),
    )


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
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    context, result = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_WRONG_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_MAX_PARTICIPANTS: 1},
    )
    response_view = edit_max_participants_view(user_with_settings.meetups[0], fail=True)

    assert result is ConversationMeetingState.EDIT_MAX_PARTICIPANTS
    context.api.assert_send_message_called(update, response_view)


@pytest.mark.parametrize("update", [(UpdateRequest(message_text="no number today"))], indirect=True)
async def test_edit_meeting_wrong_max_participants_fails_if_context_not_saved(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    with capture_logs() as logs:
        context, result = await call_handler(
            EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_WRONG_MESSAGE, handler_context=handler_context
        )
    assert_context_lost_logged(logs, ContextId.EDIT_MEETING_MAX_PARTICIPANTS)

    assert meeting.max_members is None
    mock_session.assert_not_flushed()
    assert result is ConversationHandler.END
    context.api.assert_send_message_called(
        update,
        factory.main_menu_view(
            RenderContext(lang=user_with_settings.lang),
            message=CommonMessages.CONTEXT_LOST.rich(lang=user_with_settings.lang),
            counts=MeetingCounts(0, 0, 0),
        ),
    )


# ---------------------------------------------------------------------------
# PARTICIPANTS_MAXIMUM_MESSAGE — the guard stops a meeting the user does not own
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("update", [UpdateRequest(message_text="5")], indirect=True)
async def test_edit_max_participants_message_stops_when_meeting_not_owned(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """A meeting owned by somebody else stops the handler before any state is returned."""
    not_owned_meeting = create_meetup(id=99, title="Not Owned", owner=create_member(id=2, tg_user_id=456))
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(not_owned_meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_MAX_PARTICIPANTS: 99},
    )

    assert state == ConversationHandler.END
    # Meeting not owned — redirected to main menu. A message update has no message of ours to
    # replace, so the redirect arrives as a fresh reply.
    context.api.assert_send_message_called(update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang)))
    context.api.assert_edit_message_not_called()


# ---------------------------------------------------------------------------
# PARTICIPANTS_MAXIMUM_WRONG_MESSAGE — the guard stops a meeting the user does not own
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("update", [UpdateRequest(message_text="not a number")], indirect=True)
async def test_edit_wrong_max_participants_stops_when_meeting_not_owned(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """A meeting owned by somebody else stops the wrong-input handler too."""
    not_owned_meeting = create_meetup(id=99, title="Not Owned", owner=create_member(id=2, tg_user_id=456))
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(not_owned_meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_WRONG_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_MAX_PARTICIPANTS: 99},
    )

    assert state == ConversationHandler.END
    # Meeting not owned — redirected to main menu, as a fresh reply to the message update.
    context.api.assert_send_message_called(update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang)))
    context.api.assert_edit_message_not_called()


# ---------------------------------------------------------------------------
# PARTICIPANTS_MAXIMUM_MESSAGE — ContextPropertyNotSetError path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="5")],
    indirect=True,
)
async def test_edit_meeting_max_participants_message_sends_main_menu_when_context_missing(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """When no meeting_id is stored in context, edit_meeting_max_participants catches
    ContextPropertyNotSetError, sends the main menu view as a new message, and ends the conversation.

    The loss is an expected state, so it is logged as a warning and counted on the ContextLost
    series the central error handler feeds — not on the fault series."""
    user, meeting = owner_with_meeting(meeting_id=1)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    # Do NOT pass with_meeting_id so ContextPropertyNotSetError is raised.
    with capture_logs() as logs:
        context, state = await call_handler(
            EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_MESSAGE,
            handler_context=handler_context,
        )
    assert_context_lost_logged(logs, ContextId.EDIT_MEETING_MAX_PARTICIPANTS)
    metrics.assert_emitted(name=MetricKey.CONTEXT_LOST, value=1, times=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)

    assert state == ConversationHandler.END
    context.api.assert_send_message_called(
        update,
        factory.main_menu_view(
            RenderContext(lang=user.lang),
            message=CommonMessages.CONTEXT_LOST.rich(lang=user.lang),
            counts=MeetingCounts(0, 0, 0),
        ),
    )


# ---------------------------------------------------------------------------
# PARTICIPANTS_MAXIMUM_WRONG_MESSAGE — ContextPropertyNotSetError path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="not a number")],
    indirect=True,
)
async def test_edit_meeting_wrong_max_participants_message_sends_main_menu_when_context_missing(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """When no meeting_id is stored in context, edit_meeting_wrong_max_participants catches
    ContextPropertyNotSetError, sends the main menu view as a new message, and ends the conversation."""
    user, meeting = owner_with_meeting(meeting_id=1)
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting)

    # Do NOT pass with_meeting_id so ContextPropertyNotSetError is raised.
    with capture_logs() as logs:
        context, state = await call_handler(
            EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_WRONG_MESSAGE,
            handler_context=handler_context,
        )
    assert_context_lost_logged(logs, ContextId.EDIT_MEETING_MAX_PARTICIPANTS)
    metrics.assert_emitted(name=MetricKey.CONTEXT_LOST, value=1, times=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)

    assert state == ConversationHandler.END
    context.api.assert_send_message_called(
        update,
        factory.main_menu_view(
            RenderContext(lang=user.lang),
            message=CommonMessages.CONTEXT_LOST.rich(lang=user.lang),
            counts=MeetingCounts(0, 0, 0),
        ),
    )


def test_edit_max_participants_view_asks_only_for_the_number(user_with_settings: User):
    """Removing a limit lives on the editor card, so the prompt carries no shortcut button."""
    meeting = user_with_settings.meetups[0]

    view = edit_max_participants_view(meeting)

    assert view.message == MeetingEditParticipantsMessages.LIMIT_PROMPT.rich(lang=meeting.lang)
    assert view.menu == [
        [
            ButtonConfig(
                text=ButtonMessages.CANCEL.text(lang=meeting.lang),
                callback_data=cb.CANCEL_EDIT_MEETING_PARTICIPANS.with_id(meeting.db_id),
            )
        ]
    ]


# ---------------------------------------------------------------------------
# Remove limit: direct clear for uncapped owners, confirmation for capped ones
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.DELETE_MEETING_LIMIT.with_id(1))], indirect=True)
async def test_remove_limit_uncapped_owner_clears_it_outright(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    user_with_settings.supporter_level = supporter.SupporterLevel.HOST_2
    mock_session.add_object(user_with_settings, "tg_user_id")
    meeting = user_with_settings.meetups[0]
    meeting.max_members = 10
    mock_session.add_object(meeting)

    context, _ = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_REMOVE_LIMIT_CALLBACK, handler_context=handler_context
    )

    assert meeting.max_members is None
    context.api.assert_edit_message_called(update, meeting_views.owner_view(meeting))
    context.api.assert_update_meeting_messages_called(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.DELETE_MEETING_LIMIT.with_id(1))], indirect=True)
async def test_remove_limit_capped_owner_gets_the_cap_confirmation(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    monkeypatch: pytest.MonkeyPatch,
):
    """The removed limit resolves to the plan's cap, so the dialog states that number and carries
    the Collaborate button inline where the upsell mentions it."""
    monkeypatch.setattr(supporter.PolicyState, "config", LimitsConfig(free_participant_capacity=20))
    mock_session.add_object(user_with_settings, "tg_user_id")
    meeting = user_with_settings.meetups[0]
    meeting.max_members = 10
    mock_session.add_object(meeting)

    context, _ = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_REMOVE_LIMIT_CALLBACK, handler_context=handler_context
    )

    assert meeting.max_members == 10
    context.api.assert_edit_message_called(
        update,
        factory.confirmation_view(
            RenderContext(lang=user_with_settings.lang),
            message=MeetingEditParticipantsMessages.REMOVE_LIMIT_CONFIRMATION.rich(
                lang=user_with_settings.lang, cap=20, button_collaborate=collaborate_button(user_with_settings.lang)
            ),
            confirm_callback_data=cb.CONFIRM_DELETE_MEETING_LIMIT.with_id(1),
            decline_callback_data=cb.DECLINE_DELETE_MEETING_LIMIT.with_id(1),
        ),
    )


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.CONFIRM_DELETE_MEETING_LIMIT.with_id(1))], indirect=True
)
async def test_confirm_remove_limit_clears_it_and_returns_to_the_editor(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    meeting = user_with_settings.meetups[0]
    meeting.max_members = 10
    mock_session.add_object(meeting)

    context, _ = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_REMOVE_LIMIT_CONFIRM_CALLBACK, handler_context=handler_context
    )

    assert meeting.max_members is None
    context.api.assert_edit_message_called(update, meeting_views.owner_view(meeting))
    context.api.assert_update_meeting_messages_called(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.DECLINE_DELETE_MEETING_LIMIT.with_id(1))], indirect=True
)
async def test_decline_remove_limit_keeps_it(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    meeting = user_with_settings.meetups[0]
    meeting.max_members = 10
    mock_session.add_object(meeting)

    context, _ = await call_handler(
        EditMeetingHandlerId.PARTICIPANTS_REMOVE_LIMIT_DECLINE_CALLBACK, handler_context=handler_context
    )

    assert meeting.max_members == 10
    mock_session.assert_not_added()
    mock_session.assert_not_flushed()
    context.api.assert_edit_message_called(update, meeting_views.owner_view(meeting))
