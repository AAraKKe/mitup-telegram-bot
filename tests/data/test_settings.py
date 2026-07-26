import logging

import pytest
from pydantic import ValidationError
from pytest import LogCaptureFixture

from mitup_bot.limits import MEETING_MAX_TIMEOUT_MINUTES
from mitup_bot.models import Settings, User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, SettingsMessages
from mitup_bot.views import MitupView
from mitup_bot.views.factory import options_button
from mitup_bot.views.meeting_settings import default_meeting_settings_view


def test_valid_timezone(settings: Settings):
    assert settings.tz.key == "Europe/Madrid"


def test_invalid_timezone(settings: Settings, caplog: LogCaptureFixture):
    settings.timezone = "Invalid/Timezone"
    with caplog.at_level(logging.WARNING):
        assert settings.tz.key == "UTC"
        assert "Invalid timezone" in caplog.records[0].message


@pytest.mark.parametrize(
    "timeout",
    [MEETING_MAX_TIMEOUT_MINUTES + 1, 99_999_999_999],
    ids=["just_above_cap", "far_above_cap"],
)
def test_timeout_above_cap_is_rejected_on_assignment(settings: Settings, timeout: int):
    """The cap belongs to the model, so it holds on any write path — an over-cap timeout keeps its
    owner's dated meetings active forever."""
    with pytest.raises(ValidationError):
        settings.timeout = timeout


def test_timeout_above_cap_is_rejected_on_construction():
    with pytest.raises(ValidationError):
        Settings(timeout=MEETING_MAX_TIMEOUT_MINUTES + 1)


def test_timeout_at_cap_is_accepted(settings: Settings):
    settings.timeout = MEETING_MAX_TIMEOUT_MINUTES

    assert settings.timeout == MEETING_MAX_TIMEOUT_MINUTES


def expected_default_meeting_options_view(settings: Settings) -> MitupView:
    lang = settings.language
    waiting_list = settings.default_waiting_list
    public = settings.default_public
    invitation = settings.default_allow_invitation
    incognito = settings.default_incognito
    lock_on_start = settings.default_lock_on_start

    message = SettingsMessages.DEFAULT_OPTIONS_DESCRIPTION.get(lang=lang)
    waiting_list_button = options_button(
        cb.SET_DEFAULT_WAITING_LIST, ButtonMessages.WAITING_LIST.get(lang=lang), waiting_list
    )
    public_button = options_button(cb.SET_DEFAULT_PUBLIC, ButtonMessages.PUBLIC.get(lang=lang), public)
    invitation_button = options_button(
        cb.SET_DEFAULT_INVITATIONS, ButtonMessages.OPEN_INVITATION.get(lang=lang), invitation
    )
    incognito_button = options_button(cb.SET_DEFAULT_INCOGNITO, ButtonMessages.INCOGNITO.get(lang=lang), incognito)
    # Row 3 is the lock toggle directly (no sub-screen navigation)
    lock_button = options_button(
        cb.SET_DEFAULT_LOCK_ON_START,
        ButtonMessages.LOCK_ON_START.get(lang=lang),
        lock_on_start,
    )

    return MitupView(
        message,
        keyboard=[
            [waiting_list_button, public_button],
            [invitation_button, incognito_button],
            [lock_button],
        ],
    ).with_back_button(text=ButtonMessages.SETTINGS, callback_data=cb.SETTINGS, lang=lang)


@pytest.mark.parametrize("waiting_list", [True, False], ids=["waiting_list_true", "waiting_list_false"])
@pytest.mark.parametrize("public", [True, False], ids=["public_true", "public_false"])
@pytest.mark.parametrize("invitation", [True, False], ids=["invitation_true", "invitation_false"])
@pytest.mark.parametrize("incognito", [True, False], ids=["incognito_true", "incognito_false"])
@pytest.mark.parametrize("lock_on_start", [True, False], ids=["lock_on_start_true", "lock_on_start_false"])
def test_default_meeting_options_view(
    waiting_list: bool,
    public: bool,
    invitation: bool,
    incognito: bool,
    lock_on_start: bool,
    user_with_settings: User,
):
    settings = user_with_settings.settings
    settings.default_allow_invitation = invitation
    settings.default_incognito = incognito
    settings.default_public = public
    settings.default_waiting_list = waiting_list
    settings.default_lock_on_start = lock_on_start

    view = default_meeting_settings_view(settings)

    expected_view = expected_default_meeting_options_view(settings)

    assert expected_view == view
