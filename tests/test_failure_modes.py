"""
This module contains tests for common failure modes for all handlers. The intention is that we remove the need of
repeating the same kind of tests for every hanlder that should behave the same.

We just need to update the factory methods that produces the parameters for each test case.
"""

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum

import pytest
from aws_embedded_metrics.unit import Unit
from telegram import Update

from mitup_bot.callback_id import CallbackId
from mitup_bot.custom_context import ContextId
from mitup_bot.exceptions import ContextPropertyNotSetError, MalformedCallbackData, UserNotFound
from mitup_bot.handlers.edit_meeting.enums import EditMeetingHandlerId
from mitup_bot.handlers.edit_settings.enums import EditSettingsHandlerId
from mitup_bot.models import User
from mitup_bot.monitoring import MetricKey
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages
from mitup_bot.views import ButtonConfig, Keyboard, MitupView, factory
from tests.helpers import AnyFloat, MockApi, StubMitupApp, UpdateRequest, call_handler, create_meetup
from tests.helpers.stub_db import MockDbSession


class ErrorMode(Enum):
    """
    Enum to define the different error modes that can be tested
    """

    MEETING_NOT_OWNED = "MeetingNotOwned"
    USER_NOT_FOUND = "UserNotFound"
    MEETING_NOT_FOUND = "MeetingNotFound"
    MALFORMED_CALLBACK_DATA = "MalformedCallbackData"
    MISSING_USER_DATA = "MissingUserData"


@dataclass
class MetricsProperties:
    metrics: list[str] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    units: list[Unit] = field(default_factory=list)


@dataclass
class Context:
    callback_id: CallbackId
    update_request: UpdateRequest
    id: str
    error_modes: set[ErrorMode]
    user_fixture: str = "user_with_settings"
    exception: Exception | None = None
    fault_count: int = 0  # This is the value of the fault metric (both with and without prefix)
    custom_keyboard: Keyboard | None = None  # Used when the meeting does not exist and the message is edited
    meeting_id: dict[ContextId, int] | None = None  # Meeting id to store in the context data
    metrics_emitted: MetricsProperties = field(default_factory=MetricsProperties)
    metrics_properties: dict[str, str] | None = None


CONTEXTS = [
    Context(
        callback_id=EditMeetingHandlerId.DATE_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_DATE.with_id(99).with_date(dt.date(2024, 12, 21))),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="edit_meeting_date",
    ),
    Context(
        callback_id=EditMeetingHandlerId.SET_DATE_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_MEETING_DATE.with_id(99).with_date(dt.date(2024, 12, 21))),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="set_meeting_date",
    ),
    Context(
        callback_id=EditMeetingHandlerId.EDIT_TIME_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_TIME.with_id(99)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="edit_meeting_time",
    ),
    Context(
        callback_id=EditMeetingHandlerId.DELETE_DATE_TIME_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.DELETE_MEETING_DATE.with_id(99)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="delete_meeting_datetime",
    ),
    Context(
        callback_id=EditMeetingHandlerId.SET_TIME_MESSAGE,
        update_request=UpdateRequest(message_text="12:00"),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="set_meeting_time_message",
        metrics_emitted=MetricsProperties(metrics=["CleanUserData"], values=[1], units=[Unit.COUNT]),
        metrics_properties={"ContextId": ContextId.EDIT_MEETING_TIME.value},
        meeting_id={ContextId.EDIT_MEETING_TIME: 99},
    ),
    Context(
        callback_id=EditMeetingHandlerId.WRONG_TIME_FORMAT,
        update_request=UpdateRequest(message_text="12:00"),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="wrong_time_format",
    ),
    Context(
        callback_id=EditSettingsHandlerId.LANGUAGE_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_LANGUAGE),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="user_not_found_edit_language",
    ),
    Context(
        callback_id=EditMeetingHandlerId.SET_TIME_MESSAGE,
        update_request=UpdateRequest(message_text="12:00"),
        error_modes={ErrorMode.MISSING_USER_DATA},
        metrics_properties={"ContextId": ContextId.EDIT_MEETING_TIME.value},
        id="set_meeting_time_message",
    ),
    Context(
        callback_id=EditMeetingHandlerId.DATE_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_DATE.with_date(dt.date(2024, 12, 21))),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="edit_meeting_date",
    ),
    Context(
        callback_id=EditMeetingHandlerId.SET_DATE_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_MEETING_DATE.with_date(dt.date(2024, 12, 21))),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="set_meeting_date",
    ),
    Context(
        callback_id=EditMeetingHandlerId.EDIT_TIME_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_TIME),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="edit_meeting_time",
    ),
    Context(
        callback_id=EditMeetingHandlerId.DELETE_DATE_TIME_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.DELETE_MEETING_DATE),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="delete_meeting_datetime",
    ),
    Context(
        callback_id=EditMeetingHandlerId.MEETING_SETTINGS_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_SETTINGS.with_id(99)),
        error_modes={ErrorMode.MEETING_NOT_OWNED},
        id="edit_meeting_settings",
    ),
    Context(
        callback_id=EditMeetingHandlerId.SET_MEETING_WAITING_LIST_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_MEETING_WAITING_LIST.with_id(99)),
        error_modes={ErrorMode.MEETING_NOT_OWNED},
        id="set_meeting_waiting_list",
    ),
    Context(
        callback_id=EditMeetingHandlerId.SET_MEETING_PUBLIC_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_MEETING_PUBLIC.with_id(99)),
        error_modes={ErrorMode.MEETING_NOT_OWNED},
        id="set_meeting_public",
    ),
    Context(
        callback_id=EditMeetingHandlerId.SET_MEETING_INCOGNITO_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_MEETING_INCOGNITO.with_id(99)),
        error_modes={ErrorMode.MEETING_NOT_OWNED},
        id="set_meeting_incognito",
    ),
    Context(
        callback_id=EditMeetingHandlerId.SET_MEETING_ALLOW_INVITATIONS_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_MEETING_ALLOW_INVITATIONS.with_id(99)),
        error_modes={ErrorMode.MEETING_NOT_OWNED},
        id="set_meeting_allow_invitations",
    ),
    Context(
        callback_id=EditMeetingHandlerId.SET_MEETING_SHOW_TIMEZONE_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_MEETING_SHOW_TIMEZONE.with_id(99)),
        error_modes={ErrorMode.MEETING_NOT_OWNED},
        id="set_meeting_show_timezone",
    ),
]


# -------------------
# Factory methods
# -------------------
def handler_stop_for_accessing_meeting_not_owned_factory() -> list[Context]:
    """
    This factory should return a list of Context object for test cases where the handler should fail because the
    meeting is not accessible. This is, the guards.meeting_accessible returns None.
    Also used for testing when:
    - user is not found
    - meeting is not found
    """
    return [context for context in CONTEXTS if ErrorMode.MEETING_NOT_OWNED in context.error_modes]


def handler_stops_when_user_not_found() -> list[Context]:
    # We can use the same as for meeting not owned because the user needs to
    # be checked before the meeting ownership
    return [context for context in CONTEXTS if ErrorMode.USER_NOT_FOUND in context.error_modes]


def handler_stops_when_meeting_not_found() -> list[Context]:
    # We can use the same as for meeting not owned we with teh same context we just need
    # not to register the meeting
    return [context for context in CONTEXTS if ErrorMode.MEETING_NOT_FOUND in context.error_modes]


def handler_stopes_due_to_missing_user_data() -> list[Context]:
    return [context for context in CONTEXTS if ErrorMode.MISSING_USER_DATA in context.error_modes]


def handler_stops_due_to_malformed_callback_data() -> list[Context]:
    """
    Provides context with callback data that would fail when being parsed
    """
    return [context for context in CONTEXTS if ErrorMode.MALFORMED_CALLBACK_DATA in context.error_modes]


@pytest.mark.parametrize(
    "test_context, update",
    [
        pytest.param(context, context.update_request, id=context.id)
        for context in handler_stop_for_accessing_meeting_not_owned_factory()
    ],
    indirect=["update"],
)
async def test_callback_fails_when_meeting_not_accessible(
    mock_session: MockDbSession,
    test_context: Context,
    update: Update,
    app: StubMitupApp,
    user_with_settings: User,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(create_meetup(id=99))

    with MockApi.start("mitup_bot.guards") as api:
        context, _ = await call_handler(update, app, test_context.callback_id, test_context.meeting_id)

    # This does not raise an exception but logs an error
    metric_names = test_context.metrics_emitted.metrics + [
        MetricKey.ERROR.with_prefix(MetricKey.MEETING_NOT_OWNED),
        MetricKey.FAULT,
        MetricKey.TIME,
    ]
    metric_values = test_context.metrics_emitted.values + [1, 0, AnyFloat()]
    metric_units = test_context.metrics_emitted.units + [Unit.COUNT, Unit.COUNT, Unit.MILLISECONDS]

    context.metrics_engine.assert_metrics_emited(
        names=metric_names,
        values=metric_values,
        units=metric_units,
        properties=test_context.metrics_properties,
        add_handler_dimensions=True,
        add_update_properties=True,
    )
    # The user is sent to the main menu
    api.assert_edit_message_called(context, update, factory.main_menu_view(lang=user_with_settings.lang))


@pytest.mark.parametrize(
    "test_context, update",
    [
        pytest.param(context, context.update_request, id=context.id)
        for context in handler_stop_for_accessing_meeting_not_owned_factory()
    ],
    indirect=["update"],
)
async def test_callback_fails_when_meeting_not_found(
    mock_session: MockDbSession,
    test_context: Context,
    update: Update,
    app: StubMitupApp,
    user_with_settings: User,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    with MockApi.start("mitup_bot.guards") as api:
        context, _ = await call_handler(update, app, test_context.callback_id, test_context.meeting_id)

    # This does not raise an exception but logs an error
    metric_names = test_context.metrics_emitted.metrics + [MetricKey.FAULT, MetricKey.TIME]
    metric_values = test_context.metrics_emitted.values + [0, AnyFloat()]
    metric_units = test_context.metrics_emitted.units + [Unit.COUNT, Unit.MILLISECONDS]

    context.metrics_engine.assert_metrics_emited(
        names=metric_names,
        values=metric_values,
        units=metric_units,
        properties=test_context.metrics_properties,
        add_handler_dimensions=True,
        add_update_properties=True,
    )
    # The user is sent to the main menu
    keyboard = test_context.custom_keyboard or [
        [
            ButtonConfig(
                text=f"{ButtonMessages.GO_BACK}{ButtonMessages.MAIN_MENU.get(lang=user_with_settings.lang)}",
                callback_data=cb.MAIN_MENU,
            )
        ]
    ]
    api.assert_edit_message_called(
        context,
        update,
        MitupView(
            description=MeetingMessages.ACCESS_TO_DELETED_MEETING.get(lang=user_with_settings.lang),
            keyboard=keyboard,
        ),
    )


@pytest.mark.parametrize(
    "test_context, update",
    [
        pytest.param(context, context.update_request, id=context.id)
        for context in handler_stops_due_to_malformed_callback_data()
    ],
    indirect=["update"],
)
async def test_callback_fails_with_malformed_callback_data(
    mock_session: MockDbSession,
    test_context: Context,
    update: Update,
    app: StubMitupApp,
):
    context, _ = await call_handler(update, app, test_context.callback_id, test_context.meeting_id)

    # This does not raise an exception but logs an error
    metric_names = test_context.metrics_emitted.metrics + [
        MetricKey.FAULT.with_prefix("MalformedCallbackData"),
        MetricKey.FAULT,
        MetricKey.TIME,
    ]
    metric_values = test_context.metrics_emitted.values + [1, 1, AnyFloat()]
    metric_units = test_context.metrics_emitted.units + [Unit.COUNT, Unit.COUNT, Unit.MILLISECONDS]

    context.metrics_engine.assert_metrics_emited(
        names=metric_names,
        values=metric_values,
        units=metric_units,
        properties=test_context.metrics_properties,
        exception=MalformedCallbackData,
        add_handler_dimensions=True,
        add_update_properties=True,
    )


@pytest.mark.parametrize(
    "test_context, update",
    [pytest.param(context, context.update_request, id=context.id) for context in handler_stops_when_user_not_found()],
    indirect=["update"],
)
async def test_callback_fails_when_user_is_not_found(
    mock_session: MockDbSession,
    test_context: Context,
    update: Update,
    app: StubMitupApp,
):
    # Do not register the user in the db and call the handler
    context, _ = await call_handler(update, app, test_context.callback_id, test_context.meeting_id)

    # This does not raise an exception but logs an error
    metric_names = test_context.metrics_emitted.metrics + [
        MetricKey.FAULT.with_prefix("UserNotFound"),
        MetricKey.FAULT,
        MetricKey.TIME,
    ]
    metric_values = test_context.metrics_emitted.values + [1, 1, AnyFloat()]
    metric_units = test_context.metrics_emitted.units + [Unit.COUNT, Unit.COUNT, Unit.MILLISECONDS]

    context.metrics_engine.assert_metrics_emited(
        names=metric_names,
        values=metric_values,
        units=metric_units,
        properties=test_context.metrics_properties,
        exception=UserNotFound,
        add_handler_dimensions=True,
        add_update_properties=True,
    )


@pytest.mark.parametrize(
    "test_context, update",
    [
        pytest.param(context, context.update_request, id=context.id)
        for context in handler_stopes_due_to_missing_user_data()
    ],
    indirect=["update"],
)
async def test_callback_fails_when_missing_necessary_user_data(
    mock_session: MockDbSession,
    test_context: Context,
    update: Update,
    app: StubMitupApp,
):
    # If context data is needed it should be validated before having to hit the database.
    # The fault should happen before testing if any object exists in the db and, therefore,
    # there is no need to add any.
    # If this test fails because the an object is not found in the database, it means that the
    # validation is not happening in the right place and the callback needs to be updated.
    context, _ = await call_handler(update, app, test_context.callback_id, test_context.meeting_id)

    # This does not raise an exception but logs an error
    metric_names = test_context.metrics_emitted.metrics + [
        MetricKey.FAULT.with_prefix("ContextPropertyNotSetError"),
        MetricKey.FAULT,
        MetricKey.TIME,
    ]
    metric_values = test_context.metrics_emitted.values + [1, 1, AnyFloat()]
    metric_units = test_context.metrics_emitted.units + [Unit.COUNT, Unit.COUNT, Unit.MILLISECONDS]

    context.metrics_engine.assert_metrics_emited(
        names=metric_names,
        values=metric_values,
        units=metric_units,
        properties=test_context.metrics_properties,
        exception=ContextPropertyNotSetError,
        add_handler_dimensions=True,
        add_update_properties=True,
    )
