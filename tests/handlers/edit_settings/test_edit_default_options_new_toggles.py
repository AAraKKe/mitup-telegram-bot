import pytest
from telegram import Update

from mitup_bot.handlers.edit_settings.enums import EditSettingsHandlerId
from mitup_bot.models import User
from mitup_bot.utils import callbacks as cb
from tests.helpers import HandlerContext, MockDbSession, UpdateRequest, call_handler


@pytest.mark.parametrize(
    "update, handler_id, attr_name",
    [
        (
            UpdateRequest(callback_query=cb.SET_DEFAULT_LOCK_ON_START),
            EditSettingsHandlerId.SET_DEFAULT_LOCK_ON_START,
            "default_lock_on_start",
        ),
    ],
    ids=["lock_on_start"],
    indirect=["update"],
)
@pytest.mark.parametrize("initial_value", [True, False], ids=["initially_true", "initially_false"])
async def test_toggle_flips_value_and_re_renders_default_meeting_settings(
    mock_session: MockDbSession,
    user_with_settings: User,
    update: Update,
    handler_id: EditSettingsHandlerId,
    attr_name: str,
    initial_value: bool,
    handler_context: HandlerContext,
):
    settings = user_with_settings.settings
    setattr(settings, attr_name, initial_value)
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, _ = await call_handler(handler_id, handler_context=handler_context)

    # The setting was toggled
    assert getattr(settings, attr_name) == (not initial_value)

    # After toggling lock, the default_meeting_settings_view() is re-rendered (not a sub-screen)
    expected_view = settings.default_meeting_settings_view()
    context.api.assert_edit_message_called(update, expected_view)
