import logging

import pytest
from telegram import Update

from mitup_bot.datetimes import DateFormat
from mitup_bot.handlers.edit_settings.enums import EditSettingsHandlerId, SettingName
from mitup_bot.handlers.edit_settings.utils import SETTING_CHANGED_EVENT
from mitup_bot.models import User
from mitup_bot.utils import callbacks as cb
from tests.helpers import HandlerContext, MockDbSession, UpdateRequest, call_handler, log_record


@pytest.mark.parametrize("update", [UpdateRequest(message_text="15")], indirect=True)
async def test_timeout_change_is_recorded_as_a_setting_change(
    mock_session: MockDbSession,
    user_with_settings: User,
    update: Update,
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    old_timeout = user_with_settings.settings.timeout

    await call_handler(EditSettingsHandlerId.TIMEOUT_MESSAGE_WITH_TEXT, handler_context=handler_context)

    record = log_record(caplog, SETTING_CHANGED_EVENT)
    assert record.__dict__["user_id"] == user_with_settings.db_id
    assert record.__dict__["setting"] == SettingName.TIMEOUT.value
    assert record.__dict__["old_value"] == old_timeout
    assert record.__dict__["new_value"] == 15


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.SET_DEFAULT_INCOGNITO)], indirect=True)
async def test_default_option_toggle_shares_the_setting_changed_event(
    mock_session: MockDbSession,
    user_with_settings: User,
    update: Update,
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
):
    """A second, unrelated mutation site writes the same event name under a different `setting`.

    That is what makes one query the whole change history rather than a per-screen scavenger hunt.
    """
    caplog.set_level(logging.INFO)
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    user_with_settings.settings.default_incognito = False

    await call_handler(EditSettingsHandlerId.SET_DEFAULT_INCOGNITO, handler_context=handler_context)

    record = log_record(caplog, SETTING_CHANGED_EVENT)
    assert record.__dict__["setting"] == SettingName.DEFAULT_INCOGNITO.value
    assert record.__dict__["old_value"] is False
    assert record.__dict__["new_value"] is True
    assert user_with_settings.settings.default_incognito is True


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.SET_DEFAULT_DATE_FORMAT.with_id(1))], indirect=True)
async def test_the_default_date_format_writes_the_same_event_from_its_own_call_site(
    mock_session: MockDbSession,
    user_with_settings: User,
    update: Update,
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
):
    """A pick rather than a flip, so it writes the shared event itself instead of going through the
    toggle helper; the facets have to line up all the same."""
    caplog.set_level(logging.INFO)
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    user_with_settings.settings.default_date_format = DateFormat.DEFAULT

    await call_handler(EditSettingsHandlerId.SET_DEFAULT_DATE_FORMAT, handler_context=handler_context)

    record = log_record(caplog, SETTING_CHANGED_EVENT)
    assert record.__dict__["setting"] == SettingName.DEFAULT_DATE_FORMAT.value
    assert record.__dict__["old_value"] == DateFormat.DEFAULT.value
    assert record.__dict__["new_value"] == DateFormat.LONG.value


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.TOGGLE_NOTIFICATIONS)], indirect=True)
async def test_the_notifications_switch_records_only_the_settings_it_changes(
    mock_session: MockDbSession,
    user_with_settings: User,
    update: Update,
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    user_with_settings.settings.notification = False

    await call_handler(EditSettingsHandlerId.TOGGLE_NOTIFICATIONS, handler_context=handler_context)

    recorded = [record.__dict__["setting"] for record in caplog.records if record.message == SETTING_CHANGED_EVENT]
    assert recorded == [SettingName.DELETION_WARNING.value, SettingName.DELETION_NOTICE.value]
