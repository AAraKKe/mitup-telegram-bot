from typing import cast
from unittest import mock

import pytest
from telegram import Update
from telegram.ext import CommandHandler
from telegram.ext.filters import CAPTION, PHOTO

from mitup_bot.callback_id import CallbackId
from mitup_bot.custom_context import MitupContext
from mitup_bot.exceptions import (
    EffectiveUserNotSet,
    HandlerNotRegistered,
    HandlerRegisteredError,
    WrongCommandNameError,
)
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.handlers.commands import command_cancel, command_go_to_main_menu, command_start_with_existing_user
from mitup_bot.handlers.registration_process.edit_registration_timezone import command_start_with_new_user
from mitup_bot.handlers.registration_process.enums import (
    ConversationRegistrationProcessState,
    RegistrationProcessHandlerId,
)
from mitup_bot.monitoring import Feature, MetricKey, MitupMetricsEngine
from mitup_bot.utils import SettingsMessages
from mitup_bot.views.factory import main_menu_view
from tests.helpers import MockApi, StubMetrics, StubMitupApp, StubMitupContext, UpdateRequest, call_handler
from tests.helpers.stub_db import MockDbSession


@pytest.fixture
def api():
    with MockApi.start("mitup_bot.handlers.commands") as api:
        yield api


class CommandsTestId(CallbackId):
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
    async def command_my_command(update: Update, context: MitupContext):
        return "Done!"

    assert CommandsTestId.COMMAND_EXAMPLE in HandlersRegistry.handlers
    handler = HandlersRegistry.get_handler(CommandsTestId.COMMAND_EXAMPLE)
    assert isinstance(handler, CommandHandler)
    assert handler.has_args is None
    assert "my_command" in handler.commands

    callback_return = await handler.callback(
        update,
        MitupContext(mock.MagicMock(), update, MitupMetricsEngine(logger_provider=lambda ep: StubMetrics())),
    )
    assert callback_return == "Done!"


async def test_register_command_with_custom_name():
    @HandlersRegistry.register_command(CommandsTestId.COMMAND_CUSTOM_NAME, command="my_custom_command")
    async def command_my_command(update: Update, context: MitupContext):
        return "Done!"

    assert CommandsTestId.COMMAND_CUSTOM_NAME in HandlersRegistry.handlers


async def test_register_command_with_filters():
    @HandlersRegistry.register_command(CommandsTestId.COMMAND_WITH_FILTERS, filters=CAPTION & PHOTO)
    async def command_with_filters(update: Update, context: MitupContext):
        return "Done!"

    assert CommandsTestId.COMMAND_WITH_FILTERS in HandlersRegistry.handlers


def test_registry_raises_if_command_name_is_not_correct():
    # Should raise error if we want to register a CommandHandler with a callback name
    # that does not start with `command_`
    with pytest.raises(WrongCommandNameError):

        @HandlersRegistry.register_command(CommandsTestId.COMMAND_WRONG_NAME)
        async def my_command(update: Update, context: MitupContext):
            return "Done!"


def test_registry_raises_if_hander_already_registered():
    @HandlersRegistry.register_command(CommandsTestId.COMMAND_EXIST_NAME)
    async def command_my_new_command(update: Update, context: MitupContext):
        return "Done!"

    with pytest.raises(HandlerRegisteredError):

        @HandlersRegistry.register_command(CommandsTestId.COMMAND_EXIST_NAME)
        async def command_existing_command(update: Update, context: MitupContext):
            return "Done!"


def test_multiple_commands_can_be_registered_with_different_names():
    @HandlersRegistry.register_command(CommandsTestId.COMMAND_CUSTOM_NAME_2)
    async def command_custom_new(update: Update, context: MitupContext):
        return "Done!"

    @HandlersRegistry.register_command(CommandsTestId.COMMAND_CUSTOM_NAME_3, command="custom_new", filters=CAPTION)
    async def command_another_custom_new(update: Update, context: MitupContext):
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
    app: StubMitupApp,
    api: MockApi,
):
    context, result = await call_handler(update, app, RegistrationProcessHandlerId.TIMEZONE_COMMAND)

    assert update.effective_user is not None

    mock_session.assert_added()
    api.assert_send_message_called(
        context,
        update,
        SettingsMessages.SET_REGISTRATION_TIMEZONE.get(first_name=update.effective_user.first_name),
    )
    assert result == ConversationRegistrationProcessState.TIMEZONE

    context.metrics_engine.assert_metrics_emited(
        names=[MetricKey.COUNT],
        values=[1],
        dimensions={"Feature": Feature.NEW_LANDING},
        add_handler_dimensions=False,
    )


@pytest.mark.parametrize("update", ([UpdateRequest(user=False)]), indirect=True)
async def test_command_stat_with_new_user_use_incorrect_user(
    mock_session: MockDbSession, update: Update, context: StubMitupContext
):
    with pytest.raises(EffectiveUserNotSet):
        await command_start_with_new_user(update, context)

    context.metrics_engine.assert_metrics_not_emited(
        names=[MetricKey.COUNT], values=[1], dimensions={"Feature": Feature.NEW_LANDING}
    )


@pytest.mark.parametrize("command_list", [command_start_with_existing_user, command_go_to_main_menu])
async def test_commands_to_show_main_menu(command_list, update: Update, context: StubMitupContext, api: MockApi):
    await command_start_with_existing_user(update, context)

    expected_view = main_menu_view()
    api.assert_send_message_called(context, update, expected_view)


async def test_command_cancel(update: Update, context: StubMitupContext, api: MockApi):
    await command_cancel(update, context)

    expected_view = main_menu_view()
    api.assert_send_message_called(context, update, expected_view)
