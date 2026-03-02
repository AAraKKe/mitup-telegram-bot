import logging

import pytest
from pytest import LogCaptureFixture

from mitup_bot.models import Settings, User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, SettingsMessages
from mitup_bot.views import MitupView
from mitup_bot.views.factory import options_button


def test_valid_timezone(settings: Settings):
    assert settings.tz.key == "Europe/Madrid"


def test_invalid_timezone(settings: Settings, caplog: LogCaptureFixture):
    settings.timezone = "Invalid/Timezone"
    with caplog.at_level(logging.WARNING):
        assert settings.tz.key == "UTC"
        assert "Invalid timezone" in caplog.records[0].message


def expected_default_meeting_options_view(
    settings: Settings,
) -> MitupView:
    lang = settings.language
    waiting_list = settings.default_waiting_list
    public = settings.default_public
    invitation = settings.default_allow_invitation
    incognito = settings.default_incognito

    message = SettingsMessages.DEFAULT_MEETING_OPTIONS_MESSAGE.get(lang=lang)
    waiting_list_button = options_button(
        cb.SET_DEFAULT_WAITING_LIST, ButtonMessages.WAITING_LIST.get(lang=lang), waiting_list
    )
    public_button = options_button(cb.SET_DEFAULT_PUBLIC, ButtonMessages.PUBLIC.get(lang=lang), public)
    invitation_button = options_button(
        cb.SET_DEFAULT_INVITATIONS, ButtonMessages.OPEN_INVITATION.get(lang=lang), invitation
    )
    incognito_button = options_button(cb.SET_DEFAULT_INCOGNITO, ButtonMessages.INCOGNITO.get(lang=lang), incognito)

    return MitupView(
        message,
        keyboard=[
            [waiting_list_button, public_button],
            [invitation_button, incognito_button],
        ],
    ).with_back_button(text=ButtonMessages.SETTINGS, callback_data=cb.SETTINGS, lang=lang)


@pytest.mark.parametrize("waiting_list", [True, False], ids=["waiting_list_true", "waiting_list_false"])
@pytest.mark.parametrize("public", [True, False], ids=["public_true", "public_false"])
@pytest.mark.parametrize("invitation", [True, False], ids=["invitation_true", "invitation_false"])
@pytest.mark.parametrize("incognito", [True, False], ids=["incognito_true", "incognito_false"])
def test_default_meeting_options_view(
    waiting_list: bool,
    public: bool,
    invitation: bool,
    incognito: bool,
    user_with_settings: User,
):
    settings = user_with_settings.settings
    settings.default_allow_invitation = invitation
    settings.default_incognito = incognito
    settings.default_public = public
    settings.default_waiting_list = waiting_list

    view = settings.default_meeting_settings_view()

    expected_view = expected_default_meeting_options_view(settings)

    assert expected_view == view
