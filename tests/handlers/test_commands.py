from collections.abc import Awaitable, Callable
from typing import cast
from unittest import mock

import pytest
from telegram import Update
from telegram.ext import CommandHandler, ConversationHandler
from telegram.ext.filters import CAPTION, PHOTO

from mitup_bot.exceptions import (
    EffectiveUserNotSet,
    HandlerNotRegistered,
    HandlerRegisteredError,
    WrongCommandNameError,
)
from mitup_bot.handler_id import HandlerId
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.handlers.commands import CommandsId, command_go_to_main_menu, command_start_with_existing_user
from mitup_bot.handlers.meeting.enums import ConversationMeetingState
from mitup_bot.handlers.registration_process.edit_registration_timezone import command_start_with_new_user
from mitup_bot.handlers.registration_process.enums import (
    ConversationRegistrationProcessState,
    RegistrationProcessHandlerId,
)
from mitup_bot.models import User
from mitup_bot.monitoring import Feature, MetricKey, MetricsClient, NullBackend
from mitup_bot.utils import SettingsMessages
from mitup_bot.views.factory import create_meeting_view, main_menu_view
from tests.helpers import (
    HandlerContext,
    MockApi,
    StubMitupContext,
    UpdateRequest,
    call_handler,
)
from tests.helpers.monitoring import MetricAssertions
from tests.helpers.stub_db import MockDbSession


class CommandsTestId(HandlerId):
    COMMAND_EXAMPLE = "my_command"
    COMMAND_CUSTOM_NAME = "the_custom_command_name"
    COMMAND_WITH_FILTERS = "with_filters"
    COMMAND_WRONG_NAME = "wrong_command_name"
    COMMAND_EXIST_NAME = "existing_command"
    COMMAND_CUSTOM_NAME_2 = "some custom command"
    COMMAND_CUSTOM_NAME_3 = "new_custom_command"
    COMMAND_NOT_REGISTERED = "not_registered"


async def test_command_registry_can_register_command_handlers(update: Update):
    @HandlersRegistry.register_command(CommandsTestId.COMMAND_EXAMPLE)
    async def command_my_command(update: Update, context: StubMitupContext):
        return "Done!"

    assert CommandsTestId.COMMAND_EXAMPLE in HandlersRegistry.handlers
    handler = HandlersRegistry.get_handler(CommandsTestId.COMMAND_EXAMPLE)
    assert isinstance(handler, CommandHandler)
    assert handler.has_args is None
    assert "my_command" in handler.commands

    callback_return = await handler.callback(
        update,
        StubMitupContext(mock.MagicMock(), update, MetricsClient(NullBackend()), MockApi()),
    )
    assert callback_return == "Done!"


async def test_register_command_with_custom_name():
    @HandlersRegistry.register_command(CommandsTestId.COMMAND_CUSTOM_NAME, command="my_custom_command")
    async def command_my_command(update: Update, context: StubMitupContext):
        return "Done!"

    assert CommandsTestId.COMMAND_CUSTOM_NAME in HandlersRegistry.handlers


async def test_register_command_with_filters():
    @HandlersRegistry.register_command(CommandsTestId.COMMAND_WITH_FILTERS, filters=CAPTION & PHOTO)
    async def command_with_filters(update: Update, context: StubMitupContext):
        return "Done!"

    assert CommandsTestId.COMMAND_WITH_FILTERS in HandlersRegistry.handlers


def test_registry_raises_if_command_name_is_not_correct():
    # Should raise error if we want to register a CommandHandler with a callback name
    # that does not start with `command_`
    with pytest.raises(WrongCommandNameError):

        @HandlersRegistry.register_command(CommandsTestId.COMMAND_WRONG_NAME)
        async def my_command(update: Update, context: StubMitupContext):
            return "Done!"


def test_registry_raises_if_hander_already_registered():
    @HandlersRegistry.register_command(CommandsTestId.COMMAND_EXIST_NAME)
    async def command_my_new_command(update: Update, context: StubMitupContext):
        return "Done!"

    with pytest.raises(HandlerRegisteredError):

        @HandlersRegistry.register_command(CommandsTestId.COMMAND_EXIST_NAME)
        async def command_existing_command(update: Update, context: StubMitupContext):
            return "Done!"


def test_multiple_commands_can_be_registered_with_different_names():
    @HandlersRegistry.register_command(CommandsTestId.COMMAND_CUSTOM_NAME_2)
    async def command_custom_new(update: Update, context: StubMitupContext):
        return "Done!"

    @HandlersRegistry.register_command(CommandsTestId.COMMAND_CUSTOM_NAME_3, command="custom_new", filters=CAPTION)
    async def command_another_custom_new(update: Update, context: StubMitupContext):
        return "Done!"

    assert CommandsTestId.COMMAND_CUSTOM_NAME_2 in HandlersRegistry.handlers
    assert CommandsTestId.COMMAND_CUSTOM_NAME_3 in HandlersRegistry.handlers

    # Check the command is the same in both
    custom_command_handler = cast(CommandHandler, HandlersRegistry.get_handler(CommandsTestId.COMMAND_CUSTOM_NAME_2))
    new_custom_command_handler = cast(
        CommandHandler, HandlersRegistry.get_handler(CommandsTestId.COMMAND_CUSTOM_NAME_3)
    )
    assert custom_command_handler.commands == new_custom_command_handler.commands


def test_registry_fails_to_get_handler_that_does_not_exist():
    with pytest.raises(HandlerNotRegistered):
        HandlersRegistry.get_handler(CommandsTestId.COMMAND_NOT_REGISTERED)


@pytest.mark.parametrize("update", ([UpdateRequest(command="start")]), indirect=True)
async def test_command_start_with_new_user(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
):
    context, result = await call_handler(RegistrationProcessHandlerId.TIMEZONE_COMMAND, handler_context=handler_context)

    assert update.effective_user is not None

    mock_session.assert_added()
    context.api.assert_send_message_called(
        update,
        SettingsMessages.SET_REGISTRATION_TIMEZONE.get(first_name=update.effective_user.first_name),
    )
    assert result == ConversationRegistrationProcessState.TIMEZONE

    metrics.assert_emitted(name=MetricKey.COUNT, value=1, dimensions={"Feature": str(Feature.NEW_LANDING)})


@pytest.mark.parametrize("update", ([UpdateRequest(user=False)]), indirect=True)
async def test_command_stat_with_new_user_use_incorrect_user(
    mock_session: MockDbSession, update: Update, context: StubMitupContext, metrics: MetricAssertions
):
    with pytest.raises(EffectiveUserNotSet):
        await command_start_with_new_user(update, context)

    metrics.assert_not_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.NEW_LANDING)})


@pytest.mark.parametrize("command", [command_start_with_existing_user, command_go_to_main_menu])
async def test_commands_to_show_main_menu(
    command: Callable[[Update, StubMitupContext], Awaitable[None]],
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    mock_session: MockDbSession,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    result = await command(update, context)

    expected_view = main_menu_view(lang=user_with_settings.lang)
    context.api.assert_send_message_called(update, expected_view)
    assert result == ConversationHandler.END


@pytest.mark.parametrize("update", [UpdateRequest(command="start", command_args="inline")], indirect=True)
async def test_start_inline_deep_link_sends_create_meeting_view(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    mock_session.add_user(user_with_settings)

    context, result = await call_handler(CommandsId.START_WITH_EXISTING_USER, handler_context=handler_context)

    expected_view = create_meeting_view(lang=user_with_settings.lang)
    context.api.assert_send_message_called(update, expected_view)
    assert result == ConversationMeetingState.TITLE
