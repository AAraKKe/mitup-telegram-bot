import pytest
from telegram import Update

from mitup_bot.handlers.edit_settings.enums import EditSettingsHandlerId
from mitup_bot.models import Settings, User
from mitup_bot.utils import callbacks as cb
from tests.helpers import MockApi, MockDbSession, StubMitupApp, UpdateRequest, call_handler


@pytest.fixture
def api():
    with MockApi.start("mitup_bot.handlers.edit_settings.edit_default_options") as api:
        yield api


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.EDIT_DEFAULT_OPTIONS)], indirect=True)
async def test_edit_default_options_view(
    mock_session: MockDbSession, user_with_settings: User, update: Update, app: StubMitupApp, api: MockApi
):
    settings = user_with_settings.settings
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    expected_view = settings.default_meeting_settings_view()

    contex, _ = await call_handler(update, app, EditSettingsHandlerId.DEFAULT_OPTIONS_CALLBACK)

    api.assert_edit_message_called(contex, update, expected_view)


def assert_default_options_value(
    settings: Settings,
    handler_id: EditSettingsHandlerId,
    waiting_list: bool,
    public: bool,
    invitation: bool,
    incognito: bool,
    show_timezone: bool,
):
    expected_waiting_list = (
        not waiting_list if handler_id is EditSettingsHandlerId.SET_DEFAULT_WAITING_LIST else waiting_list
    )
    expected_public = not public if handler_id is EditSettingsHandlerId.SET_DEFAULT_PUBLIC else public
    expected_invitation = not invitation if handler_id is EditSettingsHandlerId.SET_DEFAULT_INVITATIONS else invitation
    expected_incognito = not incognito if handler_id is EditSettingsHandlerId.SET_DEFAULT_INCOGNITO else incognito
    expected_show_timezone = (
        not show_timezone if handler_id is EditSettingsHandlerId.SET_DEFAULT_SHOW_TIMEZONE else show_timezone
    )

    assert settings.default_waiting_list == expected_waiting_list
    assert settings.default_public == expected_public
    assert settings.default_allow_invitation == expected_invitation
    assert settings.default_incognito == expected_incognito
    assert settings.default_show_timezone == expected_show_timezone


@pytest.mark.parametrize(
    "update,handler_id",
    [
        (UpdateRequest(callback_query=cb.SET_DEFAULT_WAITING_LIST), EditSettingsHandlerId.SET_DEFAULT_WAITING_LIST),
        (UpdateRequest(callback_query=cb.SET_DEFAULT_PUBLIC), EditSettingsHandlerId.SET_DEFAULT_PUBLIC),
        (UpdateRequest(callback_query=cb.SET_DEFAULT_INVITATIONS), EditSettingsHandlerId.SET_DEFAULT_INVITATIONS),
        (UpdateRequest(callback_query=cb.SET_DEFAULT_INCOGNITO), EditSettingsHandlerId.SET_DEFAULT_INCOGNITO),
        (UpdateRequest(callback_query=cb.SET_DEFAULT_SHOW_TIMEZONE), EditSettingsHandlerId.SET_DEFAULT_SHOW_TIMEZONE),
    ],
    ids=["waiting_list", "public", "invitation", "incognito", "show_timezone"],
    indirect=["update"],
)
@pytest.mark.parametrize("waiting_list", [True, False], ids=["waiting_list_true", "waiting_list_false"])
@pytest.mark.parametrize("public", [True, False], ids=["public_true", "public_false"])
@pytest.mark.parametrize("invitation", [True, False], ids=["invitation_true", "invitation_false"])
@pytest.mark.parametrize("incognito", [True, False], ids=["incognito_true", "incognito_false"])
@pytest.mark.parametrize("show_timezone", [True, False], ids=["show_timezone_true", "show_timezone_false"])
async def test_callbacks_to_set_default_option(
    mock_session: MockDbSession,
    user_with_settings: User,
    update: Update,
    handler_id: EditSettingsHandlerId,
    waiting_list: bool,
    public: bool,
    invitation: bool,
    incognito: bool,
    show_timezone: bool,
    app: StubMitupApp,
    api: MockApi,
):
    settings = user_with_settings.settings
    settings.default_allow_invitation = invitation
    settings.default_incognito = incognito
    settings.default_public = public
    settings.default_show_timezone = show_timezone
    settings.default_waiting_list = waiting_list

    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    contex, _ = await call_handler(update, app, handler_id)

    expected_view = settings.default_meeting_settings_view()

    api.assert_edit_message_called(contex, update, expected_view)
    assert_default_options_value(settings, handler_id, waiting_list, public, invitation, incognito, show_timezone)
