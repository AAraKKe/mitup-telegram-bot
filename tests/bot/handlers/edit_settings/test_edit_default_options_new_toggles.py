from collections.abc import Callable

import pytest
from telegram import Update

from mitup_bot.datetimes import DateFormat
from mitup_bot.handlers.edit_settings.enums import EditSettingsHandlerId
from mitup_bot.models import Settings, User
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import MitupView
from mitup_bot.views.meeting_settings import default_behavior_view, default_time_format_view
from tests.helpers import HandlerContext, MockDbSession, UpdateRequest, call_handler


@pytest.mark.parametrize(
    "update, handler_id, attr_name, sub_card",
    [
        (
            UpdateRequest(callback_query=cb.SET_DEFAULT_LOCK_ON_START),
            EditSettingsHandlerId.SET_DEFAULT_LOCK_ON_START,
            "default_lock_on_start",
            default_behavior_view,
        ),
        (
            UpdateRequest(callback_query=cb.SET_DEFAULT_SHOW_TIMEZONE),
            EditSettingsHandlerId.SET_DEFAULT_SHOW_TIMEZONE,
            "default_show_timezone",
            default_time_format_view,
        ),
        (
            UpdateRequest(callback_query=cb.SET_DEFAULT_CLOCK_24H),
            EditSettingsHandlerId.SET_DEFAULT_CLOCK_24H,
            "default_clock_24h",
            default_time_format_view,
        ),
    ],
    ids=["lock_on_start", "show_timezone", "clock_24h"],
    indirect=["update"],
)
@pytest.mark.parametrize("initial_value", [True, False], ids=["initially_true", "initially_false"])
async def test_toggle_flips_value_and_redraws_the_sub_card_it_lives_on(
    mock_session: MockDbSession,
    user_with_settings: User,
    update: Update,
    handler_id: EditSettingsHandlerId,
    attr_name: str,
    sub_card: Callable[[Settings], MitupView],
    initial_value: bool,
    handler_context: HandlerContext,
):
    settings = user_with_settings.settings
    setattr(settings, attr_name, initial_value)
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, _ = await call_handler(handler_id, handler_context=handler_context)

    assert getattr(settings, attr_name) == (not initial_value)

    context.api.assert_edit_message_called(update, sub_card(settings))


@pytest.mark.parametrize(
    "update,picked",
    [
        (UpdateRequest(callback_query=cb.SET_DEFAULT_DATE_FORMAT.with_id(index)), date_format)
        for index, date_format in enumerate(DateFormat)
    ],
    ids=[date_format.value for date_format in DateFormat],
    indirect=["update"],
)
async def test_setting_the_default_date_format_stores_the_picked_one(
    mock_session: MockDbSession,
    user_with_settings: User,
    update: Update,
    picked: DateFormat,
    handler_context: HandlerContext,
):
    settings = user_with_settings.settings
    settings.default_date_format = DateFormat.DEFAULT
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, _ = await call_handler(EditSettingsHandlerId.SET_DEFAULT_DATE_FORMAT, handler_context=handler_context)

    assert settings.default_date_format is picked
    context.api.assert_edit_message_called(update, default_time_format_view(settings))
