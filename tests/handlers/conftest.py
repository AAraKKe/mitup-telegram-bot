import pytest

from mitup_bot.handlers.callback_query import (
    callback_query_cancel_settings,
    callback_query_main_menu,
    callback_query_settings,
    callback_query_timezone,
)
from mitup_bot.handlers.commands import command_cancel, command_start_with_existing_user, command_start_with_new_user
from mitup_bot.handlers.messages import registration_timezone_message_handler, settings_timezone_message_handler


@pytest.fixture(params=[command_start_with_existing_user, command_start_with_new_user, command_cancel])
def command_list(request):
    return request.param


@pytest.fixture(
    params=[callback_query_settings, callback_query_timezone, callback_query_cancel_settings, callback_query_main_menu]
)
def callback_query_list(request):
    return request.param


@pytest.fixture(params=[registration_timezone_message_handler, settings_timezone_message_handler])
def message_list(request):
    return request.param
